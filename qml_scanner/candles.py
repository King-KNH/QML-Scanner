"""Timeframe construction from base candles.

A candle is a tuple (t_open_epoch_seconds, open, high, low, close).
All timeframes are nested: only the base series (M5) needs to be stored, everything else is derived.
Buckets are aligned on 00:00 UTC (epoch multiples), which is exact for M45 (1440 / 45 = 32 per day), H2, H4, H8, D1.
Only CLOSED candles are returned: a bucket is closed when its end is <= the end of the last base candle.
"""
from bisect import bisect_left
from typing import Dict, Iterable, List, Sequence, Tuple

Candle = Tuple[int, float, float, float, float]

TF_SECONDS: Dict[str, int] = {
    "M5": 300, "M15": 900, "M30": 1800, "M45": 2700,
    "H1": 3600, "H2": 7200, "H4": 14400, "H8": 28800, "D1": 86400,
}
TF_ORDER: List[str] = ["M5", "M15", "M30", "M45", "H1", "H2", "H4", "H8", "D1"]
TF_RANK: Dict[str, int] = {tf: i for i, tf in enumerate(TF_ORDER)}


def check_sorted(base: Sequence[Candle]) -> None:
    prev = None
    for c in base:
        if prev is not None and c[0] <= prev:
            raise ValueError("base candles must be strictly increasing in time (duplicate or unsorted timestamp %s)" % c[0])
        prev = c[0]


def aggregate(base: Sequence[Candle], base_sec: int, tf_sec: int, times: Sequence[int] = None, tail: int = None) -> List[Candle]:
    """Aggregate closed base candles into closed candles of tf_sec seconds.

    tail: keep only the last `tail` closed candles (the base slice needed is computed from the timestamps).
    times: optional precomputed [c[0] for c in base] (saves work when building many timeframes).
    """
    if tf_sec % base_sec != 0:
        raise ValueError("timeframe %ss is not a multiple of the base %ss" % (tf_sec, base_sec))
    if not base:
        return []
    last_end = base[-1][0] + base_sec
    start_idx = 0
    if tail is not None:
        last_closed_start = (last_end // tf_sec) * tf_sec - tf_sec
        first_start = last_closed_start - (tail - 1) * tf_sec
        if times is None:
            times = [c[0] for c in base]
        start_idx = bisect_left(times, first_start)
    out: List[Candle] = []
    cur_b = None
    o = h = l = c = 0.0
    for i in range(start_idx, len(base)):
        t, o_, h_, l_, c_ = base[i]
        b = t - t % tf_sec
        if b != cur_b:
            if cur_b is not None and cur_b + tf_sec <= last_end:
                out.append((cur_b, o, h, l, c))
            cur_b, o, h, l, c = b, o_, h_, l_, c_
        else:
            if h_ > h:
                h = h_
            if l_ < l:
                l = l_
            c = c_
    if cur_b is not None and cur_b + tf_sec <= last_end:
        out.append((cur_b, o, h, l, c))
    if tail is not None and len(out) > tail:
        out = out[-tail:]
    return out


def build_timeframes(base: Sequence[Candle], base_sec: int = 300, tfs: Iterable[str] = TF_ORDER, tail: int = 500) -> Dict[str, List[Candle]]:
    """Return {tf: closed candles (last `tail`)} for every requested timeframe, derived from the base series."""
    check_sorted(base)
    times = [c[0] for c in base]
    res: Dict[str, List[Candle]] = {}
    for tf in tfs:
        sec = TF_SECONDS[tf]
        if sec == base_sec:
            res[tf] = list(base[-tail:])
        else:
            res[tf] = aggregate(base, base_sec, sec, times=times, tail=tail)
    return res


# ------------------------------------------------------------------ history entries
# The stored history of one symbol is either a plain list of M5 candles, or {"M5": [...], "H1": [...]}:
# M5 (recent) feeds M15 / M30 / M45, H1 (long) feeds H1 / H2 / H4 / H8 / D1.
def as_entry(x) -> Dict[str, List[Candle]]:
    if isinstance(x, dict):
        return x
    return {"M5": list(x)}


def finest(entry) -> List[Candle]:
    e = as_entry(entry)
    return e.get("M5") or e.get("H1") or []


def frames_for(entry, tf: str, tail: int = 500) -> List[Candle]:
    """Closed candles of timeframe `tf` built from the best available base series (empty if impossible)."""
    e = as_entry(entry)
    sec = TF_SECONDS[tf]
    if sec >= 3600 and e.get("H1"):
        base, bsec = e["H1"], 3600
    elif e.get("M5"):
        base, bsec = e["M5"], 300
    elif e.get("H1"):
        base, bsec = e["H1"], 3600
    else:
        return []
    if sec < bsec:
        return []
    return build_timeframes(base, bsec, [tf], tail=tail)[tf]
