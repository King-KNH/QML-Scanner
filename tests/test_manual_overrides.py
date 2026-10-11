import unittest
from dataclasses import replace

from helpers import T0, build, build_wick, leg, make_history, make_setup, mirror, rw, with_times
from qml_scanner import config
from qml_scanner.candles import TF_SECONDS
from qml_scanner.manual import add_manual, atr_wilder, describe_diag, diagnose, manual_setups
from qml_scanner.overrides import adjust, apply_overrides, find_override, prune, reassess
from qml_scanner.qml_core import Params
from qml_scanner.engine import scan_series

NOW = 1_800_000_000
P = Params(ext=3, atr_len=5, midzone=False)


class TestAddManual(unittest.TestCase):
    def test_entry_defaults_and_validation(self):
        st = {}
        s = add_manual(st, "XAUUSD", "H1", "sell", NOW, zone=(4199.4, 4190.0))
        self.assertEqual((s["zone_low"], s["zone_high"], s["entry"]), (4190.0, 4199.4, 4190.0))      # sell: proximal edge = low
        b = add_manual(st, "XAUUSD", "H1", "buy", NOW, zone=(4100.0, 4110.0))
        self.assertEqual(b["entry"], 4110.0)                                                           # buy: proximal edge = high
        c = add_manual(st, "EURUSD", "H4", "sell", NOW, entry=1.085)
        self.assertEqual((c["zone_low"], c["zone_high"], c["entry"]), (1.085, 1.085, 1.085))
        d = add_manual(st, "EURUSD", "H4", "sell", NOW, zone=(1.0, 1.1), entry=1.09, head=1.12, support=1.0, extreme=0.99)
        self.assertEqual((d["entry"], d["head"], d["support"], d["extreme"]), (1.09, 1.12, 1.0, 0.99))
        with self.assertRaises(ValueError):
            add_manual(st, "EURUSD", "H4", "sell", NOW)

    def test_ids_and_keys_are_unique_even_in_the_same_second(self):
        st = {}
        recs = [add_manual(st, "XAUUSD", "H1", "sell", NOW, zone=(1, 2)) for _ in range(30)]
        self.assertEqual(len({r["id"] for r in recs}), 30)
        self.assertEqual(len({r["key"] for r in recs}), 30)
        self.assertEqual(len(st["manual"]), 30)


class TestManualFollowUp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hist = make_history(["EURUSD"], days=30, seed0=5)
        cls.base = cls.hist["EURUSD"]
        cls.price = cls.base[-1][4]
        cls.t_end = cls.base[-1][0] + 300

    def one(self, rec_kwargs, base=None, last_price=None):
        st = {}
        rec = add_manual(st, "EURUSD", "H1", rec_kwargs.pop("side", "sell"), rec_kwargs.pop("t", self.base[-1][0] - 3600), **rec_kwargs)
        out = manual_setups(st, {"EURUSD": base or self.base}, {"EURUSD": last_price} if last_price else None)
        return rec, out[0]

    def test_basic_fields_and_statuses(self):
        rec, s = self.one(dict(zone=(self.price + 50, self.price + 51)))
        self.assertEqual((s.zone_kind, s.qml_type, s.symbol, s.tf, s.side), ("MANUEL", "MANUEL", "EURUSD", "H1", "sell"))
        self.assertEqual(s.key, rec["key"])
        self.assertGreater(s.atr, 0)
        self.assertEqual(s.status, "detected")
        near_entry = self.price + 0.2 * s.atr
        _, s2 = self.one(dict(zone=(near_entry, near_entry + 1), t=self.t_end))      # created just now: no touch yet
        self.assertEqual(s2.status, "approaching")
        _, s3 = self.one(dict(zone=(self.price - 1, self.price + 1), entry=self.price - 0.5))
        self.assertEqual(s3.status, "entered")

    def test_touch_since_creation_counts_even_if_price_left(self):
        t = self.base[-1][0] - 3000
        window = [c for c in self.base if c[0] >= t]
        top = max(c[2] for c in window)
        _, s = self.one(dict(zone=(top - 0.0001, top + 5.0), entry=top - 0.0001, t=t))
        self.assertEqual(s.status, "entered")

    def test_close_beyond_the_zone_after_creation_is_invalid(self):
        t = self.base[-1][0] - 20 * 3600
        frames = [c for c in __import__("qml_scanner.candles", fromlist=["x"]).aggregate(self.base, 300, 3600) if c[0] >= t]
        hi_close = max(c[4] for c in frames)
        _, s = self.one(dict(zone=(hi_close - 10, hi_close - 0.0001), entry=hi_close - 10, t=t))
        self.assertEqual(s.status, "invalid")

    def test_expiry(self):
        _, s = self.one(dict(zone=(self.price + 50, self.price + 51), t=self.base[-1][0] - 301 * TF_SECONDS["H1"]))
        self.assertEqual(s.status, "expired")

    def test_missing_history_is_ignored_and_buy_side_works(self):
        st = {}
        add_manual(st, "NOPE", "H1", "sell", NOW, zone=(1, 2))
        self.assertEqual(manual_setups(st, {"EURUSD": self.base}), [])
        _, s = self.one(dict(side="buy", zone=(self.price - 51, self.price - 50)))
        self.assertEqual((s.side, s.status), ("buy", "detected"))

    def test_atr(self):
        self.assertEqual(atr_wilder([]), 0.0)
        c = [(0, 1, 2, 0, 1), (1, 1, 3, 1, 2)]
        self.assertAlmostEqual(atr_wilder(c), (2 + 2) / 2)
        self.assertGreater(atr_wilder(with_times(rw(1, 200))), 0)


def diag_for(seq, side, entry, zone, params=P, **extra):
    st = {}
    rec = add_manual(st, "T", "M15", side, NOW, zone=zone, entry=entry, **extra)
    base = with_times(seq, 900)
    return diagnose(rec, base, params, base_sec=900)


class TestDiagnosis(unittest.TestCase):
    SELL_R = build(100, 110, 104, 120, 112, 126, 108, 118.0)

    def test_robot_already_saw_it(self):
        d = diag_for(self.SELL_R, "sell", 120.0, (119.5, 120.0))
        self.assertTrue(d["matched"])
        self.assertAlmostEqual(d["delta"]["entry"], 0.0, 6)
        self.assertEqual(d["delta"]["type"], "QML-R")
        self.assertIn("déjà repéré", " ".join(describe_diag(d)))

    def test_head_level_must_agree_when_given(self):
        ok = diag_for(self.SELL_R, "sell", 120.0, (119.5, 120.0), head=126.0)
        self.assertTrue(ok["matched"])
        bad = diag_for(self.SELL_R, "sell", 120.0, (119.5, 120.0), head=160.0)
        self.assertFalse(bad["matched"])

    def test_wick_sweep_is_explained_by_the_body_rule(self):
        d = diag_for(build_wick(), "sell", 120.0, (119.5, 120.0))
        self.assertFalse(d["matched"])
        self.assertEqual([b["key"] for b in d["blocking"]], ["body_break"])
        text = " ".join(describe_diag(d))
        self.assertIn("corps d'une bougie", text)
        self.assertIn("enregistré pour apprendre", text)

    def test_mixed_structure_is_explained_by_the_strict_rule(self):
        d = diag_for(build(100, 125, 104, 120, 112, 126, 108, 118.0), "sell", 120.0, (119.5, 120.0))
        self.assertFalse(d["matched"])
        self.assertIn("strict", [b["key"] for b in d["blocking"]])

    def test_buy_side_is_symmetric(self):
        d = diag_for(mirror(build_wick()), "buy", -120.0, (-120.0, -119.5))
        self.assertFalse(d["matched"])
        self.assertEqual([b["key"] for b in d["blocking"]], ["body_break"])

    def test_two_rules_together(self):
        # wick sweep AND mixed structure: neither rule alone is enough
        seq = build_wick(PH=125)
        d = diag_for(seq, "sell", 120.0, (119.5, 120.0))
        self.assertFalse(d["matched"])
        self.assertEqual(d["blocking"], [])
        self.assertTrue(any(set(pr["keys"]) == {"body_break", "strict"} for pr in d["pairs"]), d["pairs"])
        self.assertIn("deux règles", " ".join(describe_diag(d)))

    def test_wrong_level_is_not_explained(self):
        d = diag_for(self.SELL_R, "sell", 50.0, (49.5, 50.0))
        self.assertFalse(d["matched"])
        self.assertEqual((d["blocking"], d["pairs"]), ([], []))
        self.assertIn("Aucune règle seule", " ".join(describe_diag(d)))

    def test_wrong_side_is_not_matched(self):
        d = diag_for(self.SELL_R, "buy", 120.0, (120.0, 120.5))
        self.assertFalse(d["matched"])

    def test_too_few_candles(self):
        st = {}
        rec = add_manual(st, "T", "M15", "sell", NOW, zone=(1, 2))
        d = diagnose(rec, with_times(self.SELL_R[:20], 900), P, base_sec=900)
        self.assertIn("error", d)
        self.assertIn("pas assez", " ".join(describe_diag(d)))


class TestOverrides(unittest.TestCase):
    def robot_setup(self):
        res = scan_series("T", "M15", with_times(build(100, 110, 104, 120, 112, 126, 108, 118.0), 900), Params(ext=3, atr_len=5))
        return res[0]

    def test_adjust_keeps_original_and_logs_feedback(self):
        s = self.robot_setup()
        st = {}
        adjust(st, s, "AB12", NOW, zone=(121.0, 119.0))
        ov = find_override(st, s.key)
        self.assertEqual((ov["zone_low"], ov["zone_high"], ov["entry"]), (119.0, 121.0, s.entry))
        self.assertEqual(ov["original"], {"zone_low": s.zone_low, "zone_high": s.zone_high, "entry": s.entry})
        adjust(st, s, "AB12", NOW + 5, entry=120.4)                       # second adjustment: original stays the robot's
        ov = find_override(st, s.key)
        self.assertEqual((ov["zone_low"], ov["zone_high"], ov["entry"]), (119.0, 121.0, 120.4))
        self.assertEqual(ov["original"]["zone_low"], s.zone_low)
        self.assertEqual(len(st["overrides"]), 1)
        self.assertEqual([f["type"] for f in st["feedback"]], ["modify", "modify"])
        self.assertEqual(st["feedback"][0]["orig"]["entry"], s.entry)
        self.assertEqual(len(st["feedback"][0]["zone_candle"]), 4)

    def test_apply_replaces_levels_and_recomputes_status(self):
        s = self.robot_setup()
        base = with_times(build(100, 110, 104, 120, 112, 126, 108, 118.0), 900)
        st = {}
        adjust(st, s, "AB12", NOW, zone=(124.0, 125.0), entry=124.0)
        other = replace(s, t_left=s.t_left + 1)
        out = apply_overrides([s, other], st, {"T": base}, None, 0.5)
        mod = [x for x in out if x.key == s.key][0]
        self.assertTrue(mod.adjusted)
        self.assertEqual((mod.zone_low, mod.zone_high, mod.entry), (124.0, 125.0, 124.0))
        self.assertEqual(mod.status, "detected")
        self.assertFalse([x for x in out if x.key == other.key][0].adjusted)
        near = apply_overrides([s], st, {"T": base}, {"T": 124.0 - 0.2 * s.atr}, 0.5)[0]
        self.assertEqual(near.status, "approaching")
        hit = apply_overrides([s], st, {"T": base}, {"T": 124.5}, 0.5)[0]
        self.assertEqual(hit.status, "entered")

    def test_touch_since_the_adjustment_and_invalidation(self):
        s = self.robot_setup()
        base = with_times(build(100, 110, 104, 120, 112, 126, 108, 118.0), 900)
        t_adj = base[-1][0] - 2 * 900
        st = {}
        adjust(st, s, "AB12", t_adj, zone=(117.0, 118.0), entry=117.0)        # price already went through 117 after t_adj
        out = apply_overrides([s], st, {"T": base}, None, 0.5)[0]
        self.assertEqual(out.status, "entered")
        st2 = {}
        adjust(st2, s, "AB12", base[0][0], zone=(100.0, 101.0), entry=100.0)  # many closes above 101 since then
        self.assertEqual(apply_overrides([s], st2, {"T": base}, None, 0.5)[0].status, "invalid")

    def test_buy_side_and_prune(self):
        res = scan_series("T", "M15", with_times(mirror(build(100, 110, 104, 120, 112, 126, 108, 118.0)), 900), Params(ext=3, atr_len=5))
        b = res[0]
        base = with_times(mirror(build(100, 110, 104, 120, 112, 126, 108, 118.0)), 900)
        st = {}
        adjust(st, b, "ZZ99", NOW, zone=(-125.0, -124.0), entry=-124.0)
        m = apply_overrides([b], st, {"T": base}, None, 0.5)[0]
        self.assertEqual((m.side, m.status, m.adjusted), ("buy", "detected", True))
        prune(st, NOW + 31 * 86400)
        self.assertEqual(st["overrides"], [])
        self.assertEqual(len(st["feedback"]), 1)

    def test_nothing_changes_without_overrides(self):
        s = self.robot_setup()
        self.assertEqual(apply_overrides([s], {}, {}, None, 0.5), [s])


if __name__ == "__main__":
    unittest.main()
