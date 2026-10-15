"""Step 5.1 diagnostic (READ-ONLY - no engine behaviour change).

For every matrix floor (tests/mask_quality_matrix.py) it instruments the real
mask-engine path with module-attribute wrappers (restored afterwards) and
answers four questions:

  1. per-floor area ledger: footprint, sum of plan targets, plan circulation
     vs the actual Hallway, reserved vs actual stair, cells left UNCLAIMED
     after growth and which room absorbs them;
  2. per-stage state of the CHOSEN candidate (planner on): after growth,
     bounded fill, rebalance, ensure, rectify (local search), reconnect, arms -
     hard violations by type (engine scorer, the same call the engine makes),
     parts per room, suite (bedroom <-> its ensuite) contact, over-max rooms;
  3. why the repair step (_rectify_rooms / _reconnect_rooms) loses suite
     shared walls: every applied shape move is recorded (detected by watching
     the cells_by_room list inside the ShapeCtx.info calls - read-only), with
     the move kind (give / swap / pair re-split / reconnect), and the suite
     pairs whose contact it broke;
  4. ORIGIN of every final hard violation: the first stage from which it is
     present continuously up to the final layout (planner target -> growth
     -> fill -> rebalance -> ensure -> rectify -> reconnect -> arms).

Two passes per case:
  A (timing)  - memo ON, wrappers only time the stages: the real per-stage
                cpu share of the matrix;
  B (trace)   - memo OFF (a pure speedup cache - output identical), stage
                snapshots + scoring for the chosen candidate.
Flag-off is traced too (stages: grow -> fill -> ensure; no planner targets).

  python scripts/mask_step5_diagnostic.py [--cases a,b] [--jobs 4] [--modes on,off]
Output: scripts/output/quality/step5_diagnostic.txt (+ .json)
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT_DIR = os.path.join(ROOT, "scripts", "output", "quality")

STAGES_ON = ("grow", "fill", "rebal", "ensure", "rectify", "reconnect", "arms")
STAGES_OFF = ("grow", "fill", "ensure")
TIMED = ("_seed_rooms", "_reserve_spine", "_grow_rooms", "_fill_leftovers_bounded", "_fill_leftovers",
         "_rebalance_over_max", "_ensure_minimum_presence", "_rectify_rooms", "_reconnect_rooms",
         "_carve_access_arms", "score_floor_layout", "_cells_to_room")


# --------------------------------------------------------------------------- helpers

def _snapshot(owner, cells_by_room):
    return {
        "cells": [frozenset(s) for s in cells_by_room],
        "spine": frozenset((c, r) for r, row in enumerate(owner) for c, v in enumerate(row) if v == -2),
        "free": sum(1 for row in owner for v in row if v == -1),
    }


def _suite_pairs(targets):
    by = defaultdict(list)
    for i, t in enumerate(targets):
        if t.get("suite") is not None:
            by[t["suite"]].append(i)
    out = []
    for idx in by.values():
        baths = [i for i in idx if targets[i]["category"] in ("bathroom", "powder")]
        beds = [i for i in idx if targets[i]["category"] not in ("bathroom", "powder")]
        out += [(a, b) for a in beds for b in baths]
    return out


def _contact_edges(a, b):
    """Number of shared cell edges between two cell sets."""
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return sum(1 for (c, r) in small for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)) if (c + dc, r + dr) in big)


# --------------------------------------------------------------------------- worker

def run_case(case_id, planner, trace):
    from app.config import settings
    from app.pipeline import floor_layout_mask as M
    from tests.mask_quality_matrix import CASES

    case = next(c for c in CASES if c["id"] == case_id)
    unit = case["plot"].get("unit") or "ft"
    originals = {}
    T = defaultdict(float)  # exclusive seconds per timed function
    stack = []  # for exclusive timing
    REC = {"cur": None, "calls": [], "winner": None, "floors": [], "floor_no": None, "plan": None}

    def timed(name, fn):
        def w(*a, **k):
            t0 = time.perf_counter()
            stack.append(0.0)
            try:
                return fn(*a, **k)
            finally:
                dt = time.perf_counter() - t0
                inner = stack.pop()
                T[name] += dt - inner
                if stack:
                    stack[-1] += dt
        return w

    def patch(name, fn):
        originals[name] = getattr(M, name)
        setattr(M, name, fn)

    # ---- stage snapshot wrappers (pass B only)
    def stage_wrap(stage, fn, owner_i, cells_i, before=None):
        def w(*a, **k):
            rec = REC["cur"]
            if rec is not None and before:
                rec["stages"].append((before, _snapshot(a[owner_i], a[cells_i])))
            r = fn(*a, **k)
            if rec is not None:
                rec["stages"].append((stage, _snapshot(a[owner_i], a[cells_i])))
            return r
        return w

    def rectify_wrap(fn):
        def w(*a, **k):
            rec = REC["cur"]
            if rec is None:
                return fn(*a, **k)
            owner, cells_by_room, targets = a[0], a[1], a[2]
            ctx = a[8] if len(a) > 8 else k.get("ctx")
            pairs = _suite_pairs(targets)
            last = {"ids": tuple(id(s) for s in cells_by_room), "sets": [set(s) for s in cells_by_room]}
            moves = rec.setdefault("moves", [])
            orig_info = ctx.info

            def watch():
                ids = tuple(id(s) for s in cells_by_room)
                if ids == last["ids"]:
                    return
                changed = [i for i, (x, y) in enumerate(zip(ids, last["ids"])) if x != y]
                before = last["sets"]
                after = [set(s) for s in cells_by_room]
                gain = {i: len(after[i] - before[i]) for i in changed}
                loss = {i: len(before[i] - after[i]) for i in changed}
                if REC.get("in_reconnect"):
                    kind = "reconnect"
                else:
                    kind = "pair" if REC.get("pair_hit") and set(changed) == set(REC["pair_hit"]) else (
                        "swap" if all(gain[i] and loss[i] for i in changed) else "give")
                REC["pair_hit"] = None
                broken = []
                for bi, ba in pairs:
                    if bi in changed or ba in changed:
                        if _contact_edges(before[bi], before[ba]) and not _contact_edges(after[bi], after[ba]):
                            broken.append([targets[bi]["name"], targets[ba]["name"]])
                donor = max(changed, key=lambda i: loss[i]) if changed else None
                d_cost = orig_info(frozenset(before[donor]))[0] if donor is not None else 0.0
                d_parts = len(orig_info(frozenset(before[donor]))[1]) if donor is not None else 0
                moves.append({
                    "kind": kind, "rooms": [targets[i]["name"] for i in changed],
                    "donor": targets[donor]["name"] if donor is not None else None,
                    "donor_cat": targets[donor]["category"] if donor is not None else None,
                    "donor_cost": d_cost, "donor_parts": d_parts,
                    "donor_over": max(0, len(before[donor]) - REC["max_cells"][donor]) if donor is not None else 0,
                    "cells": sum(loss.values()), "broken": broken,
                })
                last["ids"], last["sets"] = ids, after

            def info(cells):
                watch()
                return orig_info(cells)

            ctx.info = info
            REC["max_cells"] = a[5]
            REC["watch"] = watch
            try:
                return fn(*a, **k)
            finally:
                watch()
                ctx.info = orig_info
                REC["watch"] = None
        return w

    def pair_wrap(fn):
        def w(*a, **k):
            res = fn(*a, **k)
            if res is not None:
                REC["pair_hit"] = (a[0], res[1])
            return res
        return w

    def reconnect_wrap(fn):
        # snapshot before (= rectify local search done) and after
        def w(*a, **k):
            rec = REC["cur"]
            watch = REC.get("watch")
            if rec is not None:
                if watch:
                    watch()  # flush the local search's last move
                rec["stages"].append(("rectify", _snapshot(a[0], a[1])))
                n_before = [set(s) for s in a[1]]
            REC["in_reconnect"] = True
            try:
                r = fn(*a, **k)
                if rec is not None and watch:
                    watch()
            finally:
                REC["in_reconnect"] = False
            if rec is not None:
                if not watch:  # reconnect-only path: compare before/after
                    after = a[1]
                    for bi, ba in _suite_pairs(a[2]):
                        if _contact_edges(n_before[bi], n_before[ba]) and not _contact_edges(after[bi], after[ba]):
                            rec.setdefault("moves", []).append({
                                "kind": "reconnect", "rooms": [a[2][bi]["name"], a[2][ba]["name"]],
                                "donor": None, "donor_cat": None, "donor_cost": 0, "donor_parts": 0, "donor_over": 0,
                                "cells": 0, "broken": [[a[2][bi]["name"], a[2][ba]["name"]]]})
                rec["stages"].append(("reconnect", _snapshot(a[0], a[1])))
            return r
        return w

    def grow_floor_wrap(fn):
        def w(*a, **k):
            if not trace:
                return fn(*a, **k)
            k = dict(k)
            k["memo"] = None  # memo is a pure speedup; off so every stage really runs
            dims = a[1]
            rec = {"targets": [dict(t) for t in a[0]], "dims": dict(dims),
                   "garage_cars": a[4] if len(a) > 4 else k.get("garage_cars"),
                   "planner": k.get("planner", a[5] if len(a) > 5 else False),
                   "rectify": k.get("rectify", True), "connect": list(k.get("connect_rooms") or ()),
                   "keep_suites": k.get("keep_suites", False), "stages": [],
                   "variant": [a[2], a[3], k.get("band_delta", 0), k.get("flip_egress", False),
                               k.get("stair_at_spine")]}
            prev = REC["cur"]
            REC["cur"] = rec
            try:
                res = fn(*a, **k)
            finally:
                REC["cur"] = prev
            rec["result"] = res
            rec["stats"] = {kk: v for kk, v in (k.get("stats") or {}).items() if isinstance(v, (int, float, bool))}
            REC["calls"].append(rec)
            return res
        return w

    def apply_wrap(fn):
        def w(rooms, *a, **k):
            REC["winner"] = next((c for c in REC["calls"] if c["result"] is rooms), None)
            return fn(rooms, *a, **k)
        return w

    def lfww_wrap(fn):
        def w(rooms, dims, garage_cars=None, facing=None, plan=None):
            REC["calls"] = []
            REC["winner"] = None
            t0 = time.perf_counter()
            res = fn(rooms, dims, garage_cars, facing, plan)
            REC["floors"].append({
                "winner": REC["winner"], "plan": plan, "seconds": time.perf_counter() - t0,
                "n_calls": len(REC["calls"]), "path": getattr(plan, "layout_path", None),
                "warnings": res[1], "rects": res[0],
            })
            return res
        return w

    for name in TIMED:
        patch(name, timed(name, getattr(M, name)))
    if trace:
        M._grow_rooms = stage_wrap("grow", M._grow_rooms, 0, 1)
        M._fill_leftovers_bounded = stage_wrap("fill", M._fill_leftovers_bounded, 0, 1)
        M._fill_leftovers = stage_wrap("fill", M._fill_leftovers, 0, 1)
        M._rebalance_over_max = stage_wrap("rebal", M._rebalance_over_max, 0, 1)
        M._ensure_minimum_presence = stage_wrap("ensure", M._ensure_minimum_presence, 2, 0)
        M._rectify_rooms = rectify_wrap(M._rectify_rooms)
        M._reconnect_rooms = reconnect_wrap(M._reconnect_rooms)
        M._carve_access_arms = stage_wrap("arms", M._carve_access_arms, 0, 1)
        originals.setdefault("_best_pair_resplit", M._best_pair_resplit)
        M._best_pair_resplit = pair_wrap(M._best_pair_resplit)
        patch("_grow_floor", grow_floor_wrap(M._grow_floor))
        patch("_apply_plan_transform", apply_wrap(M._apply_plan_transform))
        patch("_apply_facing_transform", apply_wrap(M._apply_facing_transform))
    patch("layout_floor_with_warnings", lfww_wrap(M.layout_floor_with_warnings))
    score_fn = originals["score_floor_layout"]

    prev_flag = settings.house_planner_enabled
    settings.house_planner_enabled = planner
    out = {"case": case_id, "planner": planner, "floors": []}
    try:
        n = len(case["floors"])
        t0 = time.perf_counter()
        if planner:
            plan, results = M.layout_building(case["floors"], case["plot"], case["facing"], case["garage_cars"], n)
            logs = REC["floors"][-n:]
        else:
            plan, results = None, []
            for rooms in case["floors"]:
                rects, warns = M.layout_floor_with_warnings(rooms, case["plot"], case["garage_cars"], case["facing"])
                results.append((rooms, rects, warns))
            logs = REC["floors"]
        out["seconds_total"] = time.perf_counter() - t0
        out["timing"] = dict(T)
        if not trace:
            return out
        from app.pipeline.room_specs import classify_room_category, min_area_for_room
        from app.pipeline.layout_quality import narrow_bedroom_names

        for i in range(n):
            log = logs[i]
            rec = log["winner"]
            fplan = log["plan"]
            fl = {"key": f"{case_id}/floor{i + 1}", "path": log["path"], "n_calls": log["n_calls"],
                  "winner_found": rec is not None}
            if rec is None:
                out["floors"].append(fl)
                continue
            targets = rec["targets"]
            cols, rows, cw, ch = M._grid(rec["dims"])
            cell = cw * ch
            flag = bool(rec["planner"] and rec["rectify"])
            room_targets = {t["name"]: t["target_area"] for t in targets} if planner else None
            room_max = {t["name"]: t["max_area"] for t in targets} if planner else None
            daylight_fp = {"x0": 0.0, "y0": 0.0, "x1": rec["dims"]["length"], "y1": rec["dims"]["width"]}
            fnum = fplan.floor_number if fplan is not None else 1

            def build(snap):
                rooms_out = []
                for t, cs in zip(targets, snap["cells"]):
                    rr = originals["_cells_to_room"](t["name"], set(cs), cw, ch, flag)
                    if rr is None:
                        continue
                    if t.get("suite") is not None:
                        rr["suite"] = t["suite"]
                    rooms_out.append(rr)
                if snap["spine"]:
                    h = originals["_cells_to_room"]("Hallway", set(snap["spine"]), cw, ch, flag)
                    if h:
                        rooms_out.append(h)
                return rooms_out

            def score(rooms_out):
                if planner:
                    return score_fn(rooms_out, rec["dims"], rec["garage_cars"], daylight_footprint=daylight_fp,
                                    planner_checks=True, room_targets=room_targets, room_max=room_max,
                                    facing="north", floor_number=fnum)
                return score_fn(rooms_out, rec["dims"], rec["garage_cars"])

            stage_names = STAGES_ON if planner else STAGES_OFF
            snaps = {}
            for nm, sn in rec["stages"]:
                snaps[nm] = sn  # last snapshot of that name wins
            pairs = _suite_pairs(targets)
            stage_rows = []
            prev = None
            for nm in stage_names:
                sn = snaps.get(nm, prev)
                if sn is None:
                    continue
                prev = sn
                rooms_out = build(sn)
                sc = score(rooms_out)
                hard = sorted({(v.kind, v.room) for v in sc.violations if v.hard})
                parts = {r["name"]: len(r.get("parts") or [r]) for r in rooms_out}
                suite = []
                for bi, ba in pairs:
                    e = _contact_edges(sn["cells"][bi], sn["cells"][ba])
                    suite.append([targets[bi]["name"], targets[ba]["name"], e])
                over = [targets[j]["name"] for j in range(len(targets))
                        if len(sn["cells"][j]) * cell > 1.10 * targets[j]["max_area"] + 1e-9
                        and targets[j]["category"] not in ("hallway", "staircase", "garage")]
                stage_rows.append({
                    "stage": nm, "present": nm in snaps, "hard": [list(h) for h in hard],
                    "parts_gt2": sorted(n_ for n_, p in parts.items() if p > 2),
                    "parts_eq2": sorted(n_ for n_, p in parts.items() if p == 2),
                    "suite": suite, "over_max": over, "free_cells": sn["free"],
                    "narrow_bedrooms": narrow_bedroom_names(sc.violations),
                    "sizes": [round(len(c) * cell, 1) for c in sn["cells"]],
                    "hall": round(len(sn["spine"]) * cell, 1),
                })
            final_engine = sorted({(v.kind, v.room) for v in log["warnings"] if v.hard})
            fl.update({
                "names": [t["name"] for t in targets], "cats": [t["category"] for t in targets],
                "t_min": [t["min_area"] for t in targets], "t_tgt": [t["target_area"] if planner else t["preferred_area"] for t in targets],
                "t_max": [t["max_area"] for t in targets], "cell": cell, "grid": [cols, rows],
                "stages": stage_rows, "final_engine_hard": [list(h) for h in final_engine],
                "moves": rec.get("moves", []), "stats": rec.get("stats", {}), "variant": rec["variant"],
                "rectify": str(rec["rectify"]), "keep_suites": rec["keep_suites"], "connect": rec["connect"],
                "seconds": log["seconds"],
            })
            if planner:
                fp_area = plan.footprint_length * plan.footprint_width
                stair_name = next((t["name"] for t in targets if t["category"] == "staircase"), None)
                final_rects = results[i][1]
                area_of = {r["name"]: sum(p["w"] * p["h"] for p in (r.get("parts") or [r])) for r in final_rects}
                fl.update({
                    "footprint": fp_area, "fp_dims": [plan.footprint_length, plan.footprint_width],
                    "sum_targets": sum(fplan.room_targets.values()), "plan_circ": fplan.circulation_area,
                    "hall_actual": area_of.get("Hallway", 0.0),
                    "stair_plan": fplan.room_targets.get(stair_name) if stair_name else None,
                    "stair_actual": area_of.get(stair_name) if stair_name else None,
                    "stair_min": min_area_for_room("Staircase", unit) if stair_name else None,
                    "remainder": list(fplan.remainder_rooms), "plan_targets": dict(fplan.room_targets),
                    "final_area": area_of, "tier": plan.tier, "band": plan.band,
                })
            out["floors"].append(fl)
    finally:
        settings.house_planner_enabled = prev_flag
        for name, fn in originals.items():
            setattr(M, name, fn)
    return out


# --------------------------------------------------------------------------- analysis

def origin_of(fl, viol, planner):
    """First stage from which `viol` (kind, room) is continuously present to
    the final stage; "planner_target" when the room's own target already
    violates it."""
    kind, room = viol
    if planner and room in fl["names"]:
        i = fl["names"].index(room)
        tgt, mn, mx = fl["t_tgt"][i], fl["t_min"][i], fl["t_max"][i]
        cat = fl["cats"][i]
        if kind == "small_area" and tgt < 0.98 * mn:
            return "planner_target"
        if kind == "oversized" and tgt > 1.10 * mx:
            return "planner_target"
        if kind == "stair_oversized" and fl.get("stair_min") and tgt > 2.0 * fl["stair_min"]:
            return "planner_target"
    if planner and kind == "hierarchy_inverted":
        cats = fl["cats"]
        liv = [fl["t_tgt"][j] for j, c in enumerate(cats) if c == "living"]
        din = [fl["t_tgt"][j] for j, c in enumerate(cats) if c == "dining"]
        if liv and din and max(liv) < max(din):
            return "planner_target"
    origin = None
    for row in reversed(fl["stages"]):
        if [kind, room] in row["hard"]:
            origin = row["stage"]
        else:
            break
    return origin or "final_only"


def analyse(results, planner):
    L = []
    o = L.append
    mode = "planner ON" if planner else "planner OFF"
    floors = [f for r in results for f in r["floors"] if f.get("winner_found")]
    stage_names = STAGES_ON if planner else STAGES_OFF

    # sanity: last stage score == engine's own scored warnings
    mism = [f["key"] for f in floors if sorted(map(tuple, f["stages"][-1]["hard"])) != sorted(map(tuple, f["final_engine_hard"]))]
    o(f"\n#### {mode}: {len(floors)} floors traced; last-stage re-score == engine warnings on "
      f"{len(floors) - len(mism)}/{len(floors)}" + (f" (differ: {', '.join(mism)})" if mism else ""))

    # ---- 1. per-stage counts of the chosen candidate
    o(f"\n[{mode}] Chosen candidate, per stage (summed over floors): hard violations, rooms with >2 parts, "
      "2-part rooms, suite pairs in contact / total, rooms >1.10x max, free cells")
    o(f"{'stage':<11}{'hard':>6}{'parts>2':>9}{'L(2p)':>7}{'suites':>10}{'over1.1':>9}{'narrowBR':>9}  hard by type")
    for nm in stage_names:
        rows_ = [next((s for s in f["stages"] if s["stage"] == nm), None) for f in floors]
        rows_ = [r for r in rows_ if r is not None]
        hard = Counter(h[0] for r in rows_ for h in r["hard"])
        suites = sum(1 for r in rows_ for s in r["suite"] if s[2] > 0)
        suites_t = sum(len(r["suite"]) for r in rows_)
        o(f"{nm:<11}{sum(hard.values()):>6}{sum(len(r['parts_gt2']) for r in rows_):>9}"
          f"{sum(len(r['parts_eq2']) for r in rows_):>7}{f'{suites}/{suites_t}':>10}"
          f"{sum(len(r['over_max']) for r in rows_):>9}{sum(len(r['narrow_bedrooms']) for r in rows_):>9}  "
          + ", ".join(f"{k}={v}" for k, v in hard.most_common()))

    # ---- 2. origin attribution
    table = defaultdict(Counter)
    per_floor = []
    for f in floors:
        final = f["stages"][-1]["hard"]
        orig = Counter()
        for v in final:
            src = origin_of(f, tuple(v), planner)
            table[v[0]][src] += 1
            orig[src] += 1
        per_floor.append((f["key"], dict(orig)))
    cols_ = (["planner_target"] if planner else []) + list(stage_names) + ["final_only"]
    o(f"\n[{mode}] ORIGIN of every final hard violation (first stage from which it persists to the end):")
    o(f"{'type':<22}" + "".join(f"{c[:10]:>11}" for c in cols_) + f"{'total':>7}")
    tot = Counter()
    for kind in sorted(table, key=lambda k: -sum(table[k].values())):
        o(f"{kind:<22}" + "".join(f"{table[kind][c]:>11}" for c in cols_) + f"{sum(table[kind].values()):>7}")
        tot.update(table[kind])
    total = sum(tot.values())
    o(f"{'TOTAL':<22}" + "".join(f"{tot[c]:>11}" for c in cols_) + f"{total:>7}")
    if total:
        o(f"{'share %':<22}" + "".join(f"{100 * tot[c] / total:>10.0f}%" for c in cols_))
        groups = {"planner targets": ["planner_target"], "growth": ["grow"], "fill (+rebal/ensure)": ["fill", "rebal", "ensure"],
                  "repair (rectify+reconnect)": ["rectify", "reconnect"], "arms": ["arms"], "final only": ["final_only"]}
        o("grouped: " + "; ".join(f"{g} {sum(tot[c] for c in cs)} ({100 * sum(tot[c] for c in cs) / total:.0f}%)"
                                   for g, cs in groups.items() if planner or g != "planner targets"))
    data = {"origin_table": {k: dict(v) for k, v in table.items()}, "per_floor_origin": per_floor}

    # ---- 3. suite contact & moves (planner)
    if planner:
        o(f"\n[{mode}] Suite contact (bedroom <-> its own ensuite shares >=1 cell edge) of the CHOSEN candidate:")
        lost = Counter()
        for f in floors:
            prev = None
            for s in f["stages"]:
                for p in s["suite"]:
                    k = (f["key"], p[0], p[1])
                    if prev is not None and prev.get(k, 0) > 0 and p[2] == 0:
                        lost[s["stage"]] += 1
                prev = {(f["key"], p[0], p[1]): p[2] for p in s["suite"]}
        o("   contact lost at stage: " + (", ".join(f"{k}={v}" for k, v in lost.items()) or "none"))
        mv = Counter()
        mv_broken = Counter()
        why = Counter()
        examples = []
        for f in floors:
            for m in f["moves"]:
                mv[m["kind"]] += 1
                if m["broken"]:
                    mv_broken[m["kind"]] += 1
                    reason = ("disconnected" if m["donor_cost"] >= 50 else "wrapped(>2 parts / bad L)" if m["donor_cost"] >= 10
                              else "L-shape cleanup" if m["donor_cost"] > 1 else "over-max relief" if m["donor_over"] else "other")
                    why[(m["kind"], m["donor_cat"], reason)] += 1
                    if len(examples) < 12:
                        examples.append(f"{f['key']}: {m['kind']} donor {m['donor']} ({m['donor_cat']}, cost {m['donor_cost']:.0f}, "
                                        f"{m['donor_parts']} parts, over {m['donor_over']}) moved {m['cells']} cells among {m['rooms']} "
                                        f"-> broke {m['broken']}")
        o("   applied shape moves on chosen candidates: " + ", ".join(f"{k}={v}" for k, v in mv.most_common())
          + "; of which broke a suite contact: " + (", ".join(f"{k}={v}" for k, v in mv_broken.most_common()) or "0"))
        o("   breaking moves by (move kind, donor category, why the donor was being repaired):")
        for (k, c, r), v in why.most_common():
            o(f"      {k:<10}{str(c):<10}{r:<28}{v}")
        for e in examples:
            o("      e.g. " + e)
        # orphan check at the end
        orph = [(f["key"], p[0], p[1]) for f in floors for p in f["stages"][-1]["suite"] if p[2] == 0]
        o(f"   suite pairs WITHOUT contact in the final chosen layout: {len(orph)}: "
          + "; ".join(f"{k} {a}/{b}" for k, a, b in orph))
        data["suite_lost_stage"] = dict(lost)

    # ---- 4. area ledger (planner)
    if planner:
        o(f"\n[{mode}] Area ledger (sq ft): fp footprint, sumT plan targets, circP plan circulation, hall actual hallway, "
          "stair plan->actual (x min), free after growth, overflow = cells the fill's LAST RESORT put past max, "
          "absorber = room with the largest fill+rebal gain")
        o(f"{'floor':<36}{'fp':>7}{'sumT':>7}{'circP':>7}{'hall':>7}{'stair':>16}{'free@grow':>10}{'overflow':>9}  absorber (gain)  path")
        for f in floors:
            st = f["stages"]
            g = next(s for s in st if s["stage"] == "grow")
            rb = next((s for s in st if s["stage"] == "rebal"), g)
            gains = [rb["sizes"][j] - g["sizes"][j] for j in range(len(f["names"]))]
            j = max(range(len(gains)), key=lambda x: gains[x])
            stair = (f"{f['stair_plan']:.0f}->{f['stair_actual']:.0f} ({f['stair_actual'] / f['stair_min']:.1f}x)"
                     if f.get("stair_actual") else "-")
            o(f"{f['key']:<36}{f['footprint']:>7.0f}{f['sum_targets']:>7.0f}{f['plan_circ']:>7.0f}{f['hall_actual']:>7.0f}"
              f"{stair:>16}{g['free_cells'] * f['cell']:>10.0f}{f['stats'].get('overflow_cells', 0) * f['cell']:>9.0f}  "
              f"{f['names'][j]} (+{gains[j]:.0f})  {f['path']}")
        tot_free = sum(next(s for s in f["stages"] if s["stage"] == "grow")["free_cells"] * f["cell"] for f in floors)
        tot_over = sum(f["stats"].get("overflow_cells", 0) * f["cell"] for f in floors)
        o(f"   total free after growth {tot_free:.0f} sq ft; total last-resort overflow {tot_over:.0f} sq ft "
          f"({sum(1 for f in floors if f['stats'].get('overflow_cells'))} floors)")
        # over-max rooms final
        om = Counter()
        for f in floors:
            for j, n in enumerate(f["names"]):
                a = f["final_area"].get(n)
                if a and f["cats"][j] not in ("hallway", "staircase", "garage") and n not in f["remainder"] and a > f["t_max"][j] * 1.0001:
                    om[f["cats"][j]] += 1
        o("   final rooms above their engine max (remainder rooms excluded), by category: "
          + ", ".join(f"{k}={v}" for k, v in om.most_common()))
    return L, data


def timing_report(results, label):
    L = []
    tot = Counter()
    wall = sum(r["seconds_total"] for r in results)
    for r in results:
        tot.update(r["timing"])
    measured = sum(tot.values())
    L.append(f"\n[{label}] cpu (exclusive seconds, memo ON = the real engine), matrix total {wall:.1f}s:")
    for k, v in tot.most_common():
        L.append(f"   {k:<26}{v:>8.1f}s {100 * v / wall:>5.1f}%")
    L.append(f"   {'(other: candidate loop, decomposition of outputs, plan)':<26}{wall - measured:>8.1f}s {100 * (wall - measured) / wall:>5.1f}%")
    return L, {"wall": wall, "by_fn": dict(tot)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--modes", default="on,off")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "step5_diagnostic.txt"))
    args = ap.parse_args()
    from tests.mask_quality_matrix import CASES

    only = {c for c in args.cases.split(",") if c}
    ids = [c["id"] for c in CASES if not only or c["id"] in only]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    from concurrent.futures import ProcessPoolExecutor

    lines = []
    blob = {}
    for m in modes:
        planner = m == "on"
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            timing = list(pool.map(run_case, ids, [planner] * len(ids), [False] * len(ids)))
            trace = list(pool.map(run_case, ids, [planner] * len(ids), [True] * len(ids)))
        tl, td = timing_report(timing, "planner ON" if planner else "planner OFF")
        al, ad = analyse(trace, planner)
        lines += tl + al
        blob[m] = {"timing": td, "analysis": ad,
                   "floors": [{k: v for k, v in f.items() if k not in ("stages",)} | {"stages": [
                       {kk: vv for kk, vv in s.items() if kk != "sizes"} for s in f.get("stages", [])]}
                       for r in trace for f in r["floors"]]}
        print(f"mode {m} done", flush=True)
    text = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    with open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(blob, fh, default=str)
    print(text)


if __name__ == "__main__":
    main()
