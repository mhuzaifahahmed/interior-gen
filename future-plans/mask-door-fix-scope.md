# Scope check: did the Step 3 door fixes (de11b90) change anything but doors?

Question from the user: the DXF and Concept Layout door fixes were accepted, but
confirm ONLY DOORS changed (no geometry changes) on the floors whose DXF /
Concept Layout output differs between a3d5fc8 (before) and de11b90 (after).

## Method

Two clean `git worktree` checkouts (a3d5fc8, de11b90). For the whole 35-floor
quality matrix in BOTH planner modes (70 floors), each worktree computed the
rects, the full canonical DXF entity list (every entity as
`TYPE|LAYER|rounded geometry`), the full Concept Layout door-spec list
(`kaggle_autocad._door_specs_for_floor`) and the conditioning-image sha256.
Floors were compared only when their rects are byte-identical between the two
commits. DXF differences were diffed as entity multisets and grouped by layer.

## Result

- Rect-changed floors (excluded, expected to differ everywhere): **7** of 70, all
  planner-on (150x150_dense_garage_2f/floor2, 60x80_3f_east/floor3,
  60x80_3f_north/floor2+3, 60x80_3f_south/floor2+3, 60x80_3f_west/floor3).
  Flag-off: 0 of 35 rect-changed.
- Comparable floors: 63. DXF differs on **19** of them (8 flag-off, 11 planner-on);
  Concept Layout differs on **6** (3 flag-off, 3 planner-on; table below).
  (The user's "8 DXF and 2 CL" matches the flag-off numbers: 8 DXF floors; 2 floors
  with an added door spec plus a third with a relocated front door.)

### DXF: layers that changed

| Layers changed | Floors |
|---|---|
| DOORS + WALLS only | 18 |
| DOORS + WALLS + WINDOWS | 1 (off:90x40_sparse_1f/floor1) |
| any other layer (ROOMS, TEXT, furniture, yard, driveway, ...) | 0 |

For the 18 DOORS+WALLS floors the change is exactly "a door that used to be
silently dropped now exists": one-or-more new door swing arc + leaf on the DOORS
layer, and the wall polyline that passed through that spot is split so there is a
gap. Checked numerically: for every one of them, (wall length removed - wall
length added) equals the sum of the new door-arc radii (= door widths) to 3
decimals (e.g. 150x150_dense_garage_2f/floor1 flag-off: 2.483 = 2.483; 40x60
typical east: 1.719 = 1.719). No door was removed on any of these 18. No room
outline, text label, window, or wall outside the door gap moved. Rects identical.

### The one non-door change: off:90x40_sparse_1f/floor1

This is the intended front-door priority change, not a geometry change: the
ENTRANCE moved from the Kitchen (old, hard-coded choice) to the Living Room
(new priority list). Consequences, all derived from that single move:

- front door arc + leaf moved (DOORS): x 52.83 -> 32.14 (same radius 2.953)
- the exterior wall polylines were re-split around the moved door gap and the
  moved window gap (WALLS, 8 entities replaced); total wall length differs by
  the difference between the old and new door+window gap widths
- one WINDOWS line moved: the window that used to sit on the Living Room wall at
  x 29.90-37.34 is gone (the door is there now) and a window now sits where the
  door used to be (x 52.34-56.28). Windows are placed on exterior walls minus the
  door, so they follow the door.

Rects identical; room outlines/text/furniture unchanged. The conditioning image
for this floor changes too (its entrance gap moved), which is the same cause.

### Concept Layout (door specs + conditioning image)

| Floor | Door specs | Conditioning image |
|---|---|---|
| off:150x150_dense_garage_2f/floor1 | +1 door spec (27 -> 28), nothing else | identical |
| off:150x200_2f_reported/floor1 | +1 door spec (14 -> 15) | identical |
| off:90x40_sparse_1f/floor1 | ENTRANCE spec moved (same count 4 -> 4: one removed + one added, label ENTRANCE) | changed (entrance gap moved) |
| on:150x150_dense_garage_2f/floor1 | +1 door spec (23 -> 24) | identical |
| on:150x200_2f_reported/floor1 | +1 door spec (12 -> 13) | identical |
| on:300x300_typical_1f/floor1 | +1 door spec (18 -> 19) | identical |

So there are 6 Concept Layout floors that differ (3 flag-off, 3 planner-on), not
2: 5 are a pure added door spec, 1 is the front-door relocation. Only door specs
(and, for the relocated entrance, the conditioning image's entrance gap)
changed; no label/geometry payload field changed.

## Conclusion

Confined to doors. 18/19 DXF floors and 5/6 Concept Layout floors are purely
"missing door now present" (DOORS layer + the matching wall gap). The one
exception is off:90x40_sparse_1f/floor1, where the front door moved to a different
room and the exterior window (plus conditioning-image entrance gap) followed it -
intended consequence of the front-door priority list, not a geometry change.
No room/wall-outline/text/furniture/yard/driveway layer differed on any comparable
floor.

## Note on counts

The user's "8 DXF and 2 CL" corresponds to the flag-off run (8 DXF floors; 2 floors
with an added door spec). Two further numbers appear here that were not in that
summary: the planner-on mode (11 more DXF floors, 3 more CL floors) and the
relocated front door on 90x40_sparse (a third flag-off CL floor).
