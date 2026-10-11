"""Zone / line adjustments made by Harold on an opportunity the robot found (« ajuste A3F7 zone 4190 4199 »).

The adjustment is attached to ONE setup (symbol|timeframe|side|left-shoulder time). From then on that setup is displayed
and followed (approach / entry / invalidation) with Harold's levels. The original levels are kept, and every adjustment is
written to state["feedback"] so the learning study can see how he systematically corrects the robot.
"""
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from .candles import finest
from .engine import Setup

FEEDBACK_MAX = 500


def _ensure(state: Dict) -> None:
    state.setdefault("overrides", [])
    state.setdefault("feedback", [])


def find_override(state: Dict, key: str) -> Optional[Dict]:
    for o in state.get("overrides", []):
        if o["key"] == key:
            return o
    return None


def adjust(state: Dict, setup: Setup, opp_id: str, now: int, zone: Optional[Tuple[float, float]] = None,
           entry: Optional[float] = None) -> Dict:
    """Store (or update) the adjustment of `setup`. `setup` must be the ORIGINAL robot setup (not already adjusted)."""
    _ensure(state)
    prev = find_override(state, setup.key)
    orig = prev["original"] if prev else {"zone_low": setup.zone_low, "zone_high": setup.zone_high, "entry": setup.entry}
    cur_lo = prev["zone_low"] if prev else setup.zone_low
    cur_hi = prev["zone_high"] if prev else setup.zone_high
    cur_en = prev["entry"] if prev else setup.entry
    lo, hi = (min(zone), max(zone)) if zone else (cur_lo, cur_hi)
    en = entry if entry is not None else cur_en
    rec = {"key": setup.key, "id": opp_id, "zone_low": lo, "zone_high": hi, "entry": en, "t": int(now), "original": orig}
    state["overrides"] = [o for o in state["overrides"] if o["key"] != setup.key] + [rec]
    state["feedback"].append({
        "type": "modify", "t": int(now), "id": opp_id, "symbol": setup.symbol, "side": setup.side, "tf": setup.tf,
        "kind": setup.zone_kind, "atr": setup.atr, "orig": dict(orig), "new": {"zone_low": lo, "zone_high": hi, "entry": en},
        "zone_candle": list(setup.zone_candle)})
    del state["feedback"][:-FEEDBACK_MAX]
    return rec


def reassess(s: Setup, base: Sequence[Tuple], last_price: Optional[float], t_since: int, approach_atr: float) -> Setup:
    """Recompute status / distance of a setup whose levels were adjusted (price extremes since the adjustment)."""
    d = 1 if s.side == "sell" else -1
    entry_t = d * s.entry
    price = last_price if last_price is not None else (base[-1][4] if base else s.last_price)
    price_t = d * price
    top_t = max(d * s.zone_low, d * s.zone_high)
    since = [c for c in base if c[0] >= t_since]
    touched = price_t >= entry_t or any((c[2] if d == 1 else -c[3]) >= entry_t for c in since)
    closed_beyond = any(d * c[4] > max(top_t, entry_t) for c in since)
    dist = entry_t - price_t
    near = dist <= (approach_atr * s.atr if s.atr > 0 else 0.0)
    if s.status in ("invalid", "expired") or closed_beyond:
        status = "invalid" if s.status != "expired" else "expired"
    elif touched:
        status = "entered"
    elif near:
        status = "approaching"
    else:
        status = "detected"
    return replace(s, status=status, distance=dist, last_price=price, adjusted=True)


def apply_overrides(setups: Sequence[Setup], state: Dict, history: Dict[str, Sequence], last_prices: Optional[Dict[str, float]],
                    approach_atr: float) -> List[Setup]:
    ovs = {o["key"]: o for o in state.get("overrides", [])}
    if not ovs:
        return list(setups)
    out: List[Setup] = []
    for s in setups:
        o = ovs.get(s.key)
        if o is None:
            out.append(s)
            continue
        mod = replace(s, zone_low=o["zone_low"], zone_high=o["zone_high"], entry=o["entry"])
        out.append(reassess(mod, finest(history.get(s.symbol, [])), (last_prices or {}).get(s.symbol), o["t"], approach_atr))
    return out


def prune(state: Dict, now: int, ttl_days: int = 30) -> None:
    """Forget old adjustments (they stay in the feedback log)."""
    limit = now - ttl_days * 86400
    state["overrides"] = [o for o in state.get("overrides", []) if o["t"] >= limit]
