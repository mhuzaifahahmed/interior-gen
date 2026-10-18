"""End-to-end Build a House cases through the REAL run_house_pipeline (fake
provider, temp sqlite DB, in-memory storage), mask engine + planner ON.

  python scripts/e2e_house_cases.py [case_tag ...]

Saves per-floor blueprint PNGs to scripts/output/quality/e2e/ (gitignored) and
prints, per case: wall time, feasibility verdict, hard warnings, and per-room
areas with any room over its max (room_specs.max_area_for_room, a coarse
reference - the planner uses tier-based maxes that can be larger).
"""
import copy
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = Path(ROOT) / "scripts" / "output" / "quality" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import HouseProject  # noqa: E402
from app.pipeline import generate_house as gh  # noqa: E402
from app.pipeline.room_specs import max_area_for_room  # noqa: E402

GROUND = ["Entry / Foyer", "Living Room", "Dining Room", "Kitchen"]
# (tag, length, width, floors, beds/floor, baths/floor, upper-floor extra rooms)
CASES = [
    ("40x60_1f_3b2b", 40, 60, 1, 3, 2, []),
    ("50x70_3f_2b1b_lounge", 50, 70, 3, 2, 1, ["Family Lounge"]),
    ("100x150_2f_6b4b", 100, 150, 2, 6, 4, []),
    ("100x150_4f_3b2b", 100, 150, 4, 3, 2, []),
    ("150x200_2f_3b2b", 150, 200, 2, 3, 2, []),
    ("56x56_1f_3b2b", 56, 56, 1, 3, 2, []),
    ("150x150_1f_3b2b", 150, 150, 1, 3, 2, []),
    ("300x300_1f_3b2b", 300, 300, 1, 3, 2, []),
]


def room_names(floor, beds, baths, extra):
    names = (GROUND if floor == 1 else list(extra)) + [f"Bedroom {i}" for i in range(1, beds + 1)]
    return names + [f"Bathroom {i}" for i in range(1, baths + 1)]


def weight(n):
    return {"Living Room": 4, "Dining Room": 3, "Kitchen": 3, "Entry / Foyer": 1}.get(n, 2 if n.startswith("Bed") else 1)


class FakeProvider:
    def __init__(self, floors, beds, baths, extra):
        self.f, self.b, self.t, self.x = floors, beds, baths, extra

    def analyze_plot(self, *a, **k):
        return None

    def generate_floor_plan(self, *a, **k):
        return None

    def generate_house_render(self, *a, **k):
        return b"x"

    def generate_room_layout(self, *a, **k):
        return {"floors": [
            {"floor_number": i, "rooms": [{"name": n, "area": weight(n)} for n in room_names(i, self.b, self.t, self.x)]}
            for i in range(1, self.f + 1)
        ]}


class MemStorage:
    def __init__(self):
        self.o = {}

    def put(self, k, d, content_type=None):
        self.o[k] = d

    def get(self, k):
        return self.o[k]

    def url(self, k):
        return k


def room_area(r):
    parts = r.get("parts")
    if parts:
        return sum(p["w"] * p["h"] for p in parts)
    return r["w"] * r["h"]


def run_case(tag, L, W, floors, beds, baths, extra):
    captured = []
    orig = gh.render_floor_blueprint

    def spy(fn, rects, dims, total, facing=None, *a, **k):
        captured.append((fn, copy.deepcopy(rects)))
        return orig(fn, rects, dims, total, facing, *a, **k)

    gh.render_floor_blueprint = spy
    warns = []
    orig_lb = gh.layout_building

    def spy_lb(*a, **k):
        plan, per_floor = orig_lb(*a, **k)
        warns[:] = [[w for w in ws] for (_r, _rects, ws) in per_floor]
        return plan, per_floor

    gh.layout_building = spy_lb
    db = Path(tempfile.gettempdir()) / f"e2e_{uuid.uuid4().hex}.db"
    eng = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(eng)
    gh.engine = eng
    pid = uuid.uuid4().hex[:8]
    with Session(eng) as s:
        s.add(HouseProject(id=pid, status="queued"))
        s.commit()
    st = MemStorage()
    prompt = f"{floors} floors. " + " ".join(f"Floor {i}: {beds} bedrooms, {baths} bathrooms." for i in range(1, floors + 1))
    t0 = time.perf_counter()
    try:
        gh.run_house_pipeline(
            pid, FakeProvider(floors, beds, baths, extra), st, {"length": L, "width": W, "unit": "ft"}, prompt=prompt,
            floor_count=floors, facing_input=None, floor_bedrooms=[beds] * floors, floor_bathrooms=[baths] * floors,
        )
    finally:
        gh.render_floor_blueprint = orig
        gh.layout_building = orig_lb
    dt = time.perf_counter() - t0
    with Session(eng) as s:
        hp = s.get(HouseProject, pid)
        feas = json.loads(hp.feasibility_json or "null")
        status, err = hp.status, hp.error
    for k, v in st.o.items():
        if k.endswith(".png"):
            (OUT / f"{tag}_{k.rsplit('/', 1)[-1]}").write_bytes(v)
    print(f"\n=== {tag}  {L}x{W} ft  {floors}F  {beds}bed/{baths}bath  time={dt:.1f}s  status={status} err={err}")
    print("feasibility:", (feas or {}).get("verdict"), "-", ((feas or {}).get("explanation") or "")[:160])
    for fn, rects in captured:
        over = []
        areas = []
        for r in rects:
            a = room_area(r)
            mx = max_area_for_room(r["name"], "ft")
            areas.append(f"{r['name']}={a:.0f}")
            if a > mx * 1.001 and not any(k in r['name'].lower() for k in ('hallway', 'stair')):
                over.append(f"{r['name']} {a:.0f}>{mx:.0f}")
        hard = [f"{w.room}:{w.kind}" for w in (warns[fn - 1] if fn - 1 < len(warns) else []) if w.hard]
        print(f" floor {fn}: hard={hard or 'none'}")
        print(f"   over max: {over or 'none'}")
        print("   areas: " + ", ".join(areas))
    eng.dispose()
    try:
        db.unlink()
    except OSError:
        pass


def main():
    settings.house_planner_enabled = True
    settings.house_layout_engine = "mask"
    settings.autocad_generation_enabled = False
    settings.house_render_enabled = False
    wanted = set(sys.argv[1:])
    for case in CASES:
        if not wanted or case[0] in wanted:
            run_case(*case)


if __name__ == "__main__":
    main()
