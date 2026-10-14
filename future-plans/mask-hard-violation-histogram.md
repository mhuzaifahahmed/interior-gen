# Hard-violation histogram on the non-clean floors, and the top three blockers

Produced by `python scripts/mask_quality_report.py --scorecard --planner both --no-render --jobs 4`
(code: `app/pipeline/mask_scorecard.py` - `failure_events`, `violation_histogram`,
`top_blockers`, `greedy_blockers`, `format_violation_histogram`). Measured on the
35-floor quality matrix at the commit that introduced this doc; layouts are unchanged by it.

## Definitions (read before the numbers)

- **Clean** = the scorecard's own `clean_all`: passes all 8 scored checks (coverage, yards, sizes,
  hard, parts, single-floor stair, bedroom aspect, stair cap) AND circulation. Not a private definition.
- **Violation type** = every reason a floor fails: the layout/metrics hard violations
  (`narrow`, `door_too_narrow`, `small_area`, `foyer_shallow`, `degenerate`, `wrapped`,
  `coverage_gap`... whichever occur), `no_daylight`, `oversized`, `hierarchy_inverted`,
  `bedroom_aspect`, `stair_oversized`, and the circulation kinds (`unreachable`,
  `pass_through_private`, `entry_is_garage`, `entry_missing`) - class **HARD** - plus the scorecard
  checks that are not themselves "violations" (`coverage`, `yards`, `too_many_parts`,
  `bath_over_cap`, `dining_over_cap`, `pinned_over_1.5x`, `single_floor_stair`) - class **CHECK**.
  They are listed because a floor is not clean while they fail; omitting them would make "newly clean"
  meaningless. Only kinds that actually occur are printed.
- **violations** = total count; **floors** = distinct floors affected.
- **newly clean if fixed** = number of floors whose ONLY failure type is this one (eliminate that type
  alone and the floor becomes clean_all). TOP 3 BLOCKERS are sorted by that number (ties: more floors
  affected). The "Cumulative (greedy)" line fixes types together, since single-type elimination quickly
  runs out of floors (most floors fail on several types at once).
- Room-category breakdown uses `room_specs.classify_room_category`; "(floor)" = floor-level event.

## Planner ON (house_planner_enabled=True; the flag stays OFF by default)

```
=== HARD-VIOLATION HISTOGRAM [planner_on] ===
floors scored 35; NOT fully clean (clean_all: all 8 scored checks + circulation): 33
class HARD = layout/metrics hard violation; CHECK = scorecard-only check (coverage, yards, parts, size caps, single-floor stair)
type                  class   violations  floors  newly clean if fixed
narrow                HARD            29      22                     3
coverage              CHECK           19      19                     2
oversized             HARD            22      15                     0
too_many_parts        CHECK           15       9                     0
bedroom_aspect        HARD             7       7                     0
door_too_narrow       HARD             5       5                     0
dining_over_cap       CHECK            4       4                     0
pass_through_private  HARD             4       4                     0
pinned_over_1.5x      CHECK            4       4                     0
stair_oversized       HARD             4       4                     0
foyer_shallow         HARD             1       1                     0
hierarchy_inverted    HARD             1       1                     0

TOP 3 BLOCKERS (types whose elimination alone makes the most additional floors fully clean):
  1. narrow: +3 newly clean floors (22 floors / 29 violations affected) - only-this-type floors: 30x40_small_1f/floor1, 50x50_typical_1f/floor1, 56x56_typical_1f/floor1
       by room category: bedroom 8, bathroom 6, dining 5, living 4, study 3, laundry 2, kitchen 1
  2. coverage: +2 newly clean floors (19 floors / 19 violations affected) - only-this-type floors: 150x150_sparse_1f/floor1, 300x300_sparse_1f/floor1
       by room category: (floor) 19
  3. oversized: +0 newly clean floors (15 floors / 22 violations affected)
       by room category: bedroom 12, bathroom 6, living 3, dining 1
Cumulative (greedy; fixing these types TOGETHER): +narrow -> 3 floors clean; +coverage -> 7 floors clean; +oversized -> 10 floors clean
```

## Planner OFF (default)

```
=== HARD-VIOLATION HISTOGRAM [planner_off] ===
floors scored 35; NOT fully clean (clean_all: all 8 scored checks + circulation): 35
class HARD = layout/metrics hard violation; CHECK = scorecard-only check (coverage, yards, parts, size caps, single-floor stair)
type                  class   violations  floors  newly clean if fixed
too_many_parts        CHECK           84      34                     0
narrow                HARD            85      31                     0
oversized             HARD            49      29                     0
coverage              CHECK           22      22                     0
pass_through_private  HARD            41      18                     0
bedroom_aspect        HARD            13      12                     0
yards                 CHECK           12      12                     0
dining_over_cap       CHECK           11      11                     0
hierarchy_inverted    HARD             8       8                     0
door_too_narrow       HARD             7       7                     0
no_daylight           HARD             2       2                     0
bath_over_cap         CHECK            1       1                     0

TOP 3 BLOCKERS (types whose elimination alone makes the most additional floors fully clean):
  1. too_many_parts: +0 newly clean floors (34 floors / 84 violations affected)
       by room category: bedroom 61, living 12, dining 8, staircase 2, study 1
  2. narrow: +0 newly clean floors (31 floors / 85 violations affected)
       by room category: bedroom 50, living 20, dining 8, bathroom 5, study 2
  3. oversized: +0 newly clean floors (29 floors / 49 violations affected)
       by room category: bedroom 32, dining 6, foyer 6, kitchen 4, bathroom 1
Cumulative (greedy; fixing these types TOGETHER): +too_many_parts -> 0 floors clean; +narrow -> 0 floors clean; +bedroom_aspect -> 1 floors clean
```

## Reading it honestly

- With the planner ON, 33 of 35 floors are not clean. No single type clears many floors: the best is
  `narrow` (+3), then `coverage` (+2); the third place (`oversized`) is +0 on its own - it is only
  chosen by floors affected. Greedy cumulative: narrow -> 3 clean, + coverage -> 7, + oversized -> 10.
- `narrow` (mostly bedrooms, then bathrooms, dining, living) is the dominant hard blocker; the
  `coverage` blocker is entirely big-plot floors (a CHECK, not a layout violation).
- With the planner OFF every floor fails something and fixing any single type clears 0 floors (greedy cumulative reaches only 1 clean floor after three types).
