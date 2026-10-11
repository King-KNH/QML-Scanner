"""Opportunities removed by Harold after his visual check.

A dismissal stores the member setup keys and the price interval of the opportunity. From then on:
 - the same setups never come back,
 - an older setup of the same symbol/side overlapping that interval (it was already there when he dismissed) stays hidden,
 - a setup whose detection candle CLOSED after the dismissal is new information and is allowed through.
Every dismissal is also written to state["feedback"] (full snapshot + his reason) to tune the rules later.
"""
from dataclasses import asdict
from typing import Dict, List, Optional, Sequence

from .candles import TF_SECONDS
from .engine import Setup
from .selection import Opportunity, interval

TTL_DAYS = 30
FEEDBACK_MAX = 500


def _ensure(state: Dict) -> None:
    state.setdefault("clusters", {})
    state.setdefault("dismissed", [])
    state.setdefault("feedback", [])


def apply_dismissals(setups: Sequence[Setup], state: Dict, tol_atr: float = 0.25) -> List[Setup]:
    """Setups that are not covered by a dismissal."""
    ds = state.get("dismissed", [])
    if not ds:
        return list(setups)
    by = {}
    for d in ds:
        by.setdefault((d["symbol"], d["side"]), []).append(d)
    out = []
    for s in setups:
        hidden = False
        for d in by.get((s.symbol, s.side), []):
            if s.key in d["members"]:
                hidden = True
                break
            if s.t_detect + TF_SECONDS.get(s.tf, 0) <= d["t"]:
                lo, hi = interval(s)
                tol = tol_atr * s.atr
                if lo - tol <= d["hi"] and d["lo"] - tol <= hi:
                    hidden = True
                    break
        if not hidden:
            out.append(s)
    return out


def dismiss_opportunity(state: Dict, opp: Opportunity, now: int, reason: str = "") -> Dict:
    """Record the dismissal and drop the opportunity from the live clusters. Returns the stored record."""
    _ensure(state)
    members = set(opp.keys)
    cl = state["clusters"].get(opp.cid)
    if cl:
        members |= set(cl["members"])
    los, his = zip(*[interval(s) for s in [opp.primary] + opp.confluence])
    rec = {"id": opp.id, "cid": opp.cid, "symbol": opp.symbol, "side": opp.side, "tf": opp.primary.tf,
           "members": sorted(members), "lo": min(los), "hi": max(his), "t": int(now), "reason": reason}
    state["dismissed"] = [d for d in state["dismissed"] if d["id"] != rec["id"]] + [rec]
    state["clusters"].pop(opp.cid, None)
    state["feedback"].append({"type": "dismiss", "t": int(now), "id": opp.id, "reason": reason,
                              "setup": asdict(opp.primary), "confluence": [s.tf for s in opp.confluence]})
    del state["feedback"][:-FEEDBACK_MAX]
    return rec


def restore(state: Dict, ident: str, now: int) -> Optional[Dict]:
    """Undo a dismissal. The opportunity reappears at the next scan (as a new one)."""
    _ensure(state)
    for d in state["dismissed"]:
        if d["id"].lower() == ident.lower():
            state["dismissed"] = [x for x in state["dismissed"] if x is not d]
            state["feedback"].append({"type": "restore", "t": int(now), "id": d["id"], "symbol": d["symbol"], "side": d["side"]})
            del state["feedback"][:-FEEDBACK_MAX]
            return d
    return None


def prune(state: Dict, now: int, ttl_days: int = TTL_DAYS) -> None:
    _ensure(state)
    limit = now - ttl_days * 86400
    state["dismissed"] = [d for d in state["dismissed"] if d["t"] >= limit]
