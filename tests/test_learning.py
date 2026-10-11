import json
import random
import unittest

from helpers import make_history, make_setup
from qml_scanner import config, learning
from qml_scanner.qml_core import Params
from qml_scanner.selection import cluster

NOW = 1_800_000_000


def setup_dict(head_excess, atr=1.0, side="sell", zone=(100.0, 101.0), disp=2.0, leg=8.0):
    return {"atr": atr, "head": 100 + head_excess, "shoulder": 100.0, "leg": leg, "zone_low": zone[0], "zone_high": zone[1], "disp": disp,
            "side": side}


def ledger_rec(i, head_excess, tf="H1", kind="OB", typ="QML-R", cat="Forex", side="sell", symbol="EURUSD"):
    return {"id": "L%03d" % i, "t": NOW, "symbol": symbol, "category": cat, "side": side, "tf": tf, "type": typ, "kind": kind,
            "manual": False, "head_excess_atr": head_excess, "leg_atr": 8.0, "zone_h_atr": 1.0, "disp_atr": 2.0}


def state_with_dismissals(n_dis=12, n_keep=30, seed=1, dis_range=(0.02, 0.2), keep_range=(0.8, 1.6)):
    r = random.Random(seed)
    st = {"feedback": [], "ledger": [], "manual": [], "params": {"global": {}, "filters": {}}, "suggestions": []}
    for i in range(n_dis):
        he = r.uniform(*dis_range)
        st["ledger"].append(ledger_rec(i, he, tf="M15"))
        st["feedback"].append({"type": "dismiss", "t": NOW, "id": "L%03d" % i, "reason": "tête trop petite, double creux", "setup": setup_dict(he)})
    for j in range(n_keep):
        st["ledger"].append(ledger_rec(100 + j, r.uniform(*keep_range), tf="H4"))
    return st


class TestFeaturesAndParams(unittest.TestCase):
    def test_features(self):
        s = make_setup(atr=2.0, lo=100, hi=102, leg_=20, disp=4.0)
        f = learning.features(s)
        self.assertAlmostEqual(f["zone_h_atr"], 1.0)
        self.assertAlmostEqual(f["leg_atr"], 10.0)
        self.assertAlmostEqual(f["disp_atr"], 2.0)
        self.assertAlmostEqual(f["head_excess_atr"], abs(s.head - s.shoulder) / 2.0)
        self.assertEqual(learning.features(make_setup(atr=0.0)), {k: 0.0 for k in learning.FEATURES})
        d = setup_dict(0.5, atr=2.0)
        self.assertAlmostEqual(learning.features_from_dict(d)["head_excess_atr"], 0.25)

    def test_params_from_state_only_allowed_keys(self):
        st = {"params": {"global": {"strict": False, "ext": 7, "evil": 1, "body_break": False}, "filters": {}}}
        p = learning.params_from_state(st)
        self.assertEqual((p.strict, p.ext, p.body_break), (False, 7, False))
        self.assertFalse(hasattr(p, "evil"))
        self.assertEqual(learning.params_from_state({}).ext, Params().ext)

    def test_filters(self):
        a = make_setup(symbol="A", t_left=1)          # head_excess = |head - shoulder| / atr = 5.5 / 1
        b = make_setup(symbol="B", t_left=2, atr=20.0)
        manual = make_setup(symbol="C", t_left=3, atr=20.0, kind="MANUEL")
        out = learning.apply_filters([a, b, manual], {"head_excess_atr": [">=", 1.0]})
        self.assertEqual([s.symbol for s in out], ["A", "C"])           # manual setups bypass the filters
        out = learning.apply_filters([a, b, manual], {"head_excess_atr": ["<=", 1.0]})
        self.assertEqual([s.symbol for s in out], ["B", "C"])
        self.assertEqual(len(learning.apply_filters([a, b], {})), 2)
        self.assertEqual(len(learning.apply_filters([a], {"unknown": [">=", 5]})), 1)

    def test_record_new_and_cap(self):
        opp = cluster([make_setup()])[0]
        opp.id = "ZZ11"
        st = {}
        for _ in range(2100):
            learning.record_new(st, opp, NOW)
        self.assertEqual(len(st["ledger"]), learning.LEDGER_MAX)
        r = st["ledger"][-1]
        self.assertEqual((r["id"], r["symbol"], r["category"], r["manual"]), ("ZZ11", "EURUSD", "Forex", False))
        self.assertIn("head_excess_atr", r)


class TestStudyDismissals(unittest.TestCase):
    def test_no_data(self):
        st = {}
        res = learning.study(st)
        self.assertEqual((res["n_dismissed"], res["suggestions"]), (0, []))
        self.assertIn("Pas encore de données", learning.format_study(res))

    def test_not_enough_samples_gives_no_suggestion(self):
        st = state_with_dismissals(n_dis=5, n_keep=30)
        res = learning.study(st, min_n=8)
        self.assertFalse(res["features_enough"])
        self.assertEqual(res["suggestions"], [])
        self.assertIn("pas assez de données", learning.format_study(res))

    def test_clear_pattern_gives_a_filter_suggestion(self):
        st = state_with_dismissals()
        res = learning.study(st)
        self.assertTrue(res["features_enough"])
        sg = [s for s in res["suggestions"] if s["kind"] == "filter" and s["key"] == "head_excess_atr"]
        self.assertEqual(len(sg), 1)
        self.assertEqual(sg[0]["op"], ">=")
        self.assertTrue(0.2 <= sg[0]["value"] <= 0.8, sg[0]["value"])
        self.assertEqual([s["key"] for s in res["suggestions"]], ["head_excess_atr"])      # the other features carry no signal
        self.assertIn("100 %", sg[0]["text"])
        self.assertEqual(res["suggestions"][0]["n"], 1)
        self.assertEqual(st["suggestions"], res["suggestions"])
        txt = learning.format_study(res)
        self.assertIn("SUGGESTIONS", txt)
        self.assertIn("applique 1", txt)
        self.assertIn("double", txt)             # words of his reasons
        self.assertIn("creux", txt)

    def test_no_pattern_no_suggestion(self):
        st = state_with_dismissals(dis_range=(0.5, 1.5), keep_range=(0.5, 1.5), seed=3)
        self.assertEqual([s for s in learning.study(st)["suggestions"] if s["kind"] == "filter"], [])

    def test_pure_noise_never_produces_a_filter(self):
        for seed in range(30):
            r = random.Random(seed)
            st = {"feedback": [], "ledger": [], "manual": [], "params": {"global": {}, "filters": {}}, "suggestions": []}
            for i in range(10):
                he = r.uniform(0.0, 2.0)
                st["ledger"].append(ledger_rec(i, he))
                st["feedback"].append({"type": "dismiss", "t": NOW, "id": "L%03d" % i, "reason": "", "setup": setup_dict(he)})
            for j in range(40):
                st["ledger"].append(ledger_rec(100 + j, r.uniform(0.0, 2.0)))
            self.assertEqual(learning.study(st)["suggestions"], [], seed)

    def test_where_he_removes(self):
        st = state_with_dismissals()
        res = learning.study(st)
        self.assertIn("timeframe", res["removal_by_group"])
        self.assertEqual(res["removal_by_group"]["timeframe"][0][0], "M15")


class TestStudyModifications(unittest.TestCase):
    def mod(self, i, side="sell", dist_shift=0.5, prox_shift=0.0, kind="OB", candle=None, atr=2.0):
        d = 1 if side == "sell" else -1
        lo, hi = 100.0, 104.0
        if side == "sell":
            new = {"zone_low": lo + prox_shift * atr, "zone_high": hi + dist_shift * atr, "entry": lo + prox_shift * atr}
        else:
            new = {"zone_low": lo - dist_shift * atr, "zone_high": hi - prox_shift * atr, "entry": hi - prox_shift * atr}
        orig = {"zone_low": lo, "zone_high": hi, "entry": lo if side == "sell" else hi}
        return {"type": "modify", "t": NOW, "id": "M%d" % i, "symbol": "X", "side": side, "tf": "H1", "kind": kind, "atr": atr,
                "orig": orig, "new": new, "zone_candle": candle or [101.0, 105.0, 99.0, 103.0]}

    def test_systematic_bias_is_reported_for_both_sides(self):
        st = {"feedback": [self.mod(i, "sell") for i in range(4)] + [self.mod(10 + i, "buy") for i in range(4)]}
        res = learning.study(st, min_mod=5)
        self.assertEqual(res["modified"]["n"], 8)
        fe = res["modified"]["far_edge"]
        self.assertAlmostEqual(fe["mean"], 0.5, 6)                      # outward on both sides, same sign after mirroring
        self.assertEqual(fe["same_sign"], 1.0)
        self.assertTrue(any("bord lointain vers l'extérieur" in b for b in res["zone_bias"]), res.get("zone_bias"))

    def test_few_or_mixed_shifts_report_nothing(self):
        st = {"feedback": [self.mod(i, dist_shift=0.5) for i in range(3)]}
        self.assertNotIn("zone_bias", learning.study(st, min_mod=5))
        st = {"feedback": [self.mod(i, dist_shift=(0.5 if i % 2 else -0.5)) for i in range(10)]}
        self.assertNotIn("zone_bias", learning.study(st, min_mod=5))

    def test_ob_body_suggestion_when_he_prefers_the_body(self):
        # candle (o,h,l,c) = (101,105,99,103): sell wick-top 105, body-top 103 ; his far edge = 104 + shift*atr
        mods = [self.mod(i, dist_shift=-0.5, candle=[101.0, 105.0, 99.0, 103.0]) for i in range(6)]   # far edge 103 = body
        res = learning.study({"feedback": mods}, min_mod=5)
        self.assertEqual(res["modified"]["ob_n"], 6)
        self.assertEqual(res["modified"]["ob_body_share"], 1.0)
        self.assertEqual([(s["kind"], s["key"], s["value"]) for s in res["suggestions"]], [("param", "ob_body", True)])
        wick = [self.mod(i, dist_shift=0.5, candle=[101.0, 105.0, 99.0, 103.0]) for i in range(6)]     # far edge 105 = wick
        self.assertEqual(learning.study({"feedback": wick}, min_mod=5)["suggestions"], [])
        already = Params(ob_body=True)
        self.assertEqual(learning.study({"feedback": mods}, min_mod=5, params=already)["suggestions"], [])

    def test_buy_ob_uses_the_low_side(self):
        mods = [self.mod(i, side="buy", dist_shift=-0.5, candle=[103.0, 105.0, 99.0, 101.0]) for i in range(6)]
        res = learning.study({"feedback": mods}, min_mod=5)
        self.assertEqual(res["modified"]["ob_n"], 6)


class TestStudyManual(unittest.TestCase):
    def manual(self, i, matched=False, blocking=(), pairs=(), ext=()):
        return {"id": "P%d" % i, "diag": {"matched": matched, "blocking": [{"key": k, "value": v, "label": k} for k, v in blocking],
                                           "pairs": [{"keys": [a for a, _ in pr], "values": [v for _, v in pr], "labels": []} for pr in pairs],
                                           "ext": list(ext), "delta": {"zone_low": 0.1, "zone_high": 0.3, "entry": 0.0, "kind": "OB", "type": "QML-R"} if matched else None}}

    def test_repeated_blocking_rule_becomes_a_suggestion(self):
        st = {"manual": [self.manual(i, blocking=[("body_break", False)]) for i in range(4)] + [self.manual(9, matched=True)]}
        res = learning.study(st)
        self.assertEqual((res["manual"]["n"], res["manual"]["matched"], res["manual"]["missed"]), (5, 1, 4))
        self.assertEqual([(s["key"], s["value"]) for s in res["suggestions"]], [("body_break", False)])
        self.assertIn("4 de tes 4", res["suggestions"][0]["text"])
        self.assertIn("Tes setups proposés : 5", learning.format_study(res))

    def test_not_enough_misses_or_already_applied(self):
        st = {"manual": [self.manual(i, blocking=[("body_break", False)]) for i in range(2)]}
        self.assertEqual(learning.study(st)["suggestions"], [])
        st = {"manual": [self.manual(i, blocking=[("body_break", False)]) for i in range(4)]}
        self.assertEqual(learning.study(st, params=Params(body_break=False))["suggestions"], [])

    def test_pairs_and_pivot_length(self):
        st = {"manual": [self.manual(i, pairs=[[("body_break", False), ("strict", False)]]) for i in range(3)]}
        keys = sorted(s["key"] for s in learning.study(st)["suggestions"])
        self.assertEqual(keys, ["body_break", "strict"])
        st = {"manual": [self.manual(i, ext=[5, 7]) for i in range(4)]}
        res = learning.study(st)
        self.assertTrue(any(s["key"] == "ext" for s in res["suggestions"]))

    def test_mixed_reasons_below_threshold(self):
        st = {"manual": [self.manual(0, blocking=[("body_break", False)]), self.manual(1, blocking=[("strict", False)]),
                         self.manual(2, blocking=[("req_mom", False)]), self.manual(3, blocking=[("midzone", True)])]}
        self.assertEqual(learning.study(st)["suggestions"], [])


class TestApplyAndHistoryImpact(unittest.TestCase):
    def test_apply_param_filter_show_reset(self):
        st = state_with_dismissals()
        learning.study(st)
        ok, msg = learning.apply_suggestion(st, 1, NOW)
        self.assertTrue(ok)
        self.assertIn("Réglage activé", msg)
        self.assertEqual(list(st["params"]["filters"]), ["head_excess_atr"])
        self.assertIn("filtre", learning.show_params(st))
        st["suggestions"].append({"n": 7, "kind": "param", "key": "body_break", "value": False, "text": "x"})
        ok, _ = learning.apply_suggestion(st, 7, NOW)
        self.assertTrue(ok)
        self.assertIs(st["params"]["global"]["body_break"], False)
        self.assertFalse(learning.params_from_state(st).body_break)
        self.assertEqual([f["type"] for f in st["feedback"][-2:]], ["apply", "apply"])
        ok, msg = learning.apply_suggestion(st, 99, NOW)
        self.assertFalse(ok)
        self.assertIn("pas de suggestion", msg)
        json.dumps(st)
        learning.reset_params(st, NOW)
        self.assertEqual(st["params"], {"global": {}, "filters": {}})
        self.assertIn("Aucun réglage", learning.show_params(st))

    def test_invalid_value_is_rejected(self):
        st = {"suggestions": [{"n": 1, "kind": "param", "key": "ext", "value": "dix", "text": "x"}]}
        ok, msg = learning.apply_suggestion(st, 1, NOW)
        self.assertFalse(ok)
        self.assertEqual(st["params"]["global"], {})

    def test_impact_on_current_data(self):
        hist = make_history(["EURUSD", "XAUUSD", "Volatility 75 Index"], days=60, seed0=40)
        st = {"manual": [TestStudyManual().manual(i, blocking=[("body_break", False)]) for i in range(4)]}
        res = learning.study(st, history=hist)
        sg = res["suggestions"][0]
        self.assertIsNotNone(sg["impact"])
        self.assertGreaterEqual(sg["impact"], 0.0)                 # relaxing a rule can never remove setups
        self.assertIn("effet sur les données actuelles", sg["text"])


if __name__ == "__main__":
    unittest.main()
