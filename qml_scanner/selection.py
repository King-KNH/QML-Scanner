"""Cross-timeframe selection.

1. side_allowed()      Boom -> buy QML only, Crash -> sell QML only (Harold's rule).
2. cluster()           setups of the same symbol and side whose entry/zone intervals overlap are ONE opportunity.
3. score()             clarity/precision score used to pick the primary timeframe of a cluster.
                       The weights are explicit initial values, meant to be tuned with Harold's zone corrections.
4. correlation_tags()  flags opportunities that express the same market thesis (shared currency leg).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from .candles import TF_RANK
from .engine import ACTIVE, Setup


def side_allowed(symbol: str, side: str) -> bool:
    s = symbol.lower()
    if "boom" in s:
        return side == "buy"
    if "crash" in s:
        return side == "sell"
    return True


@dataclass
class ScoreWeights:
    clarity: float = 0.45      # structure leg size vs ATR (a clear structure is large compared to the noise)
    precision: float = 0.35    # narrow zone relative to the leg (precise entry)
    momentum: float = 0.20     # strength of the displacement candle vs ATR
    leg_atr_full: float = 10.0  # leg / ATR giving full clarity
    disp_atr_full: float = 2.0  # displacement body / ATR giving full momentum


def score(s: Setup, w: ScoreWeights = ScoreWeights()) -> float:
    if s.zone_kind == "MANUEL":          # proposed by Harold after his visual check: he outranks the robot
        return 1000.0
    if s.atr <= 0 or s.leg <= 0:
        return 0.0
    clarity = min(s.leg / s.atr / w.leg_atr_full, 1.0)
    precision = 1.0 - min(s.zone_height / s.leg, 1.0)
    momentum = min(s.disp / s.atr / w.disp_atr_full, 1.0)
    return round(100.0 * (w.clarity * clarity + w.precision * precision + w.momentum * momentum), 2)


def interval(s: Setup) -> Tuple[float, float]:
    """Price span covered by the setup: the QML line and the zone."""
    return min(s.entry, s.zone_low), max(s.entry, s.zone_high)


@dataclass
class Opportunity:
    primary: Setup
    confluence: List[Setup] = field(default_factory=list)
    score: float = 0.0
    tags: List[str] = field(default_factory=list)
    cid: str = ""       # stable identity across cycles (set by notify.plan_notifications)
    id: str = ""        # short id shown to the user, e.g. "A3F7" (set by notify.plan_notifications)

    @property
    def symbol(self) -> str:
        return self.primary.symbol

    @property
    def side(self) -> str:
        return self.primary.side

    @property
    def keys(self) -> List[str]:
        return [self.primary.key] + [s.key for s in self.confluence]


def _overlap(a: Setup, b: Setup, tol_atr: float) -> bool:
    lo_a, hi_a = interval(a)
    lo_b, hi_b = interval(b)
    tol = tol_atr * min(a.atr, b.atr)
    return lo_a - tol <= hi_b and lo_b - tol <= hi_a


def cluster(setups: Sequence[Setup], tol_atr: float = 0.25, weights: ScoreWeights = ScoreWeights()) -> List[Opportunity]:
    """Group ACTIVE setups into opportunities (one per symbol/side/price area) and pick the clearest timeframe."""
    groups: Dict[Tuple[str, str], List[Setup]] = {}
    for s in setups:
        if s.status in ACTIVE and side_allowed(s.symbol, s.side):
            groups.setdefault((s.symbol, s.side), []).append(s)
    result: List[Opportunity] = []
    for (_, _), items in groups.items():
        items = sorted(items, key=lambda s: (s.tf_rank, s.t_left))
        parent = list(range(len(items)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if _overlap(items[i], items[j], tol_atr):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[rj] = ri
        comps: Dict[int, List[Setup]] = {}
        for i, s in enumerate(items):
            comps.setdefault(find(i), []).append(s)
        for members in comps.values():
            ranked = sorted(members, key=lambda s: (-score(s, weights), -s.tf_rank, s.t_left))
            primary = ranked[0]
            rest = sorted(ranked[1:], key=lambda s: (s.tf_rank, s.t_left))
            result.append(Opportunity(primary=primary, confluence=rest, score=score(primary, weights)))
    result.sort(key=lambda o: (o.symbol, o.side, -o.score))
    return result


# ---------------------------------------------------------------- correlation (shared currency legs)
_ALIAS = {"XAU": "METAL", "XAG": "METAL", "BTC": "CRYPTO", "ETH": "CRYPTO"}
_CCY = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "XAU", "XAG", "BTC", "ETH"}


def exposures(symbol: str, side: str) -> Dict[str, int]:
    """{leg: +1 long / -1 short}. Only 6-letter pairs (forex, metals, crypto vs USD); synthetic indices have none."""
    s = symbol.upper().replace("/", "")
    if len(s) != 6 or s[:3] not in _CCY or s[3:] not in _CCY:
        return {}
    base, quote = _ALIAS.get(s[:3], s[:3]), _ALIAS.get(s[3:], s[3:])
    sign = 1 if side == "buy" else -1
    return {base: sign, quote: -sign}


def correlation_tags(opps: Sequence[Opportunity]) -> None:
    """Fill Opportunity.tags with the other opportunities that share the same thesis (same leg, same direction)."""
    expo = [exposures(o.symbol, o.side) for o in opps]
    for i, o in enumerate(opps):
        o.tags = []
        shared: Dict[str, List[str]] = {}
        for j, other in enumerate(opps):
            if i == j or o.symbol == other.symbol:
                continue
            for leg, sg in expo[i].items():
                if expo[j].get(leg) == sg:
                    shared.setdefault("%s %s" % (leg, "↑" if sg > 0 else "↓"), []).append(
                        "%s (%s)" % (other.symbol, "achat" if other.side == "buy" else "vente"))
        for leg_txt, names in sorted(shared.items()):
            o.tags.append("Même thèse %s : %s" % (leg_txt, ", ".join(sorted(set(names)))))
