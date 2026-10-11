import unittest

from helpers import make_setup
from qml_scanner.selection import cluster, correlation_tags, exposures, score, side_allowed


class TestSideRule(unittest.TestCase):
    def test_boom_buy_only_crash_sell_only(self):
        self.assertTrue(side_allowed("Boom 1000 Index", "buy"))
        self.assertFalse(side_allowed("Boom 1000 Index", "sell"))
        self.assertTrue(side_allowed("Crash 500 Index", "sell"))
        self.assertFalse(side_allowed("Crash 500 Index", "buy"))
        self.assertTrue(side_allowed("boom 50 index", "buy"))
        self.assertFalse(side_allowed("CRASH 99 INDEX", "buy"))

    def test_other_assets_both_sides(self):
        for sym in ("EURUSD", "XAUUSD", "Volatility 75 Index", "Jump 10 Index", "Step Index 200"):
            self.assertTrue(side_allowed(sym, "buy"))
            self.assertTrue(side_allowed(sym, "sell"))


class TestScore(unittest.TestCase):
    def test_range_and_monotonicity(self):
        base = make_setup(leg_=6, lo=100, hi=101, disp=1.0)
        bigger_leg = make_setup(leg_=9, lo=100, hi=101, disp=1.0)
        narrower = make_setup(leg_=6, lo=100, hi=100.5, disp=1.0)
        stronger = make_setup(leg_=6, lo=100, hi=101, disp=1.5)
        self.assertTrue(0 <= score(base) <= 100)
        self.assertGreater(score(bigger_leg), score(base))
        self.assertGreater(score(narrower), score(base))
        self.assertGreater(score(stronger), score(base))
        self.assertEqual(score(make_setup(atr=0.0)), 0.0)
        self.assertEqual(score(make_setup(leg_=0.0)), 0.0)

    def test_caps(self):
        huge = make_setup(leg_=1000, lo=100, hi=100.0001, disp=100)
        self.assertLessEqual(score(huge), 100.0)


class TestCluster(unittest.TestCase):
    def test_overlapping_timeframes_become_one_opportunity(self):
        h1 = make_setup(tf="H1", lo=100.0, hi=101.0, entry=100.5, atr=1.0, leg_=12)
        m15 = make_setup(tf="M15", lo=100.3, hi=100.8, entry=100.5, atr=0.3, leg_=6, t_left=2000)
        h4 = make_setup(tf="H4", lo=99.8, hi=101.5, entry=100.5, atr=2.0, leg_=30, t_left=3000)
        res = cluster([h1, m15, h4])
        self.assertEqual(len(res), 1)
        o = res[0]
        keys = {o.primary.key} | {c.key for c in o.confluence}
        self.assertEqual(keys, {h1.key, m15.key, h4.key})
        self.assertEqual(len(o.confluence), 2)
        self.assertEqual(o.score, max(score(h1), score(m15), score(h4)))
        self.assertEqual([c.tf for c in o.confluence], sorted([c.tf for c in o.confluence], key=lambda t: ["M15", "H1", "H4"].index(t)))

    def test_far_apart_or_other_side_or_symbol_stay_separate(self):
        a = make_setup(tf="H1", lo=100, hi=101, entry=100.5)
        far = make_setup(tf="M30", lo=120, hi=121, entry=120.5, t_left=2000)
        other_side = make_setup(tf="H2", side="buy", lo=100, hi=101, entry=100.5, t_left=3000)
        other_sym = make_setup(symbol="GBPUSD", tf="H4", lo=100, hi=101, entry=100.5, t_left=4000)
        res = cluster([a, far, other_side, other_sym])
        self.assertEqual(len(res), 4)
        self.assertTrue(all(not o.confluence for o in res))

    def test_inactive_and_forbidden_sides_are_excluded(self):
        ok = make_setup(tf="H1")
        inv = make_setup(tf="M15", status="invalid", t_left=2000)
        exp = make_setup(tf="M30", status="expired", t_left=3000)
        boom_sell = make_setup(symbol="Boom 500 Index", side="sell", t_left=4000)
        crash_buy = make_setup(symbol="Crash 500 Index", side="buy", t_left=5000)
        boom_buy = make_setup(symbol="Boom 500 Index", side="buy", t_left=6000)
        res = cluster([ok, inv, exp, boom_sell, crash_buy, boom_buy])
        syms = sorted((o.symbol, o.side) for o in res)
        self.assertEqual(syms, [("Boom 500 Index", "buy"), ("EURUSD", "sell")])
        self.assertEqual(res[0].confluence, [])

    def test_tolerance_merges_close_zones(self):
        a = make_setup(tf="H1", lo=100.0, hi=101.0, entry=100.5, atr=1.0)
        b = make_setup(tf="M15", lo=101.1, hi=101.4, entry=101.2, atr=0.4, t_left=2000)   # 0.1 above a's zone
        self.assertEqual(len(cluster([a, b], tol_atr=0.0)), 2)
        self.assertEqual(len(cluster([a, b], tol_atr=0.5)), 1)

    def test_transitive_merge(self):
        a = make_setup(tf="H4", lo=100, hi=102, entry=101, atr=1.0, t_left=1)
        b = make_setup(tf="H1", lo=101.5, hi=104, entry=103, atr=1.0, t_left=2)
        c = make_setup(tf="M30", lo=103.5, hi=105, entry=104, atr=1.0, t_left=3)
        self.assertEqual(len(cluster([a, b, c], tol_atr=0.0)), 1)

    def test_same_timeframe_overlap_is_one_opportunity(self):
        a = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1)
        b = make_setup(tf="H1", lo=100.5, hi=101.5, entry=101, t_left=2)
        self.assertEqual(len(cluster([a, b])), 1)

    def test_primary_tie_prefers_higher_timeframe(self):
        a = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1)
        b = make_setup(tf="H4", lo=100, hi=101, entry=100.5, t_left=2)
        res = cluster([a, b])
        self.assertEqual(res[0].primary.tf, "H4")

    def test_deterministic_order(self):
        items = [make_setup(tf=t, lo=100 + i, hi=101 + i, entry=100.5 + i, t_left=i) for i, t in enumerate(["M5", "H1", "D1", "M15"])]
        r1 = [o.primary.key for o in cluster(items)]
        r2 = [o.primary.key for o in cluster(list(reversed(items)))]
        self.assertEqual(r1, r2)


class TestCorrelation(unittest.TestCase):
    def test_exposures(self):
        self.assertEqual(exposures("EURUSD", "buy"), {"EUR": 1, "USD": -1})
        self.assertEqual(exposures("USDCHF", "sell"), {"USD": -1, "CHF": 1})
        self.assertEqual(exposures("XAUUSD", "buy"), {"METAL": 1, "USD": -1})
        self.assertEqual(exposures("ETHUSD", "sell"), {"CRYPTO": -1, "USD": 1})
        self.assertEqual(exposures("Volatility 75 Index", "buy"), {})
        self.assertEqual(exposures("Boom 500 Index", "buy"), {})

    def tags(self, items):
        opps = cluster(items)
        correlation_tags(opps)
        return {(o.symbol, o.side): o.tags for o in opps}

    def test_same_thesis_is_flagged_both_ways(self):
        t = self.tags([make_setup(symbol="EURUSD", side="buy", t_left=1), make_setup(symbol="GBPUSD", side="buy", t_left=2),
                       make_setup(symbol="USDCHF", side="sell", t_left=3)])
        self.assertEqual(len(t[("EURUSD", "buy")]), 1)
        self.assertIn("USD ↓", t[("EURUSD", "buy")][0])
        self.assertIn("GBPUSD (achat)", t[("EURUSD", "buy")][0])
        self.assertIn("USDCHF (vente)", t[("EURUSD", "buy")][0])
        self.assertIn("EURUSD (achat)", t[("USDCHF", "sell")][0])

    def test_opposite_bets_are_not_flagged(self):
        t = self.tags([make_setup(symbol="EURUSD", side="buy", t_left=1), make_setup(symbol="USDCHF", side="buy", t_left=2)])
        self.assertEqual(t[("EURUSD", "buy")], [])
        self.assertEqual(t[("USDCHF", "buy")], [])

    def test_metals_and_crypto_groups(self):
        t = self.tags([make_setup(symbol="XAUUSD", side="buy", t_left=1), make_setup(symbol="XAGUSD", side="buy", t_left=2),
                       make_setup(symbol="BTCUSD", side="sell", t_left=3), make_setup(symbol="ETHUSD", side="sell", t_left=4)])
        self.assertTrue(any("METAL ↑" in x and "XAGUSD (achat)" in x for x in t[("XAUUSD", "buy")]))
        self.assertTrue(any("CRYPTO ↓" in x and "ETHUSD (vente)" in x for x in t[("BTCUSD", "sell")]))
        # gold buy and BTC sell: USD sign differs (-1 vs +1) -> no shared thesis
        self.assertFalse(any("BTCUSD" in x for x in t[("XAUUSD", "buy")]))

    def test_synthetics_and_single_symbol_have_no_tags(self):
        t = self.tags([make_setup(symbol="Volatility 75 Index", side="buy", t_left=1),
                       make_setup(symbol="Jump 10 Index", side="buy", t_left=2),
                       make_setup(symbol="EURUSD", side="buy", tf="H1", t_left=3),
                       make_setup(symbol="EURUSD", side="buy", tf="M15", t_left=4, lo=100.2, hi=100.8)])
        self.assertTrue(all(v == [] for v in t.values()))


if __name__ == "__main__":
    unittest.main()
