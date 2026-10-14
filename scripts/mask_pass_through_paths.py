"""Lists every room the planner-ON mask matrix still reaches only THROUGH a
bedroom/bathroom (door_graph.circulation_report's pass_through_private), with
the actual door path from the entry and every wall the room shares (door
kept, or suppressed and by which rule) - READ-ONLY diagnostic.

  python scripts/mask_pass_through_paths.py [--root <checkout>] [--out FILE]
"""
import argparse
import os
import sys
from collections import deque

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=HERE)
    ap.add_argument("--out", default=os.path.join(HERE, "scripts", "output", "quality", "pass_through_paths.txt"))
    a = ap.parse_args()
    sys.path.insert(0, os.path.abspath(a.root))
    sys.path.insert(0, os.path.join(os.path.abspath(a.root), "scripts"))
    from app.pipeline import door_graph as dg
    from app.pipeline.floor_layout import room_parts
    from app.pipeline.room_specs import classify_room_category
    from mask_quality_report import compute_all

    results = compute_all(True)
    lines = []
    o = lines.append
    total_rooms = 0
    floors_hit = 0
    for key, (case, fl, rects, _m, _o) in results.items():
        rep = dg.circulation_report(rects, case["plot"], case["facing"], fl)
        bad = rep["pass_through_private"]
        if rep["unreachable"]:
            o(f"{key}: UNREACHABLE {rep['unreachable']}")
        if not bad:
            continue
        floors_hit += 1
        total_rooms += len(bad)
        g = dg.build_door_graph(rects, case["plot"], case["facing"], fl)
        names = [r["name"] for r in rects]
        idx = {id(r): i for i, r in enumerate(rects)}
        adj = [set() for _ in rects]
        for x, y, _e in g.interior:
            adj[idx[id(x)]].add(idx[id(y)])
            adj[idx[id(y)]].add(idx[id(x)])
        root = names.index(rep["root"]) if rep["root"] in names else None
        has_hall = any(n == "Hallway" for n in names)
        hall_adj = dg.compute_hallway_adjacent(rects)
        orphans = dg.orphaned_ensuites(rects) if has_hall else frozenset()
        has_entry = any(classify_room_category(n) == "foyer" for n in names)
        o(f"\n{key}  (root {rep['root']} via {rep['root_how']}; facing {case['facing']})")
        for room in bad:
            t = names.index(room)
            prev = {root: None}
            q = deque([root])
            while q:
                u = q.popleft()
                for v in sorted(adj[u]):
                    if v not in prev:
                        prev[v] = u
                        q.append(v)
            path = []
            cur = t if t in prev else None
            while cur is not None:
                path.append(names[cur])
                cur = prev[cur]
            r = rects[t]
            ar = sum(p["w"] * p["h"] for p in room_parts(r))
            o(f"  PASS-THROUGH {room} ({classify_room_category(room)}, suite={r.get('suite')}, {ar:.0f} sq ft, "
              f"{len(room_parts(r))} part(s)): path {' -> '.join(reversed(path))}")
            for other in rects:
                if other is r:
                    continue
                e = dg.shared_edge_between_rooms(r, other)
                if e is None:
                    continue
                why = "door"
                if has_hall and dg.should_suppress_direct_door(r, other, hall_adj, orphans):
                    why = "SUPPRESSED (hallway rule: private<->private / ensuite-to-hall)"
                elif has_entry and dg.should_suppress_garage_direct_door(r, other):
                    why = "SUPPRESSED (garage rule)"
                elif dg.should_suppress_bathroom_public_door(r, other):
                    why = "SUPPRESSED (bath never into living/dining/kitchen/family)"
                o(f"      wall with {other['name']:<18} ({classify_room_category(other['name'])}, suite={other.get('suite')}) "
                  f"len {e['end'] - e['start']:.1f}: {why}")
            if "Hallway" in names:
                o(f"      touches Hallway: {dg.shared_edge_between_rooms(r, rects[names.index('Hallway')]) is not None}")
            else:
                o("      floor has NO Hallway")
    o(f"\nTOTAL pass-through rooms: {total_rooms} on {floors_hit} floors")
    text = "\n".join(lines)
    print(text)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
