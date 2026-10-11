import random
import unittest

import pandas as pd

from helpers import T0, rw, with_times
from qml_scanner.candles import TF_ORDER, TF_SECONDS, aggregate, build_timeframes, check_sorted


def gappy_m5(seed, days=9):
    """M5 series with weekend-like gaps and random missing candles."""
    base = with_times(rw(seed, days * 288))
    r = random.Random(seed)
    out = []
    for c in base:
        day = (c[0] - T0) // 86400
        if day % 7 in (5, 6):          # no trading two days a week
            continue
        if r.random() < 0.01:          # sporadic hole
            continue
        out.append(c)
    return out


def pandas_aggregate(base, tf_sec):
    df = pd.DataFrame(base, columns=["t", "o", "h", "l", "c"])
    df.index = pd.to_datetime(df["t"], unit="s", utc=True)
    g = df.resample("%ds" % tf_sec, origin="epoch", closed="left", label="left").agg(
        {"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
    last_end = base[-1][0] + 300
    out = []
    for ts, row in g.iterrows():
        t = int(ts.timestamp())
        if t + tf_sec <= last_end:
            out.append((t, float(row.o), float(row.h), float(row.l), float(row.c)))
    return out


class TestAggregate(unittest.TestCase):
    def test_matches_pandas_for_every_timeframe(self):
        for seed in (1, 2, 3):
            base = gappy_m5(seed)
            for tf in TF_ORDER:
                if tf == "M5":
                    continue
                mine = aggregate(base, 300, TF_SECONDS[tf])
                ref = pandas_aggregate(base, TF_SECONDS[tf])
                self.assertEqual(len(mine), len(ref), (seed, tf))
                for a, b in zip(mine, ref):
                    self.assertEqual(a[0], b[0], (seed, tf))
                    for x, y in zip(a[1:], b[1:]):
                        self.assertAlmostEqual(x, y, 9, (seed, tf))

    def test_m45_alignment(self):
        base = with_times(rw(5, 3 * 288))
        m45 = aggregate(base, 300, 2700)
        for c in m45:
            self.assertEqual(c[0] % 2700, 0)
        # 32 buckets per UTC day, first one at 00:00
        day = [c for c in m45 if T0 <= c[0] < T0 + 86400]
        self.assertEqual(len(day), 32)
        self.assertEqual(day[0][0], T0)

    def test_only_closed_candles(self):
        base = with_times(rw(6, 500))
        last_end = base[-1][0] + 300
        for tf in TF_ORDER[1:]:
            for c in aggregate(base, 300, TF_SECONDS[tf]):
                self.assertLessEqual(c[0] + TF_SECONDS[tf], last_end)
        # cut the series in the middle of an H1 bucket: that H1 must not appear
        cut = [c for c in base if c[0] < T0 + 7 * 3600 + 900]
        h1 = aggregate(cut, 300, 3600)
        self.assertTrue(all(c[0] + 3600 <= cut[-1][0] + 300 for c in h1))
        self.assertEqual(h1[-1][0], T0 + 6 * 3600)

    def test_tail_equals_last_candles_of_full(self):
        base = with_times(rw(7, 6 * 288))
        times = [c[0] for c in base]
        for tf in TF_ORDER[1:]:
            full = aggregate(base, 300, TF_SECONDS[tf])
            for tail in (1, 5, 17):
                self.assertEqual(aggregate(base, 300, TF_SECONDS[tf], times=times, tail=tail), full[-tail:], (tf, tail))

    def test_nested_consistency(self):
        base = with_times(rw(8, 8 * 288))
        m15 = aggregate(base, 300, 900)
        for tf in ("M30", "M45", "H1", "H2", "H4", "H8", "D1"):
            direct = aggregate(base, 300, TF_SECONDS[tf])
            via = aggregate(m15, 900, TF_SECONDS[tf])
            self.assertEqual(len(direct), len(via), tf)
            for a, b in zip(direct, via):
                self.assertEqual(a[0], b[0])
                for x, y in zip(a[1:], b[1:]):
                    self.assertAlmostEqual(x, y, 9)

    def test_errors(self):
        base = with_times(rw(9, 50))
        with self.assertRaises(ValueError):
            aggregate(base, 300, 400)
        with self.assertRaises(ValueError):
            check_sorted(base + [base[-1]])
        with self.assertRaises(ValueError):
            check_sorted([base[1], base[0]])
        self.assertEqual(aggregate([], 300, 900), [])

    def test_build_timeframes_has_all_and_respects_tail(self):
        base = with_times(rw(10, 12 * 288))
        res = build_timeframes(base, 300, TF_ORDER, tail=40)
        self.assertEqual(set(res), set(TF_ORDER))
        self.assertEqual(res["M5"], base[-40:])
        for tf in TF_ORDER:
            self.assertLessEqual(len(res[tf]), 40)


if __name__ == "__main__":
    unittest.main()
