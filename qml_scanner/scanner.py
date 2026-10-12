"""One scan cycle (pure logic, no network).

Input : base M5 history per symbol ({symbol: [(t, o, h, l, c), ...]}, closed candles only) and the saved state.
Output: opportunities (one per symbol/side/price area, clearest timeframe), events to send, new state, pinned text.
M5 is only the stored base: it is not scanned (config.TIMEFRAMES starts at M15).
The data layer (Deriv / fallback source) and the Telegram layer plug in around this function.
"""
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence

from . import config
from .candles import Candle, finest, frames_for
from . import learning, overrides
from .dismiss import apply_dismissals, prune
from .engine import Setup, scan_series
from .manual import manual_setups
from .notify import Event, plan_notifications
from .qml_core import Params
from .report import format_alert, format_pinned
from .selection import Opportunity, ScoreWeights, cluster, correlation_tags

# Separate Telegram alerts: only "price in the zone". New / approaching / back / closed opportunities are no longer notified:
# they appear in the pinned list, which is still edited at every pass.
ALERT_KINDS = ("entered",)


def _alert_event(e: Event) -> Optional[Event]:
    """The event to announce on Telegram, or None. An opportunity first seen with the price already in the zone is announced as 'in the zone'."""
    if e.kind == "new" and e.opp is not None and e.opp.primary.status == "entered":
        return replace(e, kind="entered")
    return e if e.kind in ALERT_KINDS else None


@dataclass
class CycleResult:
    opportunities: List[Opportunity]
    events: List[Event]
    alerts: List[str]
    pinned: List[str]
    state: Dict
    all_setups: List[Setup]


def scan_symbol(symbol: str, entry, params_by_tf: Optional[Dict[str, Params]] = None,
                tfs: Sequence[str] = config.TIMEFRAMES, window: int = config.WINDOW,
                approach_atr: float = config.APPROACH_ATR, last_price: float = None) -> List[Setup]:
    """`entry` = plain M5 list or {"M5": [...], "H1": [...]} (see candles.frames_for)."""
    base = finest(entry)
    if not base:
        return []
    price = base[-1][4] if last_price is None else last_price
    out: List[Setup] = []
    for tf in tfs:
        frames = frames_for(entry, tf, window)
        p = (params_by_tf or {}).get(tf)
        out.extend(scan_series(symbol, tf, frames, p, approach_atr, price))
    return out


def run_cycle(history: Dict[str, Sequence[Candle]], state: Dict, now_epoch: int,
              params_by_tf: Optional[Dict[str, Params]] = None, last_prices: Optional[Dict[str, float]] = None,
              tfs: Sequence[str] = config.TIMEFRAMES, window: int = config.WINDOW,
              approach_atr: float = config.APPROACH_ATR, tol_atr: float = config.CLUSTER_TOL_ATR,
              weights: ScoreWeights = ScoreWeights(), grace: int = 2) -> CycleResult:
    """Order: scan (with the parameters learned with Harold) -> filters -> his own proposed setups -> his adjusted levels ->
    his deletions -> clustering -> notifications -> ledger of what was notified."""
    state.setdefault("clusters", {})
    state.setdefault("dismissed", [])
    state.setdefault("feedback", [])
    state.setdefault("manual", [])
    state.setdefault("overrides", [])
    state.setdefault("ledger", [])
    state.setdefault("params", {"global": {}, "filters": {}})
    prune(state, now_epoch)
    overrides.prune(state, now_epoch)
    if params_by_tf is None:
        learned = learning.params_from_state(state)
        params_by_tf = {tf: learned for tf in tfs}
    all_setups: List[Setup] = []
    for symbol in sorted(history):
        lp = (last_prices or {}).get(symbol)
        all_setups.extend(scan_symbol(symbol, history[symbol], params_by_tf, tfs, window, approach_atr, lp))
    all_setups = learning.apply_filters(all_setups, state["params"].get("filters", {}))
    all_setups.extend(manual_setups(state, history, last_prices, approach_atr, window))
    all_setups = overrides.apply_overrides(all_setups, state, history, last_prices, approach_atr)
    visible = apply_dismissals(all_setups, state, tol_atr)
    opps = cluster(visible, tol_atr, weights)
    correlation_tags(opps)
    events, new_state = plan_notifications(opps, visible, state, grace)
    for e in events:
        if e.kind == "new" and e.opp is not None:
            learning.record_new(new_state, e.opp, now_epoch)
    pinned = format_pinned(opps, now_epoch)
    alerts = [format_alert(a) for a in map(_alert_event, events) if a is not None]        # the other events only show up in the pinned list
    return CycleResult(opps, events, alerts, pinned, new_state, all_setups)
