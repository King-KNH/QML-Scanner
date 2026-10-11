import unittest

from helpers import build, leg, mirror, rw, with_times
from qml_scanner.engine import scan_series
from qml_scanner.qml_core import Params

P = Params(ext=3, atr_len=5)
SELL_R = build(100, 110, 104, 120, 112, 126, 108, 118.0)
SELL_C = build(100, 130, 100, 118, 95, 124, 90, 116.0)
ENTRY_THEN_BREAK = build(100, 110, 104, 120, 112, 126, 108, 119.8) + leg(119.8, 121.5, 3)


def one(seq, **kw):
    res = scan_series("TEST", "M5", with_times(seq), P, **kw)
    return res


class TestScenarios(unittest.TestCase):
    def test_sell_reversal_fields(self):
        res = one(SELL_R)
        self.assertEqual(len(res), 1)
        s = res[0]
        self.assertEqual((s.side, s.qml_type, s.zone_kind), ("sell", "QML-R", "MPL"))
        self.assertAlmostEqual(s.entry, 120.0, 9)
        self.assertAlmostEqual(s.zone_high, 120.0, 9)
        self.assertAlmostEqual(s.zone_low, 119.5, 9)
        self.assertGreater(s.head, 125.0)
        self.assertLess(s.extreme, 109.0)
        self.assertGreater(s.leg, 15.0)
        self.assertIsNone(s.t_entry)
        self.assertIsNone(s.t_invalid)
        self.assertLess(s.t_left, s.t_support)
        self.assertLess(s.t_support, s.t_head)
        self.assertLess(s.t_head, s.t_break)
        self.assertLessEqual(s.t_break, s.t_detect)
        self.assertIn(s.status, ("detected", "approaching"))

    def test_status_transitions_follow_last_price(self):
        base = one(SELL_R)[0]
        atr = base.atr
        self.assertGreater(atr, 0)
        far = one(SELL_R, last_price=base.entry - 10 * atr)[0]
        near = one(SELL_R, last_price=base.entry - 0.1 * atr)[0]
        edge_in = one(SELL_R, last_price=base.entry - 0.49 * atr)[0]
        edge_out = one(SELL_R, last_price=base.entry - 0.51 * atr)[0]
        over = one(SELL_R, last_price=base.entry + 0.01)[0]
        self.assertEqual(far.status, "detected")
        self.assertEqual(near.status, "approaching")
        self.assertEqual(edge_in.status, "approaching")
        self.assertEqual(edge_out.status, "detected")
        self.assertEqual(over.status, "entered")
        self.assertAlmostEqual(near.distance, 0.1 * atr, 9)

    def test_entry_then_invalidation(self):
        s = one(ENTRY_THEN_BREAK)[0]
        self.assertEqual(s.status, "invalid")
        self.assertIsNotNone(s.t_entry)
        self.assertIsNotNone(s.t_invalid)
        self.assertLessEqual(s.t_entry, s.t_invalid)
        self.assertFalse(s.active)

    def test_continuation_label(self):
        res = one(SELL_C)
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0].side, res[0].qml_type), ("sell", "QML-C"))

    def test_buy_is_exact_mirror_of_sell(self):
        s = one(SELL_R)[0]
        b = one(mirror(SELL_R))[0]
        self.assertEqual(b.side, "buy")
        self.assertEqual((b.qml_type, b.zone_kind, b.status), (s.qml_type, s.zone_kind, s.status))
        self.assertAlmostEqual(b.entry, -s.entry, 9)
        self.assertAlmostEqual(b.zone_low, -s.zone_high, 9)
        self.assertAlmostEqual(b.zone_high, -s.zone_low, 9)
        self.assertAlmostEqual(b.head, -s.head, 9)
        self.assertAlmostEqual(b.extreme, -s.extreme, 9)
        self.assertEqual((b.t_left, b.t_support, b.t_head, b.t_break, b.t_detect),
                         (s.t_left, s.t_support, s.t_head, s.t_break, s.t_detect))
        self.assertAlmostEqual(b.distance, s.distance, 9)

    def test_buy_zone_is_ordered_and_on_the_right_side(self):
        b = one(mirror(SELL_R))[0]
        self.assertLessEqual(b.zone_low, b.zone_high)
        self.assertLess(b.head, b.extreme)      # buy: head = lowest low, extreme = highest high

    def test_too_few_candles(self):
        self.assertEqual(scan_series("X", "M5", with_times(SELL_R[:30]), P), [])

    def test_order_block_mode_and_params_are_respected(self):
        res = scan_series("T", "M5", with_times(SELL_R), Params(ext=3, atr_len=5, mode="ob"))
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].zone_kind, "OB")
        none = scan_series("T", "M5", with_times(SELL_R), Params(ext=3, atr_len=5, mode="mpl", midzone=False, req_mom=True),
                           approach_atr=0.5)
        self.assertEqual(len(none), 1)


class TestInvariants(unittest.TestCase):
    def test_random_series(self):
        total = 0
        for seed in range(12):
            for p in (Params(ext=5), Params(ext=8, mode="ob"), Params(ext=4, strict=False, req_mom=False)):
                series = with_times(rw(seed, 1500))
                res = scan_series("R", "M5", series, p)
                total += len(res)
                times = {c[0] for c in series}
                for s in res:
                    self.assertLessEqual(s.zone_low, s.zone_high)
                    self.assertGreater(s.leg, 0)
                    self.assertTrue(s.t_left < s.t_support < s.t_head < s.t_break <= s.t_detect)
                    self.assertTrue({s.t_left, s.t_support, s.t_head, s.t_break, s.t_detect} <= times)
                    if s.side == "sell":
                        self.assertGreater(s.head, s.shoulder)
                        self.assertLess(s.extreme, s.support)
                        self.assertLessEqual(s.zone_high, s.head + 1e-9)
                        self.assertGreaterEqual(s.zone_low, s.extreme - 1e-9)
                    else:
                        self.assertLess(s.head, s.shoulder)
                        self.assertGreater(s.extreme, s.support)
                        self.assertGreaterEqual(s.zone_low, s.head - 1e-9)
                        self.assertLessEqual(s.zone_high, s.extreme + 1e-9)
                    if s.status == "invalid":
                        self.assertIsNotNone(s.t_invalid)
                    if s.status == "entered":
                        self.assertTrue(s.t_entry is not None or s.last_price is not None)
                keys = [s.key for s in res]
                self.assertEqual(len(keys), len(set(keys)))
        self.assertGreater(total, 30)

    def test_mirror_symmetry_on_random_series(self):
        for seed in range(6):
            raw = rw(seed, 1500)
            a = scan_series("R", "M5", with_times(raw), Params(ext=5))
            b = scan_series("R", "M5", with_times(mirror(raw)), Params(ext=5))
            sig = lambda r, flip: sorted((s.t_left, s.t_head, s.t_break, s.qml_type, s.zone_kind, s.status,
                                          s.t_entry, s.t_invalid, ("sell" if s.side == "buy" else "buy") if flip else s.side)
                                         for s in r)
            self.assertEqual(sig(a, True), sig(b, False), seed)

    def test_deterministic(self):
        raw = with_times(rw(3, 1500))
        a = scan_series("R", "M5", raw, Params(ext=5))
        b = scan_series("R", "M5", raw, Params(ext=5))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
