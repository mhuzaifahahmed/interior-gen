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
"""

MIN_WEIGHT = 0.01


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
