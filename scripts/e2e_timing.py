"""End-to-end generation time for one real 3-floor dense request (user-facing
latency of the deterministic part of Build a House), separate from test time.

Runs the REAL `run_house_pipeline` (room-layout post-processing, feasibility,
mask layout per floor, blueprint PNG + DXF per floor) against a fake provider
(no Gemini/Kaggle/OpenAI calls - those remote calls cannot be timed offline and
are NOT included), a temp SQLite file DB and an in-memory storage.

  python scripts/e2e_timing.py [--runs 3]

Reports, per plot x planner flag: the first (cold) run and the median of the
remaining runs, in seconds. Planner flag OFF is the live default today.
"""
import argparse
import os
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.models import HouseProject  # noqa: E402
from app.pipeline import generate_house as gh  # noqa: E402

_DENSE_FLOORS = [
    {"floor_number": 1, "rooms": [
        {"name": n, "area": a} for n, a in [
            ("Entry", 1), ("Living Room", 4), ("Dining Room", 3), ("Kitchen", 3), ("Laundry", 1),
            ("Master Bedroom", 3), ("Master Bathroom", 1), ("Bedroom 2", 2), ("Bathroom 2", 1),
        ]]},
    {"floor_number": 2, "rooms": [
        {"name": n, "area": a} for n, a in [
            ("Master Bedroom", 3), ("Master Bathroom", 1), ("Bedroom 3", 2), ("Bathroom 3", 1),
            ("Bedroom 4", 2), ("Bathroom 4", 1), ("Family Lounge", 3),
        ]]},
    {"floor_number": 3, "rooms": [
        {"name": n, "area": a} for n, a in [
            ("Bedroom 5", 2), ("Bathroom 5", 1), ("Bedroom 6", 2), ("Bathroom 6", 1),
            ("Study", 2), ("Sitting Area", 3),
        ]]},
]


class _Provider:
    def analyze_plot(self, *a, **k):
        return None

    def generate_floor_plan(self, *a, **k):
        return None

    def generate_room_layout(self, dimensions, prompt, *a, **k):
        import copy
        return {"floors": copy.deepcopy(_DENSE_FLOORS)}

    def generate_house_render(self, *a, **k):
        return b"x"


class _Storage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type="image/png"):
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]

    def url(self, key):
        return f"/media/{key}"


def _one_run(plot, planner):
    db = Path(tempfile.gettempdir()) / f"e2e_timing_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False, "timeout": 30})
    SQLModel.metadata.create_all(engine)
    gh.engine = engine
    pid = uuid.uuid4().hex[:8]
    with Session(engine) as s:
        s.add(HouseProject(id=pid, status="queued"))
        s.commit()
    settings.house_planner_enabled = planner
    settings.house_layout_engine = "mask"
    settings.autocad_generation_enabled = False
    settings.house_render_enabled = False
    t0 = time.perf_counter()
    gh.run_house_pipeline(pid, _Provider(), _Storage(), plot, prompt="3 floors, 6 bedrooms, garage")
    dt = time.perf_counter() - t0
    with Session(engine) as s:
        hp = s.get(HouseProject, pid)
        status = (hp.status, hp.blueprint_status)
    try:
        engine.dispose()
        db.unlink(missing_ok=True)
    except Exception:
        pass
    return dt, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    rows = []
    for plot in ({"length": 60, "width": 80, "unit": "ft"}, {"length": 150, "width": 150, "unit": "ft"}):
        for planner in (False, True):
            times, status = [], None
            for _ in range(args.runs):
                dt, status = _one_run(plot, planner)
                times.append(dt)
            rest = times[1:] or times
            rows.append((f"{plot['length']}x{plot['width']}", "planner ON" if planner else "planner OFF",
                         times[0], statistics.median(rest), min(times), max(times), status))
    print(f"{'plot':9} {'mode':12} {'cold(s)':>8} {'median(s)':>10} {'min':>6} {'max':>6}  status")
    for r in rows:
        print(f"{r[0]:9} {r[1]:12} {r[2]:8.2f} {r[3]:10.2f} {r[4]:6.2f} {r[5]:6.2f}  {r[6]}")


if __name__ == "__main__":
    main()
