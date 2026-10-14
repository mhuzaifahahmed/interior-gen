"""Door-logic regression snapshot (Phase 4a pure-refactor guard).

For EVERY floor of the quality matrix (tests/mask_quality_matrix.py), in BOTH
planner modes, computes hashes of the real outputs of the four door-consuming
renderers plus a raw dump of the door decisions:

  png        sha256 of blueprint_svg.render_floor_blueprint() bytes
  dxf        sha256 of a CANONICAL dump of the parsed DXF (entity type, layer,
             geometry rounded to 4 dp, sorted) - raw DXF bytes embed GUIDs /
             timestamps so are not hashed directly
  door_specs sha256 of json(kaggle_autocad._door_specs_for_floor())
  cond       sha256 of conditioning_image.render_conditioning_edge_map() bytes
  decisions  the (room_a, room_b, edge) list the PNG renderer's door loop
             decides to draw, plus the front-door opening (kept verbatim for
             diagnosing a mismatch; also compared)

  python scripts/door_refactor_snapshot.py --write  out.json
  python scripts/door_refactor_snapshot.py --compare out.json

Reusable: run --write before touching door logic, --compare after.
The decision dump deliberately re-implements the PNG door loop using the
blueprint_svg door helpers (kept importable), so it is independent of any
shared build function.
"""
import argparse
import hashlib
import io
import json
import os
import sys
from itertools import combinations

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import ezdxf  # noqa: E402

from app.config import settings  # noqa: E402
from app.pipeline import blueprint_svg as svg  # noqa: E402
from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf  # noqa: E402
from app.pipeline.conditioning_image import render_conditioning_edge_map  # noqa: E402
from app.pipeline.floor_layout import footprint_bbox  # noqa: E402
from app.pipeline.room_specs import classify_room_category  # noqa: E402
from app.providers.kaggle_autocad import _door_specs_for_floor  # noqa: E402
from mask_quality_report import compute_all  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "scripts", "output", "quality", "door_snapshot.json")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _r(v):
    return round(float(v), 4)


def _canon_entity(e) -> str:
    t = e.dxftype()
    layer = e.dxf.layer
    parts = [t, layer]
    if t == "LWPOLYLINE":
        parts.append("pts=" + repr([(_r(x), _r(y)) for x, y, *_ in e.get_points()]))
        parts.append(f"cw={_r(e.dxf.const_width)}")
        parts.append(f"closed={e.closed}")
    elif t == "LINE":
        parts.append(repr((_r(e.dxf.start.x), _r(e.dxf.start.y), _r(e.dxf.end.x), _r(e.dxf.end.y))))
    elif t == "ARC":
        parts.append(repr((_r(e.dxf.center.x), _r(e.dxf.center.y), _r(e.dxf.radius),
                           _r(e.dxf.start_angle), _r(e.dxf.end_angle))))
    elif t == "CIRCLE":
        parts.append(repr((_r(e.dxf.center.x), _r(e.dxf.center.y), _r(e.dxf.radius))))
    elif t in ("TEXT",):
        p = e.dxf.insert
        parts.append(repr((e.dxf.text, _r(p.x), _r(p.y), _r(e.dxf.height))))
    elif t == "MTEXT":
        p = e.dxf.insert
        parts.append(repr((e.text, _r(p.x), _r(p.y))))
    else:
        parts.append(repr(sorted((k, str(v)) for k, v in e.dxf.all_existing_dxf_attribs().items()
                                 if k not in ("handle", "owner"))))
    return "|".join(parts)


def canonical_dxf(dxf_bytes: bytes) -> list[str]:
    doc = ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8")))
    return sorted(_canon_entity(e) for e in doc.modelspace())


def door_decisions(rects, dimensions, facing, floor_number) -> dict:
    """The PNG renderer's door set, re-derived here via blueprint_svg's helpers."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    has_hallway = any(r["name"] == "Hallway" for r in rects)
    has_entry = any(classify_room_category(r["name"]) == "foyer" for r in rects)
    hallway_adjacent = svg._compute_hallway_adjacent(rects)
    interior = []
    for a, b in combinations(rects, 2):
        edge = svg._shared_edge_between_rooms(a, b)
        if edge is None:
            continue
        orphans = svg.orphaned_ensuites(rects) if (has_hallway and hasattr(svg, "orphaned_ensuites")) else None
        if has_hallway and svg._should_suppress_direct_door(a, b, hallway_adjacent, *( [orphans] if orphans is not None else [])):
            continue
        if has_entry and svg._should_suppress_garage_direct_door(a, b):
            continue
        # Phase 4b rule (a no-op on the Phase 4a "before" snapshot's inputs
        # only where no bathroom touches a living/dining/kitchen/family room).
        if hasattr(svg, "_should_suppress_bathroom_public_door") and svg._should_suppress_bathroom_public_door(a, b):
            continue
        interior.append([a["name"], b["name"], {k: (_r(v) if isinstance(v, float) else v) for k, v in edge.items()}])
    fp = footprint_bbox(rects) or (0.0, 0.0, length, width)
    f = (facing or "").strip().lower()
    front = svg._front_door_opening(rects, f, fp) if floor_number == 1 else None
    if front:
        front = {k: (_r(v) if isinstance(v, float) else v) for k, v in front.items()}
    return {"interior": interior, "front": front}


def compute_snapshot() -> dict:
    snap = {}
    previous = settings.house_planner_enabled
    try:
        for planner in (False, True):
            results = compute_all(planner)
            mode = "on" if planner else "off"
            for key, (case, floor_n, rects, _m, _o) in results.items():
                plot = case["plot"]
                total = len(case["floors"])
                facing = case["facing"]
                png = svg.render_floor_blueprint(floor_n, rects, plot, total, facing)
                dxf = render_floor_blueprint_dxf(floor_n, rects, plot, facing)
                specs = _door_specs_for_floor(rects, plot, facing, floor_n)
                cond = render_conditioning_edge_map(rects, plot, facing=facing, floor_number=floor_n)
                dec = door_decisions(rects, plot, facing, floor_n)
                snap[f"{mode}:{key}"] = {
                    "png": _sha(png),
                    "dxf": _sha("\n".join(canonical_dxf(dxf)).encode()),
                    "door_specs": _sha(json.dumps(specs, sort_keys=True, default=str).encode()),
                    "cond": _sha(cond),
                    "decisions": dec,
                }
    finally:
        settings.house_planner_enabled = previous
    return snap


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", metavar="OUT.json")
    g.add_argument("--compare", metavar="BASE.json")
    args = ap.parse_args()

    snap = compute_snapshot()
    if args.write:
        os.makedirs(os.path.dirname(os.path.abspath(args.write)), exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=1, sort_keys=True)
        print(f"wrote {len(snap)} floors -> {args.write}")
        return 0

    with open(args.compare, encoding="utf-8") as f:
        base = json.load(f)
    mismatches = 0
    compared = 0
    for key in sorted(set(base) | set(snap)):
        if key not in base or key not in snap:
            print(f"MISSING {key}: in base={key in base} in new={key in snap}")
            mismatches += 1
            continue
        for art in ("png", "dxf", "door_specs", "cond", "decisions"):
            compared += 1
            if base[key][art] != snap[key][art]:
                mismatches += 1
                print(f"MISMATCH {key} [{art}]")
    print(f"floors compared: {len(base)}; artifact comparisons: {compared}; mismatches: {mismatches}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
