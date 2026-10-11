"""History update: for each symbol and base timeframe (M5, H1)
  - first time (or a long gap)  -> bootstrap: the last N candles, fetched backwards in pages;
  - afterwards                  -> only the candles that appeared since the last stored one;
  - closed candles only, merged without duplicates, purged beyond the retention window, saved atomically.
Source order: Deriv (public WebSocket); if it fails for a symbol and the symbol is forex / metal / crypto, Twelve Data.
A time budget protects the job: what is not done this run (« deferred ») is simply resumed at the next run.
"""
import time
from typing import Callable, Dict, Iterable, Optional

from . import store
from .sources import DerivClient, QuotaExceeded, TwelveClient

TFS = ("M5", "H1")


def _staleness(root: str, symbol: str) -> int:
    last = []
    for tf in TFS:
        c = store.read_candles(store.hist_path(root, symbol, tf))
        last.append(c[-1][0] if c else 0)
    return min(last)


def _update_one(root: str, symbol: str, tf: str, now: int, deriv: Optional[DerivClient], code: Optional[str],
                market_open: Optional[bool], twelve: Optional[TwelveClient]) -> str:
    sec = store.BASE_SEC[tf]
    path = store.hist_path(root, symbol, tf)
    stored = store.read_candles(path)
    if stored and market_open is False:
        return "closed"
    if stored and now < stored[-1][0] + 2 * sec:
        return "uptodate"                                    # the next candle is not closed yet
    last = stored[-1][0] if stored else None
    boot = last is None or (now - last) > store.RETENTION_DAYS[tf] * 86400 * 0.8
    new = None
    err = None
    if deriv is not None and code:
        try:
            if boot:
                new = deriv.candles_back(code, sec, store.wanted_bars(tf))
            else:
                new = deriv.candles(code, sec, 5000, start=last + sec)
        except Exception as e:
            err = "deriv: %s" % str(e)[:160]
    if new is None and twelve is not None and twelve.supports(symbol):
        try:
            new = twelve.candles(symbol, sec, count=store.wanted_bars(tf)) if boot else twelve.candles(symbol, sec, since=last + sec)
            err = None
        except QuotaExceeded as e:
            err = str(e)
        except Exception as e:
            err = "twelve: %s" % str(e)[:160]
    if new is None:
        return "error: %s" % (err or "aucune source disponible")
    new = store.drop_open(new, tf, now)
    merged = store.purge(store.merge(stored, new), tf, now)
    store.write_candles(path, merged)
    return "ok +%d (%s)" % (len([c for c in new if not stored or c[0] > stored[-1][0]]), "bootstrap" if boot else "incrémental")


def update_history(root: str, symbols: Iterable[str], now: int, deriv: Optional[DerivClient] = None,
                   codes: Optional[Dict[str, str]] = None, is_open: Optional[Dict[str, bool]] = None,
                   twelve: Optional[TwelveClient] = None, time_budget: float = 480.0, clock: Callable = time.time) -> Dict[str, Dict[str, str]]:
    codes, is_open = codes or {}, is_open or {}
    t0 = clock()
    order = sorted(symbols, key=lambda s: _staleness(root, s))          # stalest first
    report: Dict[str, Dict[str, str]] = {s: {} for s in order}
    for tf in TFS:
        for s in order:
            if clock() - t0 > time_budget:
                report[s][tf] = "deferred"
                continue
            report[s][tf] = _update_one(root, s, tf, now, deriv, codes.get(s), is_open.get(s), twelve)
    return report


def summarize(report: Dict[str, Dict[str, str]]) -> str:
    counts: Dict[str, int] = {}
    for tfs in report.values():
        for st in tfs.values():
            key = st.split(":")[0].split(" ")[0]
            counts[key] = counts.get(key, 0) + 1
    return ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items()))
