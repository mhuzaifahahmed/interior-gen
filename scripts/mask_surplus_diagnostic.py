"""Mask planner SURPLUS diagnostic (READ-ONLY; no source edits).

Question: do the planner (house_planner) and the mask engine
(floor_layout_mask) disagree on how much area each room gets, and where
does the difference go?

For every matrix floor (tests/mask_quality_matrix.py) with the planner ON,
runs the REAL engine path (floor_layout_mask.layout_building, exactly like
scripts/mask_quality_report.py) twice:
  (a) baseline           - house_planner.STAIR_FIXED_BLOCK as committed (False)
  (b) stair_block        - house_planner.STAIR_FIXED_BLOCK forced True at runtime
and instruments the engine (module-attribute wrappers only - restored after)
to recover, for the WINNING candidate of every floor, a per-room area ledger:

  plan_target  planner FloorPlan.room_targets[name]
  quant        round(plan_target / cell) * cell - plan_target  (grid rounding)
  fit          engine target after _fit_cells (zone rescale) - quantised plan target
  grow         cells after _grow_rooms - engine target
  fill         _fill_leftovers_bounded
  rebal        _rebalance_over_max
  ensure       _ensure_minimum_presence
  shape        _rectify_rooms / _reconnect_rooms
  arms         _carve_access_arms (circulation-repair candidates)
  final        actual drawn area; delta = final - plan_target

plus hallway (spine) area at each stage, the planner's circulation estimate,
and surplus = footprint - sum(plan targets) - actual hallway (this is exactly
the sum of every room's delta, since rooms + hallway tile the footprint).

  python scripts/mask_surplus_diagnostic.py [--root <checkout>] [--cases a,b]
Output: scripts/output/quality/surplus_diagnostic.txt (+ .json)
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "scripts", "output", "quality")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=HERE)
    ap.add_argument("--cases", default="")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "surplus_diagnostic.txt"))
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.root))

    from app.config import settings
    from app.pipeline import floor_layout_mask as M
    from app.pipeline import house_planner as HP
    from app.pipeline import planning_rules as rules
    from app.pipeline.door_graph import shared_edge_between_rooms
    from app.pipeline.floor_layout import room_parts
    from app.pipeline.room_specs import classify_room_category, min_area_for_room
    from tests.mask_quality_matrix import CASES

    only = {c for c in args.cases.split(",") if c}
    cases = [c for c in CASES if not only or c["id"] in only]

    # ------------------------------------------------------------ instrumentation
    REC = {"cur": None, "calls": [], "winner": None, "floors": []}
    originals = {}

    def patch(name, fn):
        originals[name] = getattr(M, name)
        setattr(M, name, fn)

    def spine_count(owner):
        return sum(1 for row in owner for v in row if v == -2)

    def w_grow_floor(*a, **k):
        targets, dims = a[0], a[1]
        cols, rows, cw, ch = M._grid(dims)
        rec = {
            "names": [t["name"] for t in targets],
            "cats": [t["category"] for t in targets],
            "t_min": [t["min_area"] for t in targets],
            "t_tgt": [t["target_area"] for t in targets],
            "t_max": [t["max_area"] for t in targets],
            "cell": cw * ch, "cols": cols, "rows": rows, "cw": cw, "ch": ch,
            "stages": [], "fit": [],
            "variant": {kk: (list(v) if isinstance(v, tuple) else v) for kk, v in k.items()
                        if kk in ("band_delta", "flip_egress", "rectify", "stair_at_spine", "connect_rooms")},
            "args": [a[2], a[3]] if len(a) > 3 else [],
        }
        prev = REC["cur"]
        REC["cur"] = rec
        # The per-candidate memo (a6341d9, a pure speedup) would skip the
        # wrapped stages for every candidate after the first - off here.
        k = dict(k)
        k["memo"] = None
        try:
            res = originals["_grow_floor"](*a, **k)
        finally:
            REC["cur"] = prev
        rec["result"] = res
        rec["stats"] = dict(k.get("stats") or {})
        REC["calls"].append(rec)
        return res

    def w_fit(idx, min_c, tgt_c, max_c, available):
        rec = REC["cur"]
        before = sum(tgt_c[i] for i in idx) if idx else 0
        r = originals["_fit_cells"](idx, min_c, tgt_c, max_c, available)
        if rec is not None:
            rec["fit"].append({"idx": list(idx), "available": available, "before": before,
                               "after": sum(tgt_c[i] for i in idx) if idx else 0,
                               "max_sum": sum(max_c[i] for i in idx) if idx else 0,
                               "min_sum": sum(min_c[i] for i in idx) if idx else 0})
        return r

    def w_grow_rooms(*a, **k):
        rec = REC["cur"]
        if rec is not None:
            rec["eng_target_cells"] = list(a[5])
            rec["min_cells"] = list(a[6])
            rec["max_cells"] = list(a[7])
            rec["band"] = a[9] if len(a) > 9 else k.get("band", 0)
            rec["spine0"] = spine_count(a[0])
            rec["stages"].append(("pre_grow", [len(s) for s in a[1]], spine_count(a[0])))
        r = originals["_grow_rooms"](*a, **k)
        if rec is not None:
            rec["stages"].append(("grow", [len(s) for s in a[1]], spine_count(a[0])))
        return r

    def stage(name, cells_i, owner_i):
        orig_name = {"fill": "_fill_leftovers_bounded", "rebal": "_rebalance_over_max",
                     "ensure": "_ensure_minimum_presence", "shape": None, "arms": "_carve_access_arms"}

        def make(fn_name):
            def w(*a, **k):
                rec = REC["cur"]
                r = originals[fn_name](*a, **k)
                if rec is not None:
                    rec["stages"].append((name, [len(s) for s in a[cells_i]], spine_count(a[owner_i])))
                return r
            return w
        return make

    def w_apply(rooms, *a, **k):
        REC["winner"] = next((c for c in REC["calls"] if c["result"] is rooms), None)
        return originals["_apply_plan_transform"](rooms, *a, **k)

    def w_lfww(rooms, dims, garage_cars=None, facing=None, plan=None):
        REC["calls"] = []
        REC["winner"] = None
        res = originals["layout_floor_with_warnings"](rooms, dims, garage_cars, facing, plan)
        REC["floors"].append({
            "winner": REC["winner"], "diag": dict(M.LAST_FLOOR_DIAGNOSTICS),
            "plan_path": getattr(plan, "layout_path", None), "n_calls": len(REC["calls"]),
        })
        return res

    patch("_grow_floor", w_grow_floor)
    patch("_fit_cells", w_fit)
    patch("_grow_rooms", w_grow_rooms)
    originals["_fill_leftovers_bounded"] = M._fill_leftovers_bounded
    M._fill_leftovers_bounded = stage("fill", 1, 0)("_fill_leftovers_bounded")
    originals["_rebalance_over_max"] = M._rebalance_over_max
    M._rebalance_over_max = stage("rebal", 1, 0)("_rebalance_over_max")
    originals["_ensure_minimum_presence"] = M._ensure_minimum_presence
    M._ensure_minimum_presence = stage("ensure", 0, 2)("_ensure_minimum_presence")
    originals["_rectify_rooms"] = M._rectify_rooms
    M._rectify_rooms = stage("shape", 1, 0)("_rectify_rooms")
    originals["_reconnect_rooms"] = M._reconnect_rooms
    M._reconnect_rooms = stage("shape", 1, 0)("_reconnect_rooms")
    originals["_carve_access_arms"] = M._carve_access_arms
    M._carve_access_arms = stage("arms", 1, 0)("_carve_access_arms")
    patch("_apply_plan_transform", w_apply)
    patch("layout_floor_with_warnings", w_lfww)

    def area(r):
        return sum(p["w"] * p["h"] for p in room_parts(r))

    STAGES = ("grow", "fill", "rebal", "ensure", "shape", "arms")

    def ledger(rec):
        """{name: {stage: cells}} carrying forward missing stages."""
        out = {}
        sizes = {}
        last = None
        for nm, s, sp in rec["stages"]:
            sizes[nm] = (s, sp)
        for st in ("pre_grow",) + STAGES:
            if st in sizes:
                last = sizes[st]
            sizes[st] = last
        for i, n in enumerate(rec["names"]):
            out[n] = {st: (sizes[st][0][i] if sizes[st] else None) for st in ("pre_grow",) + STAGES}
        hall = {st: (sizes[st][1] if sizes[st] else None) for st in ("pre_grow",) + STAGES}
        return out, hall

    def run_mode(label, stair_block):
        prev_flag, prev_block = settings.house_planner_enabled, HP.STAIR_FIXED_BLOCK
        settings.house_planner_enabled = True
        HP.STAIR_FIXED_BLOCK = stair_block
        floors_out = []
        try:
            for case in cases:
                REC["floors"] = []
                n = len(case["floors"])
                plan, results = M.layout_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], n)
                logs = REC["floors"][-n:]
                unit = case["plot"].get("unit") or "ft"
                for i in range(n):
                    used, rects, warnings = results[i]
                    fplan = plan.floors[i]
                    log = logs[i]
                    rec = log["winner"]
                    fp = plan.footprint_length * plan.footprint_width
                    actual = {r["name"]: area(r) for r in rects}
                    hall_act = actual.get("Hallway", 0.0)
                    targets = dict(fplan.room_targets)
                    sum_t = sum(targets.values())
                    rows_ = []
                    led, hall_led = ledger(rec) if rec else ({}, {})
                    cell = rec["cell"] if rec else 0.0
                    eng_t = dict(zip(rec["names"], rec.get("eng_target_cells", []))) if rec else {}
                    for name in sorted(set(targets) | {k for k in actual if k != "Hallway"}):
                        pt = targets.get(name)
                        fa = actual.get(name, 0.0)
                        L = led.get(name, {})
                        row = {"name": name, "cat": classify_room_category(name), "plan_target": pt,
                               "plan_max": fplan.room_max.get(name), "final": fa,
                               "delta": (fa - pt) if pt is not None else None, "in_plan": pt is not None,
                               "drawn": name in actual}
                        if rec and name in eng_t and pt is not None:
                            q = round(pt / cell) * cell
                            et = eng_t[name] * cell
                            row["quant"] = q - pt
                            row["fit"] = et - q
                            prevv = et
                            for st in STAGES:
                                v = L.get(st)
                                if v is None:
                                    row[st] = 0.0
                                    continue
                                row[st] = v * cell - prevv
                                prevv = v * cell
                            row["ledger_end"] = prevv
                        rows_.append(row)
                    room_rows = [r for r in rows_ if r["in_plan"]]
                    surplus = fp - sum_t - hall_act
                    pos = [r for r in room_rows if r["drawn"] and r["delta"] is not None]
                    absorber = max(pos, key=lambda r: r["delta"]) if pos else None
                    by_name = {r["name"]: r for r in rects}
                    stair = next((r for r in rects if classify_room_category(r["name"]) == "staircase"), None)
                    hall = by_name.get("Hallway")
                    adj = {}
                    if absorber and absorber["name"] in by_name:
                        ar = by_name[absorber["name"]]
                        adj = {
                            "stair": bool(stair is not None and stair is not ar and shared_edge_between_rooms(ar, stair)),
                            "hallway": bool(hall is not None and shared_edge_between_rooms(ar, hall)),
                            "neighbours": sorted(o["name"] for o in rects if o is not ar and shared_edge_between_rooms(ar, o)),
                        }
                    stair_min = min_area_for_room("Staircase", unit)
                    st_info = None
                    if stair is not None:
                        srow = next((r for r in rows_ if r["name"] == stair["name"]), {})
                        st_info = {"name": stair["name"], "actual": area(stair), "min": stair_min,
                                   "ratio": area(stair) / stair_min, "plan_target": srow.get("plan_target"),
                                   "ledger": {k: srow.get(k) for k in ("quant", "fit") + STAGES},
                                   "w": stair["w"], "h": stair["h"], "parts": len(room_parts(stair)),
                                   "neighbours": sorted(o["name"] for o in rects if o is not stair and shared_edge_between_rooms(stair, o))}
                    hard = Counter(v.kind for v in warnings if v.hard)
                    floors_out.append({
                        "key": f"{case['id']}/floor{i + 1}", "case": case["id"], "floor": i + 1, "unit": unit,
                        "footprint": fp, "fp_dims": [plan.footprint_length, plan.footprint_width],
                        "plot_area": case["plot"]["length"] * case["plot"]["width"],
                        "band": plan.band, "tier": plan.tier,
                        "sum_targets": sum_t, "plan_circ": fplan.circulation_area, "plan_usable": fplan.usable_area,
                        "plan_unalloc": fplan.unallocated_area, "hall_actual": hall_act,
                        "hall_ledger": {k: (v * cell if v is not None else None) for k, v in hall_led.items()},
                        "surplus": surplus, "remainder_rooms": list(fplan.remainder_rooms),
                        "added_rooms": [a["name"] for a in fplan.added_rooms], "rooms": rows_,
                        "absorber": absorber and {"name": absorber["name"], "cat": absorber["cat"], "delta": absorber["delta"],
                                                  "plan_target": absorber["plan_target"], "final": absorber["final"],
                                                  "ledger": {k: absorber.get(k) for k in ("quant", "fit") + STAGES}, **adj},
                        "gross_pos": sum(max(r["delta"], 0) for r in pos), "gross_neg": sum(min(r["delta"], 0) for r in pos),
                        "stair": st_info, "stair_block": dict(plan.stair_block) if plan.stair_block else None,
                        "stair_block_area": plan.stair_area,
                        "path": log["diag"].get("path"), "plan_path": log["plan_path"],
                        "diag": {k: v for k, v in log["diag"].items() if k != "seconds"},
                        "cell": cell, "cell_dims": [rec["cw"], rec["ch"]] if rec else None,
                        "grid": [rec["cols"], rec["rows"]] if rec else None, "band_rows": rec.get("band") if rec else None,
                        "fit": rec["fit"] if rec else [], "stats": {k: v for k, v in (rec["stats"] if rec else {}).items()
                                                                    if isinstance(v, (int, float, str, list, bool))},
                        "variant": rec["variant"] if rec else None,
                        "winner_found": rec is not None, "n_runs": log["n_calls"],
                        "hard": dict(hard), "hard_total": sum(hard.values()),
                        "plan": {"program_min": plan.program_min, "program_ceiling": plan.program_ceiling,
                                 "envelope": plan.envelope_area, "net_plot": plan.net_plot_area,
                                 "built_area_cap": plan.built_area_cap, "porch_area": plan.porch_area,
                                 "total_floors": plan.total_floors},
                    })
                print(f"[{label}] {case['id']} done", flush=True)
        finally:
            settings.house_planner_enabled, HP.STAIR_FIXED_BLOCK = prev_flag, prev_block
        return floors_out

    try:
        modes = {"baseline": run_mode("baseline", False), "stair_block": run_mode("stair_block", True)}
    finally:
        for name, fn in originals.items():
            setattr(M, name, fn)

    # ------------------------------------------------------------ big-plot growth
    growth = []
    for c in cases:
        if not any(p in c["id"] for p in ("150x150", "150x200", "300x300")):
            continue
        f = next(x for x in modes["baseline"] if x["case"] == c["id"])
        p = f["plan"]
        unit = f["unit"]
        band = rules.plot_band(p["net_plot"], unit)
        adjust = rules.floor_count_coverage_adjust(p["total_floors"])
        target = band.target_cov * adjust * p["net_plot"]
        ceil85 = p["program_ceiling"] * HP.CEILING_PACKING_FRACTION
        cap_fp = p["built_area_cap"] / p["total_floors"] if p["built_area_cap"] else None
        cands = {"band_target": target, "ceiling_x0.85": ceil85, "envelope": p["envelope"]}
        if cap_fp is not None:
            cands["per_bedroom_cap_fp"] = max(cap_fp, p["program_min"])
        upper = min(ceil85, p["envelope"])
        if cap_fp is not None:
            upper = min(upper, max(cap_fp, p["program_min"]))
        enclosed = min(max(target, p["program_min"]), upper)
        binding = min(cands, key=lambda k: cands[k]) if target > upper else "band_target"
        growth.append({"case": c["id"], "band": band.name, "tier": f["tier"], "net_plot": p["net_plot"],
                       "band_target": target, "program_min": p["program_min"], "program_ceiling": p["program_ceiling"],
                       "ceiling_x0.85": ceil85, "envelope": p["envelope"], "per_bedroom_cap_fp": cap_fp,
                       "enclosed_area": enclosed, "porch": p["porch_area"], "footprint": f["footprint"],
                       "coverage_pct": 100 * f["footprint"] / f["plot_area"], "binding": binding,
                       "target_over_footprint": target / f["footprint"]})

    # ------------------------------------------------------------ report
    L = []
    o = L.append

    def fmt(v, w=7, d=0):
        if v is None:
            return "-".rjust(w)
        return f"{v:{w}.{d}f}"

    for label, floors in modes.items():
        o(f"\n{'=' * 100}\nMODE {label}  (planner ON, STAIR_FIXED_BLOCK={label == 'stair_block'})\n{'=' * 100}")
        o("All areas in sq ft (every matrix plot is in ft). surplus = footprint - sum(plan targets) - actual hallway "
          "(= sum of room deltas).")
        o(f"{'floor':<36}{'fp':>7}{'sumT':>7}{'circP':>7}{'hall':>7}{'surpl':>7}{'+gross':>7}{'-gross':>7}"
          f"{'cell':>6} {'absorber (delta, cat, adj stair/hall)':<44}{'stair(x min)':>14} path")
        for f in floors:
            ab = f["absorber"] or {}
            st = f["stair"]
            ab_txt = "%s %+.0f %s %s%s" % (ab.get("name", "-"), ab.get("delta", 0) or 0, ab.get("cat", ""),
                                            "S" if ab.get("stair") else "-", "H" if ab.get("hallway") else "-")
            st_txt = "%.0f (%.1fx)" % (st["actual"], st["ratio"]) if st else "-"
            o(f"{f['key']:<36}{fmt(f['footprint'])}{fmt(f['sum_targets'])}{fmt(f['plan_circ'])}{fmt(f['hall_actual'])}"
              f"{fmt(f['surplus'])}{fmt(f['gross_pos'])}{fmt(f['gross_neg'])}{fmt(f['cell'], 6, 1)} "
              f"{ab_txt:<44}{st_txt:>14} {f['path']}")
        o("")
        o("Per-room ledger (sq ft): plan target -> quant (grid rounding) -> fit (zone rescale in _fit_cells) -> grow "
          "-> fill -> rebal -> ensure -> shape (rectify/reconnect) -> arms -> final")
        for f in floors:
            o(f"\n-- {f['key']}  fp {f['fp_dims'][0]:.1f}x{f['fp_dims'][1]:.1f}={f['footprint']:.0f}  grid {f['grid']} "
              f"cell {f['cell']:.2f}  band_rows {f['band_rows']}  plan circ {f['plan_circ']:.0f} vs hall {f['hall_actual']:.0f}  "
              f"unalloc {f['plan_unalloc']:.0f}  surplus {f['surplus']:+.0f}  path {f['path']}  hard {f['hard']}")
            hl = f["hall_ledger"]
            o("   hallway cells x cell: " + "  ".join(f"{k}={v:.0f}" for k, v in hl.items() if v is not None))
            for fz in f["fit"]:
                o(f"   _fit_cells zone: rooms={len(fz['idx'])} available={fz['available']} cells, targets before={fz['before']} "
                  f"after={fz['after']} (min sum {fz['min_sum']}, max sum {fz['max_sum']}) -> "
                  f"{(fz['available'] - fz['before']) * f['cell']:+.0f} sq ft to redistribute")
            o(f"   {'room':<22}{'cat':<10}{'target':>7}{'max':>7}{'quant':>7}{'fit':>7}{'grow':>7}{'fill':>7}{'rebal':>7}"
              f"{'ensur':>7}{'shape':>7}{'arms':>7}{'final':>7}{'delta':>7}")
            for r in sorted(f["rooms"], key=lambda r: -(r["delta"] or 0)):
                o(f"   {r['name'][:21]:<22}{r['cat'][:9]:<10}{fmt(r['plan_target'])}{fmt(r['plan_max'])}"
                  + "".join(fmt(r.get(k)) for k in ("quant", "fit") + STAGES)
                  + f"{fmt(r['final'])}{fmt(r['delta'])}" + ("" if r["drawn"] else "  (not drawn)"))

        # aggregates
        o(f"\n--- AGGREGATES ({label}) ---")
        n = len(floors)
        for thr in (25, 50, 100, 200):
            o(f"floors with |surplus| > {thr} sq ft: {sum(1 for f in floors if abs(f['surplus']) > thr)}/{n}; "
              f"surplus > +{thr}: {sum(1 for f in floors if f['surplus'] > thr)}")
        cats = Counter((f["absorber"] or {}).get("cat") for f in floors)
        o("absorber category (largest positive delta): " + ", ".join(f"{k}={v}" for k, v in cats.most_common()))
        adjs = Counter(((f["absorber"] or {}).get("stair"), (f["absorber"] or {}).get("hallway")) for f in floors)
        o("absorber adjacency (stair, hallway): " + ", ".join(f"{k}={v}" for k, v in adjs.items()))
        per_cat = defaultdict(list)
        for f in floors:
            for r in f["rooms"]:
                if r["in_plan"] and r["drawn"]:
                    per_cat[r["cat"]].append(r["delta"])
        o(f"{'category':<12}{'n':>4}{'mean|d|':>9}{'max|d|':>9}{'mean d':>9}{'max +d':>9}{'min d':>9}")
        for c, ds in sorted(per_cat.items(), key=lambda kv: -max(abs(x) for x in kv[1])):
            o(f"{c:<12}{len(ds):>4}{sum(abs(x) for x in ds) / len(ds):>9.0f}{max(abs(x) for x in ds):>9.0f}"
              f"{sum(ds) / len(ds):>9.0f}{max(ds):>9.0f}{min(ds):>9.0f}")
        # stage attribution: sum of |stage contribution| across every room
        tot = defaultdict(float)
        totpos = defaultdict(float)
        for f in floors:
            for r in f["rooms"]:
                for k in ("quant", "fit") + STAGES:
                    v = r.get(k)
                    if v is not None:
                        tot[k] += abs(v)
                        totpos[k] += max(v, 0)
        o("stage attribution over all rooms (sum |contribution| / sum of positive contribution), sq ft: "
          + ", ".join(f"{k}={tot[k]:.0f}/{totpos[k]:.0f}" for k in ("quant", "fit") + STAGES))
        ab_stage = Counter()
        for f in floors:
            ab = f["absorber"]
            if ab and ab.get("ledger"):
                led = {k: v for k, v in ab["ledger"].items() if v is not None}
                if led:
                    ab_stage[max(led, key=lambda k: led[k])] += 1
        o("absorber's biggest positive stage: " + ", ".join(f"{k}={v}" for k, v in ab_stage.most_common()))
        stairs = [f for f in floors if f["stair"]]
        big = [f for f in stairs if f["stair"]["ratio"] > 2.0]
        o(f"stair floors: {len(stairs)}; stair > 2.0x min: {len(big)}")
        st_src = Counter()
        for f in big:
            led = {k: v for k, v in f["stair"]["ledger"].items() if v}
            src = max(led, key=lambda k: led[k]) if led else "?"
            st_src[src] += 1
            o(f"   {f['key']}: stair {f['stair']['actual']:.0f} sq ft ({f['stair']['ratio']:.1f}x), "
              f"{f['stair']['w']:.1f}x{f['stair']['h']:.1f} parts={f['stair']['parts']} plan target "
              f"{fmt(f['stair']['plan_target'], 1)}; ledger " + " ".join(f"{k}={v:+.0f}" for k, v in led.items())
              + f"; neighbours {f['stair']['neighbours']}")
        o("   largest stair growth stage: " + ", ".join(f"{k}={v}" for k, v in st_src.most_common()))
        o(f"hard violations total: {sum(f['hard_total'] for f in floors)}; by kind: "
          + ", ".join(f"{k}={v}" for k, v in sum((Counter(f['hard']) for f in floors), Counter()).most_common()))
        o("engine path (LAST_FLOOR_DIAGNOSTICS / plan.layout_path): "
          + ", ".join(f"{k}={v}" for k, v in Counter(f["path"] for f in floors).most_common()))
        if label == "stair_block":
            intact = Counter(f["stats"].get("stair_block_intact") for f in floors if f["stair"])
            o(f"stair block intact on winners: {dict(intact)}; block area {floors[0]['stair_block_area'] if floors else 0:.1f}")

    # mode comparison
    o(f"\n{'=' * 100}\nBASELINE vs STAIR BLOCK per floor (stair area, hard count, absorber)\n{'=' * 100}")
    bmap = {f["key"]: f for f in modes["stair_block"]}
    for f in modes["baseline"]:
        g = bmap[f["key"]]
        sa = f["stair"]["actual"] if f["stair"] else None
        sb = g["stair"]["actual"] if g["stair"] else None
        o(f"{f['key']:<36} stair {fmt(sa)} -> {fmt(sb)}   hard {f['hard_total']:>2} -> {g['hard_total']:>2}   "
          f"surplus {f['surplus']:+6.0f} -> {g['surplus']:+6.0f}   absorber {(f['absorber'] or {}).get('name')} -> "
          f"{(g['absorber'] or {}).get('name')} ({(g['absorber'] or {}).get('delta', 0):+.0f})"
          + (f"   new hard: {dict(Counter(g['hard']) - Counter(f['hard']))}" if Counter(g['hard']) - Counter(f['hard']) else ""))

    o(f"\n{'=' * 100}\nBIG-PLOT GROWTH (baseline): what binds the footprint\n{'=' * 100}")
    for gr in growth:
        o(f"{gr['case']:<26} band {gr['band']}/{gr['tier']} net {gr['net_plot']:.0f} | band target {gr['band_target']:.0f} | "
          f"program min {gr['program_min']:.0f} ceiling {gr['program_ceiling']:.0f} x0.85={gr['ceiling_x0.85']:.0f} | "
          f"envelope {gr['envelope']:.0f} | per-bedroom cap/floor {fmt(gr['per_bedroom_cap_fp'], 1)} | "
          f"enclosed {gr['enclosed_area']:.0f} + porch {gr['porch']:.0f} = footprint {gr['footprint']:.0f} "
          f"({gr['coverage_pct']:.1f}% cov; band target is {gr['target_over_footprint']:.1f}x it) -> BINDS: {gr['binding']}")

    text = "\n".join(L)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump({"modes": modes, "growth": growth}, fh, default=str)
    print(text[-6000:])
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
