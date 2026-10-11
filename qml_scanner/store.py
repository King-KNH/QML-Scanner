"""Persistent memory of the scanner (the role `history.py` had in Shadow Retracement).

data/
  state.json                      opportunities, dismissals, adjustments, manual setups, learning log, Telegram offset...
  hist/<symbol>__M5.csv           recent M5 candles (feeds M15 / M30 / M45)
  hist/<symbol>__H1.csv           long H1 candles   (feeds H1 / H2 / H4 / H8 / D1)

Only CLOSED candles are stored. First run = bootstrap (long fetch), then only the candles that appeared since the last
stored one are fetched; they are merged (no duplicates), sorted and purged beyond the retention window.
Nothing here needs the network.
"""
import csv
import json
import os
import re
import tempfile
from typing import Dict, Iterable, List, Sequence, Tuple

Candle = Tuple[int, float, float, float, float]

BASE_SEC = {"M5": 300, "H1": 3600}
RETENTION_DAYS = {"M5": 35, "H1": 420}


def slug(symbol: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", symbol.lower()).strip("_")


def hist_path(root: str, symbol: str, tf: str) -> str:
    return os.path.join(root, "hist", "%s__%s.csv" % (slug(symbol), tf))


def state_path(root: str) -> str:
    return os.path.join(root, "state.json")


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_candles(path: str) -> List[Candle]:
    if not os.path.exists(path):
        return []
    out: List[Candle] = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if len(row) != 5:
                continue
            try:
                out.append((int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
            except ValueError:
                continue                      # header or damaged line: ignored
    return sorted(set(out))


def write_candles(path: str, candles: Sequence[Candle]) -> None:
    lines = ["%d,%s,%s,%s,%s" % (t, repr(o), repr(h), repr(l), repr(c)) for (t, o, h, l, c) in candles]
    _atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))


def merge(old: Sequence[Candle], new: Iterable[Candle]) -> List[Candle]:
    """Union by open time (the new value wins), sorted."""
    d = {c[0]: c for c in old}
    for c in new:
        d[c[0]] = c
    return [d[k] for k in sorted(d)]


def drop_open(candles: Iterable[Candle], tf: str, now: int) -> List[Candle]:
    """Keep only candles that are closed at `now`."""
    sec = BASE_SEC[tf]
    return [c for c in candles if c[0] + sec <= now]


def purge(candles: Sequence[Candle], tf: str, now: int) -> List[Candle]:
    limit = now - RETENTION_DAYS[tf] * 86400
    return [c for c in candles if c[0] >= limit]


def wanted_bars(tf: str) -> int:
    return int(RETENTION_DAYS[tf] * 86400 / BASE_SEC[tf])


def load_history(root: str, symbols: Iterable[str]) -> Dict[str, Dict[str, List[Candle]]]:
    out = {}
    for s in symbols:
        e = {tf: read_candles(hist_path(root, s, tf)) for tf in BASE_SEC}
        if any(e.values()):
            out[s] = e
    return out


def load_state(root: str) -> Dict:
    p = state_path(root)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        # a damaged state file must never stop the scanner: keep a copy and start clean
        os.replace(p, p + ".damaged")
        return {}


def save_state(root: str, state: Dict) -> None:
    _atomic_write(state_path(root), json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
