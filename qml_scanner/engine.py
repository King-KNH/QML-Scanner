"""Run the validated QML core on one (symbol, timeframe) series and return Setup objects (real prices).

The core is deterministic and stateless: each call recomputes the structure from the candles it is given.
Status of a setup (computed on the last candle / last price):
  detected    QML complete, price not yet close to the entry line
  approaching price below (sell) / above (buy) the entry line by <= approach_atr x ATR, not yet reached
  entered     price reached the entry line (same event as the "QM ENTRY!" label of the indicator)
  parked      price has moved AWAY: a candle closed beyond the structure extreme (LL for a sell, HH for a buy) and the
              price is not near the line. Erased from the list, kept in memory: it comes back when the price returns near the line.
  invalid     price closed beyond the zone / QML line (setup broken)
  expired     setup older than max_bars bars without being invalidated
"""
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .candles import Candle, TF_RANK
from .qml_core import Detector, Params

ACTIVE = ("detected", "approaching", "entered")
STATUS_RANK = {"detected": 0, "approaching": 1, "entered": 2}


@dataclass
class Setup:
    symbol: str
    tf: str
    side: str            # "sell" | "buy"
    qml_type: str        # "QML-R" (reversal) | "QML-C" (continuation)
    zone_kind: str       # "MPL" | "OB" | "MPL-MID"
    entry: float         # QML line (left shoulder level)
    zone_low: float
    zone_high: float
    shoulder: float      # swing a : left shoulder price (= QML line, except MPL-MID)
    support: float       # swing b : QM support (L for a sell, H for a buy)
    head: float          # swing c : head (HH for a sell, LL for a buy)
    extreme: float       # swing e : structure break (LL for a sell, HH for a buy)
    t_left: int
    t_support: int
    t_head: int
    t_break: int
    t_detect: int
    status: str
    atr: float
    leg: float           # |head - extreme|
    disp: float          # body of the displacement candle
    last_price: float
    distance: float      # distance from last price to entry line (>0 while not reached)
    t_entry: Optional[int] = None
    t_invalid: Optional[int] = None
    away: bool = False                       # a close beyond the structure extreme happened after detection
    adjusted: bool = False                   # zone / line adjusted by Harold (see overrides.py)
    zone_candle: Tuple[float, ...] = ()      # (open, high, low, close) of the first candle of the zone (for the learning study)

    @property
    def head_excess(self) -> float:
        """How far the head goes beyond the left shoulder (QML line)."""
        return abs(self.head - self.shoulder)

    @property
    def key(self) -> str:
        return "%s|%s|%s|%d" % (self.symbol, self.tf, self.side, self.t_left)

    @property
    def tf_rank(self) -> int:
        return TF_RANK.get(self.tf, -1)

    @property
    def zone_height(self) -> float:
        return self.zone_high - self.zone_low

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


def scan_series(symbol: str, tf: str, candles: Sequence[Candle], params: Params = None, approach_atr: float = 0.5,
                last_price: float = None) -> List[Setup]:
    """All QML setups found in `candles` (closed candles, oldest first). Includes invalid/expired ones (status set)."""
    if len(candles) < 40:
        return []
    p = params if params is not None else Params()
    det = Detector(p)
    for (_, o, h, l, c) in candles:
        det.on_bar(o, h, l, c)
    times = [c[0] for c in candles]
    n = len(candles)
    price = candles[-1][4] if last_price is None else last_price
    atr_last = det.atr[-1]
    out: List[Setup] = []
    for q in det.qms:
        d = q.d
        a, b = d * q.tt, d * q.tb
        zone_low, zone_high = min(a, b), max(a, b)
        entry = d * q.ent
        entry_t = q.ent                      # transformed (sell: price, buy: -price)
        price_t = d * price
        dist = entry_t - price_t             # > 0 : price still below (sell) / above (buy) the line
        away = any(d * candles[k][4] < d * q.pLL for k in range(q.llb + 1, n))
        near = dist <= (approach_atr * atr_last if atr_last > 0 else 0.0)
        if q.inv is not None:
            status = "invalid"
        elif q.done:
            status = "expired"
        elif away and not near:
            status = "parked"
        elif q.touch is not None or price_t >= entry_t:
            status = "entered"
        elif near:
            status = "approaching"
        else:
            status = "detected"
        j = q.zbar
        out.append(Setup(
            symbol=symbol, tf=tf, side="sell" if d == 1 else "buy",
            qml_type="QML-R" if q.typ == "R" else "QML-C", zone_kind=q.kind,
            entry=entry, zone_low=zone_low, zone_high=zone_high,
            shoulder=q.pH, support=q.pL, head=q.pHH, extreme=q.pLL,
            t_left=times[q.hb], t_support=times[q.lb], t_head=times[q.hhb], t_break=times[q.llb], t_detect=times[q.det],
            status=status, atr=atr_last, leg=abs(q.pHH - q.pLL), disp=abs(candles[j][4] - candles[j][1]),
            last_price=price, distance=dist if not math.isnan(dist) else 0.0,
            t_entry=times[q.touch] if q.touch is not None else None,
            t_invalid=times[q.inv] if q.inv is not None else None,
            away=away, zone_candle=tuple(candles[q.left][1:5]),
        ))
    return out
