"""READ-ONLY measurement: the RECT engine (floor_layout.py, slice-and-dice)
on the same 35-floor mask quality matrix, scored with the SAME
layout_metrics.measure_floor / mask_scorecard.score_floor machinery the mask
gate uses, side by side with the mask engine (planner off / on) at HEAD.

Purpose: the cheapest predictor of what an "explicit rectangles" packer
(future-plans/mask-explicit-rectangles-design-note.md) would score BEFORE any
mask-style bending. No source file is modified; one in-process monkeypatch
(mode rect_in_plan only, restored afterwards) disables the rect engine's own
footprint shrink so it tiles exactly the planner's footprint.

Modes
  rect_self     rect engine exactly as it runs in production: the raw matrix
                room list, the whole plot, rect sizes/anchors its OWN footprint
                (floor_layout._footprint_dimensions: front-flush, side-centred,
                no planner yards). Scored like the flag-off mask report
                (planner=False thresholds) - and, as an extra line, with the
                planner's tier-aware thresholds.
  rect_in_plan  "packer only" proxy: house_planner.plan_building (scale 1.0)
                gives the footprint, yards, added rooms (bonus / Verandah /
                Terrace) and per-room TARGETS; the rect engine tiles exactly
                that footprint box (weights = target - reserved minimum) and
                the result is placed at the plan's yards. Scored exactly like
                planner-on mask (planner=True, plan room_max overrides,
                remainder rooms excluded from oversized).
  mask_off      floor_layout_mask, house_planner_enabled=False (HEAD default).
  mask_on       floor_layout_mask + planner (layout_building, incl. the 5.2
                footprint-scale search), house_planner_enabled=True.

Run:  python scripts/rect_baseline_on_matrix.py [--jobs 6] [--skip-mask]
Output: scripts/output/quality/rect_baseline_on_matrix.txt (+ .json)
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from app.config import settings  # noqa: E402
from app.pipeline import floor_layout  # noqa: E402
from app.pipeline.house_planner import plan_building  # noqa: E402
from app.pipeline.layout_quality import SLIVER_ASPECT_RATIO, PROPORTION_TARGET_RATIO  # noqa: E402
from app.pipeline.mask_scorecard import (  # noqa: E402
    SCORED_CHECKS,
    failure_events,
    score_floor,
    summary_counts,
    violation_histogram,
    top_blockers,
    greedy_blockers,
)
from app.pipeline.room_specs import (  # noqa: E402
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    min_area_for_room,
    to_plot_unit,
)
from tests.mask_quality_matrix import CASES, iter_floors  # noqa: E402

OUT_DIR = os.path.join(ROOT, "scripts", "output", "quality")


def _parts(r):
    ps = r.get("parts")
    if ps:
        return [p for p in ps if p.get("w", 0) > 0 and p.get("h", 0) > 0]
    return [{"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}]


# ---------------------------------------------------------------- rect modes


def rect_self():
    out, secs = {}, {}
    for case, i, rooms, key in iter_floors():
        t0 = time.perf_counter()
        rects, _w = floor_layout.layout_floor_with_warnings(
            rooms, case["plot"], garage_cars=case["garage_cars"], facing=case["facing"]
        )
        secs[key] = time.perf_counter() - t0
        out[key] = {"rects": rects, "overrides": None, "remainder": None}
    return out, secs


def rect_in_plan():
    original = floor_layout._footprint_dimensions
    floor_layout._footprint_dimensions = lambda rooms, nl, nw, unit, gc, facing="north": (nl, nw)
    out, secs = {}, {}
    plans = {}
    try:
        for case in CASES:
            t0 = time.perf_counter()
            plan = plan_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], len(case["floors"]))
            plans[case["id"]] = plan
            plan_secs = time.perf_counter() - t0
            unit = case["plot"].get("unit") or "ft"
            ext = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
            for i, rooms in enumerate(case["floors"], start=1):
                key = f"{case['id']}/floor{i}"
                t0 = time.perf_counter()
                fp = plan.for_floor(i)
                names = {str(r.get("name") or "") for r in rooms}
                used = list(rooms) + [dict(a) for a in fp.added_rooms if a["name"] not in names]
                weighted = []
                for r in used:
                    n = str(r.get("name") or "")
                    tgt = fp.room_targets.get(n)
                    reserved = min_area_for_room(n, unit, case["garage_cars"]) * 1.08
                    w = max((tgt - reserved) if tgt is not None else float(r.get("area") or 1), 0.01)
                    weighted.append({**r, "area": w})
                dims = {"length": plan.footprint_length + 2 * ext, "width": plan.footprint_width + 2 * ext, "unit": unit}
                rects, _w = floor_layout.layout_floor_with_warnings(
                    weighted, dims, garage_cars=case["garage_cars"], facing=case["facing"]
                )
                dx, dy = plan.yards.get("left", 0.0) - ext, plan.yards.get("top", 0.0) - ext
                rects = [{**r, "x": r["x"] + dx, "y": r["y"] + dy} for r in rects]
                secs[key] = time.perf_counter() - t0 + (plan_secs if i == 1 else 0.0)
                out[key] = {"rects": rects, "overrides": dict(fp.room_max) or None,
                            "remainder": list(fp.remainder_rooms)}
    finally:
        floor_layout._footprint_dimensions = original
    return out, secs, plans


# ---------------------------------------------------------------- mask modes


def mask_mode(planner: bool, jobs: int):
    import mask_quality_report as mqr

    t0 = time.perf_counter()
    res = mqr.compute_all(planner, jobs)
    wall = time.perf_counter() - t0
    out = {}
    for key, (_case, _f, rects, _m, overrides) in res.items():
        extra = mqr.EXTRA.get(key) or {}
        out[key] = {"rects": rects, "overrides": overrides,
                    "remainder": extra.get("remainder") if planner else None, "path": extra.get("path")}
    return out, wall


# ---------------------------------------------------------------- scoring


def score_all(layouts: dict, planner_thresholds: bool) -> dict:
    scored = {}
    for case, i, _rooms, key in iter_floors():
        lay = layouts[key]
        scored[key] = score_floor(
            lay["rects"], case["plot"], case["facing"], case["floors"], i, case["garage_cars"],
            lay["overrides"], remainder_names=lay["remainder"] if planner_thresholds else None,
            planner=planner_thresholds,
        )
    return scored


def shape_stats(layouts: dict) -> dict:
    """Bowling-alley proxies on the largest part of every non-linear room."""
    sliver, elong, rooms_n = [], [], 0
    for key, lay in layouts.items():
        for r in lay["rects"]:
            name = str(r.get("name") or "")
            cat = classify_room_category(name)
            if cat in ("hallway", "staircase", "garage", "porch"):
                continue
            ps = _parts(r)
            big = max(ps, key=lambda p: p["w"] * p["h"])
            a = max(big["w"], big["h"]) / max(min(big["w"], big["h"]), 1e-6)
            rooms_n += 1
            if a > SLIVER_ASPECT_RATIO:
                sliver.append(f"{key}:{name}({a:.1f})")
            elif a > PROPORTION_TARGET_RATIO:
                elong.append(f"{key}:{name}({a:.1f})")
    return {"rooms": rooms_n, "sliver": sliver, "elongated": elong}


def summarize(label: str, scored: dict, layouts: dict, seconds_total: float, seconds_note: str) -> dict:
    s = summary_counts(scored)
    hist = violation_histogram(scored)
    hard_kinds: dict[str, int] = {}
    for r in scored.values():
        for kind, _room in r["metrics"]["hard_detail"]:
            hard_kinds[kind] = hard_kinds.get(kind, 0) + 1
    dl_hard = sum(len(r["metrics"]["daylight_fail_hard"]) for r in scored.values())
    dl_soft = sum(len(r["metrics"]["daylight_fail_soft"]) for r in scored.values())
    dl_floors = sum(1 for r in scored.values() if r["metrics"]["daylight_fail_hard"])
    dl_rooms = [f"{k}:{','.join(r['metrics']['daylight_fail_hard'])}" for k, r in scored.items() if r["metrics"]["daylight_fail_hard"]]
    zero_hard = sum(1 for r in scored.values() if r["metrics"]["hard_count"] == 0 and not r["metrics"]["daylight_fail_hard"])
    unr = sum(len(r["circulation"].get("unreachable") or []) for r in scored.values())
    pt = sum(len(r["circulation"].get("pass_through_private") or []) for r in scored.values())
    eg = sum(1 for r in scored.values() if r["circulation"].get("entry_is_garage"))
    em = sum(1 for r in scored.values() if r["circulation"].get("entry_missing"))
    aspect_fail = sum(len(r["metrics"]["bedroom_aspect_fail"]) for r in scored.values())
    narrow_bed = sum(len(r["metrics"]["narrow_bedrooms"]) for r in scored.values())
    sh = shape_stats(layouts)
    # Floors whose ONLY failure types are daylight (and/or the footprint-policy
    # checks coverage/yards, which a packer inside the planner footprint
    # inherits from the planner, not from packing).
    only_dl, only_dl_or_fp = [], []
    for k, ks in hist["kind_sets"].items():
        if ks and ks <= {"no_daylight"}:
            only_dl.append(k)
        if ks and ks <= {"no_daylight", "coverage", "yards"}:
            only_dl_or_fp.append(k)
    types = {t: {"total": v["total"], "floors": len(v["floors"]), "only": len(v["only_floors"]), "hard": v["hard"]}
             for t, v in hist["types"].items()}
    return {
        "label": label, "summary": s, "hard_kinds": hard_kinds, "types": types,
        "top3": top_blockers(hist), "greedy": greedy_blockers(hist),
        "daylight_hard_rooms": dl_hard, "daylight_soft_rooms": dl_soft, "daylight_floors": dl_floors,
        "daylight_list": dl_rooms, "zero_hard_floors": zero_hard,
        "unreachable": unr, "pass_through": pt, "entry_is_garage": eg, "entry_missing": em,
        "bedroom_aspect_fail": aspect_fail, "narrow_bedrooms": narrow_bed,
        "sliver_rooms": len(sh["sliver"]), "elongated_rooms": len(sh["elongated"]), "rooms_scored": sh["rooms"],
        "sliver_list": sh["sliver"],
        "only_daylight_floors": only_dl, "only_daylight_or_footprint_floors": only_dl_or_fp,
        "clean_floors": [k for k, r in scored.items() if r["clean_all"]],
        "seconds": round(seconds_total, 1), "seconds_note": seconds_note,
        "per_floor": {k: {"clean": r["clean_all"], "fails": sorted({e["type"] for e in failure_events(r)}),
                          "cov": r["coverage"]["coverage_pct"], "hard": r["metrics"]["hard_count"],
                          "dl": r["metrics"]["daylight_fail_hard"]} for k, r in scored.items()},
    }


def fmt_table(rows: list[dict]) -> str:
    n = rows[0]["summary"]["floors"]
    cols = [r["label"] for r in rows]
    w = 16
    lines = [f"{'metric':<46}" + "".join(f"{c:>{w}}" for c in cols)]

    def line(title, fn):
        lines.append(f"{title[:45]:<46}" + "".join(f"{str(fn(r)):>{w}}" for r in rows))

    line("FULLY CLEAN floors (clean_all, all 9)", lambda r: f"{r['summary']['clean_all']}/{n}")
    line("zero-hard floors (hard 0 and no daylight)", lambda r: f"{r['zero_hard_floors']}/{n}")
    for c in SCORED_CHECKS:
        line(f"  check {c}", lambda r, c=c: f"{r['summary'][c]}/{n}")
    line("  check circulation", lambda r: f"{r['summary']['circulation']}/{n}")
    line("all-9 floor-checks", lambda r: f"{r['summary']['all9']}/{n * 9}")
    line("legacy-6 floor-checks", lambda r: f"{r['summary']['legacy']}/{n * 6}")
    line("hard violations (measure_floor hard_detail)", lambda r: sum(r["hard_kinds"].values()))
    kinds = sorted({k for r in rows for k in r["hard_kinds"]})
    for k in kinds:
        line(f"  hard {k}", lambda r, k=k: r["hard_kinds"].get(k, 0))
    line("daylight HARD rooms (bed/living/kitchen)", lambda r: r["daylight_hard_rooms"])
    line("daylight HARD floors", lambda r: r["daylight_floors"])
    line("daylight SOFT rooms (dining/study/family)", lambda r: r["daylight_soft_rooms"])
    line("bedroom aspect > 2.5 (rooms)", lambda r: r["bedroom_aspect_fail"])
    line("narrow bedrooms (aspect or hard narrow)", lambda r: r["narrow_bedrooms"])
    line(f"rooms largest-part aspect > {SLIVER_ASPECT_RATIO}", lambda r: f"{r['sliver_rooms']}/{r['rooms_scored']}")
    line(f"rooms aspect {PROPORTION_TARGET_RATIO}..{SLIVER_ASPECT_RATIO}", lambda r: f"{r['elongated_rooms']}/{r['rooms_scored']}")
    line("unreachable rooms", lambda r: r["unreachable"])
    line("pass-through private rooms", lambda r: r["pass_through"])
    line("entry-is-garage floors", lambda r: r["entry_is_garage"])
    line("entry-missing floors", lambda r: r["entry_missing"])
    line("floors failing ONLY daylight", lambda r: len(r["only_daylight_floors"]))
    line("floors failing only daylight/coverage/yards", lambda r: len(r["only_daylight_or_footprint_floors"]))
    line("runtime s (see notes)", lambda r: r["seconds"])
    return "\n".join(lines)


def fmt_hist(r: dict) -> str:
    lines = [f"--- failure-type histogram [{r['label']}] (non-clean floors; HARD and CHECK) ---",
             f"{'type':<22}{'class':<7}{'violations':>11}{'floors':>8}{'only-this':>10}"]
    for t, v in sorted(r["types"].items(), key=lambda kv: (-kv[1]["floors"], -kv[1]["total"], kv[0])):
        lines.append(f"{t:<22}{'HARD' if v['hard'] else 'CHECK':<7}{v['total']:>11}{v['floors']:>8}{v['only']:>10}")
    lines.append("top 3 blockers: " + ", ".join(r["top3"]))
    lines.append("greedy: " + "; ".join(f"+{t} -> {c}" for t, c in r["greedy"]))
    lines.append("clean floors: " + (", ".join(r["clean_floors"]) or "-"))
    lines.append("only-daylight floors: " + (", ".join(r["only_daylight_floors"]) or "-"))
    lines.append("only daylight/coverage/yards floors: " + (", ".join(r["only_daylight_or_footprint_floors"]) or "-"))
    lines.append("daylight hard rooms: " + ("; ".join(r["daylight_list"]) or "-"))
    lines.append("sliver rooms (>2.8): " + ("; ".join(r["sliver_list"]) or "-"))
    lines.append(f"runtime: {r['seconds']} s ({r['seconds_note']})")
    return "\n".join(lines)


def fmt_per_floor(rows: list[dict]) -> str:
    keys = list(rows[0]["per_floor"])
    lines = ["--- per floor: C = clean_all, else failure types (abbrev.) ---"]
    for k in keys:
        cells = []
        for r in rows:
            pf = r["per_floor"][k]
            cells.append("C" if pf["clean"] else ",".join(pf["fails"]))
        lines.append(f"{k:<34} | " + " | ".join(f"{r['label']}: {c}" for r, c in zip(rows, cells)))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--skip-mask", action="store_true")
    args = ap.parse_args()

    previous = (settings.house_planner_enabled, settings.house_layout_engine)
    rows, extra_rows = [], []
    try:
        settings.house_planner_enabled = False
        lay, secs = rect_self()
        rows.append(summarize("rect_self", score_all(lay, False), lay, sum(secs.values()),
                              f"single process, sum over 35 floors; max floor {max(secs.values()):.2f}s"))
        extra_rows.append(summarize("rect_self_tier", score_all(lay, True), lay, sum(secs.values()), "same layouts, tier thresholds"))

        lay2, secs2, _plans = rect_in_plan()
        rows.append(summarize("rect_in_plan", score_all(lay2, True), lay2, sum(secs2.values()),
                              f"single process, plan_building + rect per floor; max floor {max(secs2.values()):.2f}s"))

        if not args.skip_mask:
            settings.house_planner_enabled = False
            lay3, wall3 = mask_mode(False, args.jobs)
            rows.append(summarize("mask_off", score_all(lay3, False), lay3, wall3, f"WALL clock, --jobs {args.jobs}"))
            settings.house_planner_enabled = True
            lay4, wall4 = mask_mode(True, args.jobs)
            settings.house_planner_enabled = True
            rows.append(summarize("mask_on", score_all(lay4, True), lay4, wall4, f"WALL clock, --jobs {args.jobs}"))
    finally:
        settings.house_planner_enabled, settings.house_layout_engine = previous

    text = ["=== RECT vs MASK on the 35-floor mask quality matrix (read-only) ===",
            "rect_self = rect engine, own footprint, raw program, standard thresholds (planner=False)",
            "rect_in_plan = rect packer inside the planner footprint/targets (scale 1.0), planner thresholds",
            "mask_off / mask_on = mask engine at HEAD, planner flag off / on (mask_on incl. 5.2 scale search)",
            "", fmt_table(rows), "",
            "extra: rect_self scored with tier-aware thresholds (planner=True, no overrides):",
            fmt_table(extra_rows), ""]
    for r in rows:
        text += [fmt_hist(r), ""]
    text.append(fmt_per_floor(rows))
    out = "\n".join(text)
    print(out)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "rect_baseline_on_matrix.txt"), "w", encoding="utf-8") as f:
        f.write(out + "\n")
    with open(os.path.join(OUT_DIR, "rect_baseline_on_matrix.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "extra": extra_rows}, f, indent=1, default=str)


if __name__ == "__main__":
    main()
