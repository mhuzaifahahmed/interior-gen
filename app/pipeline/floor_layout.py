"""Pure, deterministic room-rectangle layout for one floor - no I/O, no model
calls, nothing to mock in tests. Takes Gemini's free-text room list
(GeminiProvider.generate_room_layout) and slices the plot into non-overlapping
room rectangles that exactly tile it.

Room "area" values from Gemini are treated as RELATIVE WEIGHTS, not literal
square footage - the layout always rescales to exactly fill the plot's real
length x width regardless of what absolute numbers the model returned, so the
algorithm never needs those numbers to sum to anything in particular.

Algorithm: simple recursive slicing (a "slice and dice" treemap, not the more
complex squarified-treemap variant). At each step the room list is split into
two roughly weight-balanced halves and the box is cut along its LONGER side -
this keeps resulting rectangles closer to square than naive fixed-axis
alternation would, without the bookkeeping of a true squarified layout, which
is unnecessary complexity for a v1 concept blueprint.

PUBLIC/PRIVATE ZONE GROUPING (added after real user feedback: "no sense of
living room being in the middle" - a living room could land dead-center of
the plot purely by coincidence of the weight-balanced split, since the
original algorithm had zero notion of real-world room-placement conventions).
Fix: `_slice()` itself is UNCHANGED, still pure area-weighted recursive
splitting - only the ORDER of the room list fed into it changed.
`_slice()` always splits a CONTIGUOUS sublist into two halves, so the input
order directly determines which rooms end up spatially grouped together.
`layout_floor()` now does one STABLE sort (Python's `sorted()` preserves
relative order within equal keys) grouping every room into a "public zone"
(garage, entry/foyer, living/lounge/family/drawing, dining, kitchen, powder/
guest bath, study/office - the keyword list mirrors blueprint_svg.py's own
furniture-dispatch vocabulary for consistency) ahead of everything else (the
"private zone" - bedrooms, bathrooms, hallways, storage, utility, anything
unrecognized). This guarantees public rooms cluster together and private
rooms cluster together as two contiguous regions of the plot, without
disturbing whatever fine-grained order Gemini/fallback already gave rooms
WITHIN a zone (e.g. a "Master Bedroom" immediately followed by "Master
Bathroom" in the input list stays adjacent - only the coarse public-vs-
private split is enforced, not a full per-room adjacency solver, which is a
much harder problem deliberately still out of scope - see CLAUDE.md).
"""

MIN_WEIGHT = 0.01

# Same category vocabulary as blueprint_svg.py's furniture dispatcher, reused
# here for consistency - a room matching any of these keywords is "public"
# (front-of-house / shared spaces); everything else (bedrooms, bathrooms,
# hallways, storage, utility, unrecognized names) is "private".
_PUBLIC_ZONE_KEYWORDS = (
    "garage",
    "entry", "foyer", "lobby",
    "living", "lounge", "family", "drawing",
    "dining",
    "kitchen",
    "powder", "guest bath", "guest wc", "guest toilet",
    "study", "office",
)


def _zone_key(room_name: str) -> int:
    name = room_name.lower()
    return 0 if any(keyword in name for keyword in _PUBLIC_ZONE_KEYWORDS) else 1


def layout_floor(rooms: list[dict], dimensions: dict) -> list[dict]:
    """rooms: [{"name": str, "area": number}, ...] (area is a relative weight).
    dimensions: {"length": float, "width": float, "unit": str}.
    Returns [{"name": str, "x": float, "y": float, "w": float, "h": float}, ...]
    in the same unit as dimensions, tiling exactly [0, length] x [0, width].
    """
    if not rooms:
        return []

    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)

    # Stable sort into public-then-private zones (see module docstring) -
    # preserves each zone's own internal relative order.
    rooms = sorted(rooms, key=lambda r: _zone_key(str(r.get("name") or "")))

    names = [str(r.get("name") or f"Room {i + 1}") for i, r in enumerate(rooms)]
    weights = [max(float(r.get("area") or 0), MIN_WEIGHT) for r in rooms]

    return _slice(names, weights, 0.0, 0.0, length, width, sum(weights))


def _slice(
    names: list[str], weights: list[float], x: float, y: float, w: float, h: float, total_weight: float
) -> list[dict]:
    if len(names) == 1:
        return [{"name": names[0], "x": x, "y": y, "w": w, "h": h}]

    split_index = _balanced_split_index(weights)
    left_names, right_names = names[:split_index], names[split_index:]
    left_weights, right_weights = weights[:split_index], weights[split_index:]
    left_total = sum(left_weights)
    right_total = sum(right_weights)
    left_fraction = left_total / total_weight if total_weight else 0.5

    if w >= h:
        left_w = w * left_fraction
        return _slice(left_names, left_weights, x, y, left_w, h, left_total) + _slice(
            right_names, right_weights, x + left_w, y, w - left_w, h, right_total
        )
    else:
        left_h = h * left_fraction
        return _slice(left_names, left_weights, x, y, w, left_h, left_total) + _slice(
            right_names, right_weights, x, y + left_h, w, h - left_h, right_total
        )


def _balanced_split_index(weights: list[float]) -> int:
    """Return the split index (1..len-1) whose left-side weight sum is closest
    to half of the total - keeps the two resulting groups as evenly sized as
    possible regardless of room order."""
    total = sum(weights)
    half = total / 2
    running = 0.0
    best_index = 1
    best_diff = None
    for i in range(1, len(weights)):
        running = sum(weights[:i])
        diff = abs(running - half)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_index = i
    return best_index
