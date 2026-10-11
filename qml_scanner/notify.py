"""Notification planning (pure functions, no I/O).

State is a JSON-serialisable dict:
  {"clusters":  {cid: {"id", "members": [setup keys], "status", "symbol", "side", "primary", "missed"}},
   "dismissed": [...],   # managed by dismiss.py (opportunities Harold removed)
   "feedback":  [...]}   # managed by dismiss.py (log of his corrections, for later tuning)

Events: new (first time an opportunity is seen), approaching, entered (status only moves forward),
returned (an opportunity that had moved away comes back near the line), closed (reason "invalidated" or "expired").
An opportunity whose price moved away ("parked") is silently erased from the list but kept in the state: no "closed" event.
An opportunity keeps its identity when its primary timeframe changes, because identity follows the member setups
(symbol|tf|side|left-shoulder time), not the chosen timeframe. Splits and merges are resolved deterministically.
"""
import copy
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .engine import STATUS_RANK, Setup
from .selection import Opportunity

_ID_CHARS = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"     # no I / O (confusable with 1 / 0)
_RANK_STATUS = {v: k for k, v in STATUS_RANK.items()}


@dataclass
class Event:
    kind: str                       # new | approaching | entered | returned | closed
    cid: str
    symbol: str
    side: str
    opp: Optional[Opportunity] = None
    reason: str = ""                # for closed: invalidated | expired
    primary_changed: bool = False
    id: str = ""


def empty_state() -> Dict:
    return {"clusters": {}, "dismissed": [], "feedback": []}


def short_id(seed: str, salt: int = 0) -> str:
    n = zlib.crc32(("%s#%d" % (seed, salt)).encode("utf-8"))
    out = ""
    for _ in range(4):
        out += _ID_CHARS[n % len(_ID_CHARS)]
        n //= len(_ID_CHARS)
    return out


def new_id(seed: str, used: Set[str]) -> str:
    salt = 0
    while True:
        i = short_id(seed, salt)
        if i not in used:
            return i
        salt += 1


def plan_notifications(opps: Sequence[Opportunity], all_setups: Sequence[Setup], state: Dict, grace: int = 2) -> Tuple[List[Event], Dict]:
    """Compare the current opportunities with the saved state. Returns (events, new_state). Sets opp.cid / opp.id."""
    old: Dict[str, Dict] = state.get("clusters", {})
    invalid_keys = {s.key for s in all_setups if s.status == "invalid"}
    parked_keys = {s.key for s in all_setups if s.status == "parked"}
    used_ids: Set[str] = {c["id"] for c in old.values() if c.get("id")} | {d["id"] for d in state.get("dismissed", [])}

    # 1) assign old identities to current opportunities (primary continuity first, then overlap, then score)
    cands = []
    for i, o in enumerate(opps):
        keys = set(o.keys)
        for cid, c in old.items():
            ov = len(keys & set(c["members"]))
            if ov:
                cands.append((0 if c["primary"] in keys else 1, -ov, -o.score, cid, i))
    cands.sort()
    opp_cid: Dict[int, str] = {}
    cid_opp: Dict[str, int] = {}
    for _, _, _, cid, i in cands:
        if i in opp_cid or cid in cid_opp:
            continue
        opp_cid[i] = cid
        cid_opp[cid] = i

    # 2) old clusters not assigned but whose members live on inside another opportunity: merged silently
    merged_into: Dict[str, int] = {}
    for cid, c in old.items():
        if cid in cid_opp:
            continue
        best, best_ov = None, 0
        for i, o in enumerate(opps):
            ov = len(set(o.keys) & set(c["members"]))
            if ov > best_ov:
                best, best_ov = i, ov
        if best is not None:
            merged_into[cid] = best

    # 3) build the new clusters and the events
    new_clusters: Dict[str, Dict] = {}
    events: List[Event] = []
    taken: Set[str] = set(cid_opp)
    for i, o in enumerate(opps):
        keys = set(o.keys)
        prev = old.get(opp_cid[i]) if i in opp_cid else None
        merged = [old[c] for c, j in sorted(merged_into.items()) if j == i]
        if prev is None:
            cid = o.primary.key
            n = 2
            while cid in old or cid in taken:
                cid = "%s#%d" % (o.primary.key, n)
                n += 1
            taken.add(cid)
            ident = new_id(cid, used_ids)
            used_ids.add(ident)
            notified = -1
        else:
            cid = opp_cid[i]
            ident = prev.get("id") or new_id(cid, used_ids)
            used_ids.add(ident)
            notified = STATUS_RANK[prev["status"]]
        for m in merged:
            notified = max(notified, STATUS_RANK[m["status"]])
        now_rank = STATUS_RANK[o.primary.status]
        status = o.primary.status if now_rank > notified else _RANK_STATUS[notified]
        if prev is None:
            if not merged:
                events.append(Event("new", cid, o.symbol, o.side, opp=o, id=ident))
            elif now_rank > notified:
                events.append(Event(status, cid, o.symbol, o.side, opp=o, id=ident))
        elif prev.get("parked"):
            events.append(Event("returned", cid, o.symbol, o.side, opp=o, id=ident, primary_changed=prev["primary"] != o.primary.key))
        elif now_rank > notified:
            events.append(Event(status, cid, o.symbol, o.side, opp=o, id=ident, primary_changed=prev["primary"] != o.primary.key))
        members = set(keys)
        if prev:
            members |= set(prev["members"])
        for m in merged:
            members |= set(m["members"])
        new_clusters[cid] = {"id": ident, "members": sorted(members), "status": status, "symbol": o.symbol, "side": o.side,
                             "primary": o.primary.key, "missed": 0}
        o.cid, o.id = cid, ident

    # 4) clusters that disappeared
    for cid, c in old.items():
        if cid in cid_opp or cid in merged_into:
            continue
        broken = any(k in invalid_keys for k in c["members"])
        if not broken and any(k in parked_keys for k in c["members"]):
            kept = copy.deepcopy(c)                      # moved away: erased from the list, kept in memory
            kept["parked"] = True
            kept["missed"] = 0
            new_clusters[cid] = kept
            continue
        if c.get("parked"):                              # was already out of sight: drop silently when it ends
            continue
        missed = c.get("missed", 0) + 1
        if broken or missed >= grace:
            events.append(Event("closed", cid, c["symbol"], c["side"], reason="invalidated" if broken else "expired", id=c.get("id", "")))
        else:
            kept = copy.deepcopy(c)
            kept["missed"] = missed
            new_clusters[cid] = kept

    new_state = dict(state)
    new_state["clusters"] = new_clusters
    new_state.setdefault("dismissed", [])
    new_state.setdefault("feedback", [])
    return events, new_state
