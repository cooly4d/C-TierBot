"""Server-side chart geometry for the website, so every chart is complete without JavaScript.

The trend chart draws in a 1000 x 300 viewBox stretched to its box (`preserveAspectRatio="none"`): lines use
non-scaling strokes, and dots are zero-length round-capped strokes, which stay round at any aspect. Slot `i` of `n`
sits at x = (i + 0.5) / n, the centre of column `i` of an `n`-column CSS grid, so the HTML labels and hit targets
line up with the points without any inline style.
"""
import math

W, H = 1000.0, 300.0
METRICS = ("damage", "impact", "kills")
METRIC_UNITS = {"damage": "avg dmg", "impact": "avg impact", "kills": "kills per game"}
METRIC_TITLES = {"damage": "AVG DAMAGE TREND", "impact": "AVERAGE IMPACT TREND (0–100)", "kills": "KILLS PER GAME TREND"}
METRIC_EMPTY = {
    "damage": "No damage numbers in these queues.",
    "impact": "No impact scores in these queues.",
    "kills": "No kill counts in these queues.",
}
# Three gridlines at the bottom, middle and top of the plot, on round values that always contain every point.
TICK_FRACTIONS = (0.0, 0.5, 1.0)


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def format_value(metric: str, value) -> str:
    if value is None:
        return "—"
    if metric == "damage":
        return format(float(value), ",.0f")  # as the cards and /leaderboard_season
    if metric == "impact":
        return str(round_half_up(value))
    return f"{value:.1f}"


def game_count(row: dict):
    """The player's own game count for a queue row: `games`, else the legacy inference
    round(total_damage / avg_damage) when avg_damage > 0 (as in db.get_seasonal_queue_stats). None when unknown."""
    games = row.get("games")
    if isinstance(games, int) and not isinstance(games, bool):
        return games if games > 0 else None
    avg = row.get("avg_damage")
    total = row.get("total_damage")
    if games is None and isinstance(avg, (int, float)) and avg > 0 and isinstance(total, (int, float)):
        inferred = round(total / avg)
        return inferred if inferred > 0 else None
    return None


def trend_value(row: dict, metric: str):
    """The one value a queue contributes to the trend chart, its tooltip, its label and the table: avg dmg, avg
    impact, or kills per game (queue kills / that player's own game count). None (a missing point, never 0) when
    the number or its denominator is unknown."""
    if metric == "damage":
        avg = row.get("avg_damage")
        return float(avg) if isinstance(avg, (int, float)) and game_count(row) is not None else None
    if metric == "impact":
        impact = row.get("impact")
        return float(impact) if isinstance(impact, (int, float)) and not isinstance(impact, bool) else None
    if metric == "kills":
        kills = row.get("kills")
        games = game_count(row)
        if not isinstance(kills, (int, float)) or isinstance(kills, bool) or games is None:
            return None
        return kills / games
    raise ValueError(metric)


def nice_step(raw: float) -> float:
    """The smallest of 1, 2, 2.5 or 5 times a power of ten that is at least `raw`."""
    if raw <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * exp >= raw - 1e-12:
            return m * exp
    return 10 * exp


def _domain(values: list[float], metric: str = "damage") -> tuple[float, float]:
    """[base, base + 2 * step] on round values containing every point; ticks sit at base, middle and top."""
    lo, hi = min(values), max(values)
    minimum = 0.5 if metric == "kills" else 1.0
    step = nice_step(max((hi - lo) / 2, minimum))
    for _ in range(40):
        base = math.floor(lo / step) * step if hi > lo else math.floor(lo / step) * step - step  # equal: centred
        if base + 2 * step >= hi - 1e-9:
            return max(0.0, base), max(0.0, base) + 2 * step
        step = nice_step(step * 1.0001)
    return lo, hi


def trend_geometry(points: list[dict], metric: str) -> dict:
    """Geometry for one metric. `points` are oldest first, each {"match_id", "won", "row"}.

    Returns slots (one per queue, with y or None), line and area paths broken at missing points, tick labels,
    the latest value and its slot (`latest_i`, the newest queue that has a value), and `empty` when no queue has
    a value."""
    n = len(points)
    values = [trend_value(p["row"], metric) for p in points]
    present = [v for v in values if v is not None]
    slots = []
    for i, p in enumerate(points):
        slots.append({
            "i": i,
            "match_id": p["match_id"],  # None for a stored number the site can't show
            "won": p.get("won"),
            "score": p.get("score") or "",
            "x": round((i + 0.5) / n * W, 2) if n else 0,
            "y": None,
            "value": values[i],
            "text": format_value(metric, values[i]),
        })
    if not present:
        return {"metric": metric, "empty": True, "slots": slots, "line": "", "area": "", "ticks": [],
                "latest": None, "latest_i": None, "unit": METRIC_UNITS[metric], "title": METRIC_TITLES[metric],
                "empty_text": METRIC_EMPTY[metric]}
    lo, hi = _domain(present, metric)
    for slot in slots:
        if slot["value"] is not None:
            slot["y"] = round(H - (slot["value"] - lo) / (hi - lo) * H, 2)
    segments, current = [], []
    for slot in slots:
        if slot["y"] is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(slot)
    if current:
        segments.append(current)
    line = " ".join(
        "M" + " L".join(f"{s['x']} {s['y']}" for s in seg) for seg in segments if len(seg) >= 2
    )
    area = " ".join(
        "M" + " L".join(f"{s['x']} {s['y']}" for s in seg) + f" L{seg[-1]['x']} {H} L{seg[0]['x']} {H} Z"
        for seg in segments if len(seg) >= 2
    )
    ticks = [{"fraction": f, "label": format_value(metric, lo + (hi - lo) * f), "y": round(H - f * H, 2)}
             for f in TICK_FRACTIONS]
    latest = next((s for s in reversed(slots) if s["value"] is not None), None)
    return {
        "metric": metric, "empty": False, "slots": slots, "line": line, "area": area, "ticks": ticks,
        "latest": latest["text"] if latest else None, "latest_i": latest["i"] if latest else None,
        "unit": METRIC_UNITS[metric], "title": METRIC_TITLES[metric],
        "empty_text": METRIC_EMPTY[metric],
    }


def trend_chart(points: list[dict]) -> dict:
    """Geometry for every metric (the page shows one; script switches between them without a reload)."""
    return {metric: trend_geometry(points, metric) for metric in METRICS}


def bar_fractions(values: list) -> list[float]:
    """Each value as a fraction of the top value; a top value of 0 (or none) draws no bars."""
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    top = max(nums) if nums else 0
    if top <= 0:
        return [0.0 for _ in values]
    return [max(0.0, min(1.0, v / top)) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
            for v in values]
