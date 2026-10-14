"""Mask-engine quality report (Phase 0 gate tooling, Phase 2 scorecard).

Runs every case in tests/mask_quality_matrix.py through the mask engine
directly, prints a metrics table + summary, renders PNGs and a contact sheet
into scripts/output/quality/ (gitignored).

  --planner off|on|both   which engine mode(s) to run (default off). "on"
                          sets settings.house_planner_enabled for the run and
                          builds one BuildingPlan per case (like
                          generate_house.py does, via
                          floor_layout_mask.layout_building). PNGs for "on" go to
                          scripts/output/quality/planner_on/.
  --scorecard             also print the honest absolute-target scorecard
                          (app/pipeline/mask_scorecard.py) and write it to
                          scripts/output/quality/scorecard_<mode>.txt.
  --update-baseline       write tests/data/mask_quality_baseline.json (off)
                          or tests/data/mask_quality_baseline_planner.json
                          (on). Needs a single --planner mode.

Run:  python scripts/mask_quality_report.py [--planner on] [--scorecard] [--no-render]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import settings  # noqa: E402
from app.pipeline.floor_layout_mask import layout_building, layout_floor_with_warnings  # noqa: E402
from app.pipeline.layout_metrics import measure_floor  # noqa: E402
from tests.mask_quality_matrix import CASES, iter_floors  # noqa: E402

OUT_DIR = os.path.join(ROOT, "scripts", "output", "quality")
EXTRA: dict = {}
BASELINE_PATHS = {
    "off": os.path.join(ROOT, "tests", "data", "mask_quality_baseline.json"),
    "on": os.path.join(ROOT, "tests", "data", "mask_quality_baseline_planner.json"),
}


def compute_all(planner: bool = False) -> dict:
    """{key: (case, floor_number, rects, metrics, max_overrides)}; planner
    mode also fills EXTRA[key] = {"remainder", "path", "plan"}."""
    results = {}
    EXTRA.clear()
    previous = settings.house_planner_enabled
    settings.house_planner_enabled = planner
    try:
        built = {}
        if planner:
            for c in CASES:
                built[c["id"]] = layout_building(
                    c["floors"], c["plot"], c["facing"], c["garage_cars"], len(c["floors"])
                )
        for case, floor_n, rooms, key in iter_floors():
            total = len(case["floors"])
            overrides = None
            if planner:
                plan, floor_results = built[case["id"]]
                _used, rects, _w = floor_results[floor_n - 1]
                fplan = plan.for_floor(floor_n)
                overrides = dict(fplan.room_max) or None
                EXTRA[key] = {"remainder": list(fplan.remainder_rooms), "path": fplan.layout_path, "plan": plan}
            else:
                rects, _ = layout_floor_with_warnings(
                    rooms, case["plot"], garage_cars=case["garage_cars"], facing=case["facing"]
                )
            metrics = measure_floor(
                rects, case["plot"], total_floors=total, floor_number=floor_n,
                garage_cars=case["garage_cars"], max_overrides=overrides, facing=case["facing"],
            )
            results[key] = (case, floor_n, rects, metrics, overrides)
    finally:
        settings.house_planner_enabled = previous
    return results


def _render(results, out_dir):
    from PIL import Image, ImageDraw

    from app.pipeline.blueprint_svg import render_floor_blueprint

    os.makedirs(out_dir, exist_ok=True)
    thumbs = []
    for key, (case, floor_n, rects, _m, _o) in results.items():
        png = render_floor_blueprint(
            floor_n, rects, case["plot"], len(case["floors"]), case["facing"]
        )
        path = os.path.join(out_dir, key.replace("/", "_") + ".png")
        with open(path, "wb") as f:
            f.write(png)
        thumbs.append((key, path))
    cell, label_h, cols = 360, 22, 6
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + label_h)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (key, path) in enumerate(thumbs):
        im = Image.open(path).convert("RGB")
        im.thumbnail((cell - 8, cell - 8))
        x, y = (i % cols) * cell, (i // cols) * (cell + label_h)
        d.text((x + 4, y + 4), key, fill="black")
        sheet.paste(im, (x + 4, y + label_h))
    sheet.save(os.path.join(out_dir, "contact_sheet.png"))


def _print_table(results):
    print(f"{'case/floor':<36}{'cov%':>6} {'yards T/B/L/R':<20}{'hard':>5} {'kinds':<22}"
          f"{'dl_hard':<28}{'oversized':<26}{'inv':>4}{'parts':>6}")
    tot = {"hard": 0, "dl": 0, "over": 0, "inv": 0, "under": 0}
    for key, (_c, _f, _r, m, _o) in results.items():
        yards = "/".join(f"{m[k]:.0f}" for k in ("yard_top", "yard_bottom", "yard_left", "yard_right"))
        print(f"{key:<36}{m['coverage_pct']:>6.1f} {yards:<20}{m['hard_count']:>5} "
              f"{','.join(m['hard_kinds'])[:21]:<22}{','.join(m['daylight_fail_hard'])[:27]:<28}"
              f"{','.join(m['oversized'])[:25]:<26}{int(m['hierarchy_inverted']):>4}{m['max_parts']:>6}")
        tot["hard"] += m["hard_count"]
        tot["dl"] += len(m["daylight_fail_hard"])
        tot["over"] += len(m["oversized"])
        tot["inv"] += int(m["hierarchy_inverted"])
        tot["under"] += len(m["undersized"])
    print("\n=== SUMMARY ===")
    print(f"floor layouts: {len(results)}")
    print(f"hard violations: {tot['hard']}; daylight hard fails: {tot['dl']}; "
          f"oversized rooms: {tot['over']}; inverted hierarchies: {tot['inv']}; undersized: {tot['under']}")
    by_plot: dict[str, list[float]] = {}
    for _k, (c, _f, _r, m, _o) in results.items():
        p = c["plot"]
        by_plot.setdefault(f"{p['length']:.0f}x{p['width']:.0f}", []).append(m["coverage_pct"])
    for plot, vals in by_plot.items():
        print(f"coverage% {plot}: {', '.join(f'{v:.1f}' for v in vals)}")


def _scorecard(results, label, planner):
    from app.pipeline.mask_scorecard import capacity_summary, format_scorecard, score_floor

    scored = {}
    for key, (case, floor_n, rects, _m, overrides) in results.items():
        extra = EXTRA.get(key) or {}
        scored[key] = score_floor(
            rects, case["plot"], case["facing"], case["floors"], floor_n,
            case["garage_cars"], overrides,
            remainder_names=extra.get("remainder") if planner else None, planner=planner,
        )
    previous = settings.house_planner_enabled
    settings.house_planner_enabled = planner
    try:
        capacity = capacity_summary()
    finally:
        settings.house_planner_enabled = previous
    paths = {k: (EXTRA.get(k) or {}).get("path") for k in scored} if planner else None
    text = format_scorecard(scored, label, capacity, paths)
    if planner:
        text += "\n\n" + _built_area_table(results)
    print(text)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, f"scorecard_{label}.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    return text


def _built_area_table(results) -> str:
    """Built area (footprint x floors, plus the enclosed part = minus the
    porch-category open areas) per big-plot case, planner mode."""
    from app.pipeline.room_specs import classify_room_category

    lines = ["Built area (big plots): case | band/tier | floors | footprint | built (fp x floors) | "
             "open porch/terrace | enclosed built | cap | coverage %"]
    seen = set()
    for key, (case, _f, _r, _m, _o) in results.items():
        cid = case["id"]
        if cid in seen or not any(p in cid for p in ("150x150", "150x200", "300x300")):
            continue
        seen.add(cid)
        plan = EXTRA[key]["plan"]
        n = len(case["floors"])
        open_area = 0.0
        for fk, (c2, _f2, rects2, _m2, _o2) in results.items():
            if c2["id"] == cid:
                open_area += sum(
                    sum(p["w"] * p["h"] for p in (r.get("parts") or [r]))
                    for r in rects2 if classify_room_category(str(r.get("name") or "")) == "porch"
                )
        built = plan.footprint_area * n
        cap = f"{plan.built_area_cap:.0f}" if plan.built_area_cap else "-"
        cov = plan.footprint_area / (case["plot"]["length"] * case["plot"]["width"]) * 100
        lines.append(f"    {cid} | {plan.band}/{plan.tier} | {n} | {plan.footprint_area:.0f} | {built:.0f} | "
                     f"{open_area:.0f} | {built - open_area:.0f} | {cap} | {cov:.1f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--scorecard", action="store_true")
    ap.add_argument("--planner", choices=("off", "on", "both"), default="off")
    args = ap.parse_args()

    modes = ("off", "on") if args.planner == "both" else (args.planner,)
    if args.update_baseline and len(modes) != 1:
        ap.error("--update-baseline needs a single --planner mode")

    for mode in modes:
        planner = mode == "on"
        print(f"\n##### planner {mode.upper()} #####")
        results = compute_all(planner)
        _print_table(results)
        if args.scorecard:
            print()
            _scorecard(results, f"planner_{mode}", planner)
        if not args.no_render:
            out_dir = os.path.join(OUT_DIR, "planner_on") if planner else OUT_DIR
            _render(results, out_dir)
            print(f"\nPNGs + contact sheet -> {out_dir}")
        if args.update_baseline:
            path = BASELINE_PATHS[mode]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = {k: m for k, (_c, _f, _r, m, _o) in results.items()}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)
            print(f"baseline written -> {path}")


if __name__ == "__main__":
    main()
