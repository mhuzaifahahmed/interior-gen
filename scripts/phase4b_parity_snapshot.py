"""Phase 4b parity check (READ-ONLY diagnostic): did the Phase 4b door rules
change rect-engine / mask-flag-off renders, and did any LAYOUT change?

Two steps, each run with the app code of a specific checkout (git worktree):

  1) snapshot (run once per checkout):
       python scripts/phase4b_parity_snapshot.py --root <worktree> --write OUT.json
     For every matrix floor (tests/mask_quality_matrix.py, taken from <root>)
     in three modes - mask_off (mask engine, planner flag OFF), mask_on
     (planner flag ON, floor_layout_mask.layout_building), rect
     (floor_layout.layout_floor_with_warnings) - stores: the exact rects,
     the warnings, sha256 of the PNG, a canonical DXF dump, the Concept
     Layout door specs (raw) and the conditioning image hash.

  2) compare (run with the NEWER checkout as --root):
       python scripts/phase4b_parity_snapshot.py --root <new> --compare OLD.json NEW.json \
           --old-door-graph <old_root>/app/pipeline/door_graph.py
     Reports rect/warning equality per mode, artifact differences, and - on
     floors whose rects are identical - attributes every door difference to a
     rule by re-deriving the door sets with the OLD door_graph module and the
     NEW one on the same rects.

Writes nothing outside the given output path. Never modifies the checkout.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import sys


def _setup(root: str):
    sys.path.insert(0, os.path.abspath(root))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _r(v):
    return round(float(v), 4)


def _canon_entity(e) -> str:
    t = e.dxftype()
    parts = [t, e.dxf.layer]
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
    elif t == "TEXT":
        p = e.dxf.insert
        parts.append(repr((e.dxf.text, _r(p.x), _r(p.y), _r(e.dxf.height))))
    elif t == "MTEXT":
        p = e.dxf.insert
        parts.append(repr((e.text, _r(p.x), _r(p.y))))
    else:
        parts.append(repr(sorted((k, str(v)) for k, v in e.dxf.all_existing_dxf_attribs().items()
                                 if k not in ("handle", "owner"))))
    return "|".join(parts)


def snapshot(root: str, out: str) -> None:
    _setup(root)
    import ezdxf

    from app.config import settings
    from app.pipeline import floor_layout, floor_layout_mask
    from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf
    from app.pipeline.blueprint_svg import render_floor_blueprint
    from app.pipeline.conditioning_image import render_conditioning_edge_map
    from app.providers.kaggle_autocad import _door_specs_for_floor
    from tests.mask_quality_matrix import CASES

    prev_p, prev_e = settings.house_planner_enabled, settings.house_layout_engine
    snap = {}
    try:
        for mode in ("mask_off", "rect", "mask_on"):
            settings.house_planner_enabled = mode == "mask_on"
            settings.house_layout_engine = "rect" if mode == "rect" else "mask"
            for case in CASES:
                floors = case["floors"]
                if mode == "mask_on":
                    _plan, res = floor_layout_mask.layout_building(
                        floors, case["plot"], case["facing"], case["garage_cars"], len(floors))
                    per_floor = [(r[1], r[2]) for r in res]
                else:
                    fn = floor_layout.layout_floor_with_warnings if mode == "rect" else floor_layout_mask.layout_floor_with_warnings
                    per_floor = [fn(rooms, case["plot"], case["garage_cars"], case["facing"]) for rooms in floors]
                for i, (rects, warnings) in enumerate(per_floor, start=1):
                    plot, facing, total = case["plot"], case["facing"], len(floors)
                    png = render_floor_blueprint(i, rects, plot, total, facing)
                    dxf = render_floor_blueprint_dxf(i, rects, plot, facing)
                    doc = ezdxf.read(io.StringIO(dxf.decode("utf-8")))
                    canon = sorted(_canon_entity(e) for e in doc.modelspace())
                    specs = _door_specs_for_floor(rects, plot, facing, i)
                    cond = render_conditioning_edge_map(rects, plot, facing=facing, floor_number=i)
                    snap[f"{mode}:{case['id']}/floor{i}"] = {
                        "case": case["id"], "floor": i, "plot": plot, "facing": facing, "total": total,
                        "rects": rects,
                        "warnings": [[v.room, v.kind, bool(v.hard), str(getattr(v, "detail", ""))] for v in warnings],
                        "png": _sha(png),
                        "dxf": _sha("\n".join(canon).encode()),
                        "dxf_lines": canon,
                        "door_specs": json.loads(json.dumps(specs, default=str)),
                        "cond": _sha(cond),
                    }
            print(f"{mode}: done", flush=True)
    finally:
        settings.house_planner_enabled, settings.house_layout_engine = prev_p, prev_e
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snap, f, default=str)
    print(f"wrote {len(snap)} floor entries -> {out}")


def _load_old_door_graph(path: str):
    spec = importlib.util.spec_from_file_location("old_door_graph", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pair_key(a, b):
    return tuple(sorted((a["name"], b["name"])))


def _edge_key(e):
    if e is None:
        return None
    return (e["orientation"], _r(e["pos"]), _r(e["start"]), _r(e["end"]))


def _door_sets(dg, rects, plot, facing, floor, **kw):
    g = dg.build_door_graph(rects, plot, facing, floor, **kw)
    pairs = {}
    for p in g.pairs:
        a, b, edge, suppressed = p[0], p[1], p[2], p[3]
        pairs[_pair_key(a, b)] = (bool(suppressed), _edge_key(edge))
    return pairs, _edge_key(g.front)


def _room_at_front(rects, front, room_parts):
    if front is None:
        return None
    o, pos, s, e = front
    for r in rects:
        for p in room_parts(r):
            if o == "horizontal":
                if (abs(p["y"] - pos) < 1e-3 or abs(p["y"] + p["h"] - pos) < 1e-3) and abs(_r(p["x"]) - s) < 1e-3 and abs(_r(p["x"] + p["w"]) - e) < 1e-3:
                    return r["name"]
            else:
                if (abs(p["x"] - pos) < 1e-3 or abs(p["x"] + p["w"] - pos) < 1e-3) and abs(_r(p["y"]) - s) < 1e-3 and abs(_r(p["y"] + p["h"]) - e) < 1e-3:
                    return r["name"]
    return "?"


def compare(root: str, old_path: str, new_path: str, old_dg_path: str, report: str | None) -> None:
    _setup(root)
    from app.pipeline import door_graph as new_dg
    from app.pipeline.floor_layout import room_parts
    from app.pipeline.room_specs import classify_room_category

    old_dg = _load_old_door_graph(old_dg_path)
    with open(old_path, encoding="utf-8") as f:
        old = json.load(f)
    with open(new_path, encoding="utf-8") as f:
        new = json.load(f)
    lines = []
    out = lines.append
    for mode in ("mask_off", "rect", "mask_on"):
        keys = sorted(k for k in new if k.startswith(mode + ":"))
        same_rects = same_warn = 0
        art_diff = {a: [] for a in ("png", "dxf", "door_specs", "cond")}
        rule_hits: dict[str, list] = {}
        unexplained = []
        for k in keys:
            o, n = old.get(k), new[k]
            if o is None:
                out(f"MISSING in old: {k}")
                continue
            rects_eq = json.dumps(o["rects"], sort_keys=True) == json.dumps(n["rects"], sort_keys=True)
            same_rects += rects_eq
            same_warn += o["warnings"] == n["warnings"]
            diffs = [a for a in art_diff if o[a] != n[a]]
            for a in diffs:
                art_diff[a].append(k)
            if not diffs:
                continue
            if not rects_eq:
                rule_hits.setdefault("layout changed (rects differ)", []).append(f"{k} [{','.join(diffs)}]")
                continue
            rects, plot, facing, fl = n["rects"], n["plot"], n["facing"], n["floor"]
            explained = set()
            # PNG / shared rules: old default graph vs new default graph.
            op, ofront = _door_sets(old_dg, rects, plot, facing, fl)
            np_, nfront = _door_sets(new_dg, rects, plot, facing, fl)
            by_name = {r["name"]: r for r in rects}
            orphans = new_dg.orphaned_ensuites(rects) if any(r["name"] == "Hallway" for r in rects) else frozenset()
            for pk in sorted(set(op) | set(np_)):
                if op.get(pk) == np_.get(pk):
                    continue
                a, b = by_name[pk[0]], by_name[pk[1]]
                os_, ns_ = op.get(pk, (None,))[0], np_.get(pk, (None,))[0]
                if ns_ and not os_ and new_dg.should_suppress_bathroom_public_door(a, b):
                    rule = "common-bath rule (bath never opens into living/dining/kitchen/family)"
                elif os_ and not ns_ and (id(a) in orphans or id(b) in orphans):
                    rule = "EXTRA rule: orphaned ensuite treated as common bath (keeps hallway door)"
                else:
                    rule = "door change, other"
                rule_hits.setdefault(rule, []).append(f"{k}: {pk[0]}<->{pk[1]} suppressed {os_}->{ns_}")
                explained.update({"png", "dxf", "door_specs", "cond"})
            if ofront != nfront:
                orm = _room_at_front(rects, ofront, room_parts)
                nrm = _room_at_front(rects, nfront, room_parts)
                ocat = classify_room_category(orm or "") if orm else None
                if ocat == "garage":
                    rule = "garage-entrance rule (front door never in the garage)"
                elif ocat in ("bedroom", "bathroom", "powder"):
                    rule = "EXTRA rule: front door prefers a non-bedroom/bath room"
                else:
                    rule = "front door change, other"
                rule_hits.setdefault(rule, []).append(f"{k}: front {orm} -> {nrm}")
                explained.update({"png", "dxf", "door_specs", "cond"})
            # DXF-only: old DXF skipped garage-door suppression.
            if "dxf" in diffs:
                od, _ = _door_sets(old_dg, rects, plot, facing, fl, suppress_garage_doors=False)
                hit = False
                for pk in sorted(set(od) | set(np_)):
                    if od.get(pk) != np_.get(pk) and op.get(pk) == np_.get(pk):
                        a, b = by_name[pk[0]], by_name[pk[1]]
                        if new_dg.should_suppress_garage_direct_door(a, b):
                            rule_hits.setdefault("DXF garage parity (DXF now suppresses garage->living/kitchen/dining)", []).append(
                                f"{k}: {pk[0]}<->{pk[1]}")
                            hit = True
                if hit:
                    explained.add("dxf")
            # Concept Layout specs only: old used bbox-only shared_edge.
            if "door_specs" in diffs:
                ok_, _ = _door_sets(old_dg, rects, plot, facing, fl, parts_aware=False)
                hit = False
                for pk in sorted(set(ok_) | set(np_)):
                    if ok_.get(pk) != np_.get(pk) and op.get(pk) == np_.get(pk):
                        rule_hits.setdefault("kaggle parts-aware edge", []).append(
                            f"{k}: {pk[0]}<->{pk[1]} {ok_.get(pk)} -> {np_.get(pk)}")
                        hit = True
                if hit:
                    explained.add("door_specs")
            rest = [a for a in diffs if a not in explained]
            if rest:
                unexplained.append(f"{k} [{','.join(rest)}]")
        out(f"\n=== mode {mode}: {len(keys)} floors ===")
        out(f"rects identical: {same_rects}/{len(keys)}; warnings identical: {same_warn}/{len(keys)}")
        for a, ks in art_diff.items():
            out(f"  {a:<10} differs on {len(ks)} floors" + (": " + ", ".join(x.split(':', 1)[1] for x in ks) if ks else ""))
        for rule, hits in rule_hits.items():
            out(f"  RULE {rule}: {len(hits)}")
            for h in hits:
                out(f"      {h}")
        out(f"  unexplained artifact diffs: {len(unexplained)}" + ("".join("\n      " + u for u in unexplained)))
    text = "\n".join(lines)
    print(text)
    if report:
        with open(report, "w", encoding="utf-8") as f:
            f.write(text + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--write")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"))
    ap.add_argument("--old-door-graph")
    ap.add_argument("--report")
    a = ap.parse_args()
    if a.write:
        snapshot(a.root, a.write)
    elif a.compare:
        compare(a.root, a.compare[0], a.compare[1], a.old_door_graph, a.report)


if __name__ == "__main__":
    main()
