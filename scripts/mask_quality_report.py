"""Mask-engine quality report (Phase 0 gate tooling).

Runs every case in tests/mask_quality_matrix.py through the mask engine
directly, prints a metrics table + summary, renders PNGs and a contact sheet
into scripts/output/quality/ (gitignored).

Run:  python scripts/mask_quality_report.py [--update-baseline] [--no-render]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.pipeline.floor_layout_mask import layout_floor_with_warnings  # noqa: E402
from app.pipeline.layout_metrics import measure_floor  # noqa: E402
from tests.mask_quality_matrix import CASES, iter_floors  # noqa: E402

OUT_DIR = os.path.join(ROOT, "scripts", "output", "quality")
BASELINE_PATH = os.path.join(ROOT, "tests", "data", "mask_quality_baseline.json")


def compute_all() -> dict:
    """{key: (case, floor_number, rects, metrics)}"""
    results = {}
    for case, floor_n, rooms, key in iter_floors():
        total = len(case["floors"])
        rects, _ = layout_floor_with_warnings(
            rooms, case["plot"], garage_cars=case["garage_cars"], facing=case["facing"]
        )
        metrics = measure_floor(
            rects, case["plot"], total_floors=total, floor_number=floor_n,
            garage_cars=case["garage_cars"],
        )
        results[key] = (case, floor_n, rects, metrics)
    return results


def _render(results):
    from PIL import Image, ImageDraw

    from app.pipeline.blueprint_svg import render_floor_blueprint

    os.makedirs(OUT_DIR, exist_ok=True)
    thumbs = []
    for key, (case, floor_n, rects, _m) in results.items():
        png = render_floor_blueprint(
            floor_n, rects, case["plot"], len(case["floors"]), case["facing"]
        )
        path = os.path.join(OUT_DIR, key.replace("/", "_") + ".png")
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
    sheet.save(os.path.join(OUT_DIR, "contact_sheet.png"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()

    results = compute_all()
    print(f"{'case/floor':<36}{'cov%':>6} {'yards T/B/L/R':<20}{'hard':>5} {'kinds':<22}"
          f"{'dl_hard':<28}{'oversized':<26}{'inv':>4}{'parts':>6}")
    tot = {"hard": 0, "dl": 0, "over": 0, "inv": 0, "under": 0}
    for key, (_c, _f, _r, m) in results.items():
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
    for _k, (c, _f, _r, m) in results.items():
        p = c["plot"]
        by_plot.setdefault(f"{p['length']:.0f}x{p['width']:.0f}", []).append(m["coverage_pct"])
    for plot, vals in by_plot.items():
        print(f"coverage% {plot}: {', '.join(f'{v:.1f}' for v in vals)}")

    if not args.no_render:
        _render(results)
        print(f"\nPNGs + contact sheet -> {OUT_DIR}")
    if args.update_baseline:
        os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
        data = {k: m for k, (_c, _f, _r, m) in results.items()}
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        print(f"baseline written -> {BASELINE_PATH}")


if __name__ == "__main__":
    main()
