import json
import unittest

from helpers import make_setup
from qml_scanner import config
from qml_scanner.candles import TF_SECONDS
from qml_scanner.commands import execute, match_symbols, parse_command, strip_accents
from qml_scanner.dismiss import apply_dismissals, dismiss_opportunity, prune, restore
from qml_scanner.notify import empty_state, plan_notifications
from qml_scanner.selection import cluster, correlation_tags

NOW = 1_800_000_000


def world(*setups):
    opps = cluster(list(setups))
    correlation_tags(opps)
    ev, st = plan_notifications(opps, list(setups), empty_state())
    return opps, st


def by(opps, symbol, side=None):
    return [o for o in opps if o.symbol == symbol and (side is None or o.side == side)][0]


class TestSymbolMatching(unittest.TestCase):
    U = config.ALL_SYMBOLS

    def m(self, text):
        return match_symbols(strip_accents(text).lower(), self.U)

    def test_forex_and_metals(self):
        self.assertEqual(self.m("supprime eurusd"), ["EURUSD"])
        self.assertEqual(self.m("enlève EUR/USD h1"), ["EURUSD"])
        self.assertEqual(self.m("xau usd"), ["XAUUSD"])
        self.assertEqual(self.m("btcusd et ethusd"), ["BTCUSD", "ETHUSD"])

    def test_numbers_never_confuse_neighbours(self):
        self.assertEqual(self.m("boom 500"), ["Boom 500 Index"])
        self.assertEqual(self.m("boom 50"), ["Boom 50 Index"])
        self.assertEqual(self.m("boom 1000"), ["Boom 1000 Index"])
        self.assertEqual(self.m("crash 150"), ["Crash 150 Index"])
        self.assertEqual(self.m("crash 100 index"), [])          # not watched: never mistaken for Crash 1000 or 150
        self.assertEqual(self.m("jump 10"), ["Jump 10 Index"])
        self.assertEqual(self.m("jump 100"), ["Jump 100 Index"])
        self.assertEqual(self.m("volatility 150"), [])           # not on Deriv's public list: not watched
        self.assertEqual(self.m("volatility 150 (1s)"), [])
        self.assertEqual(self.m("volatility 15"), [])            # only the (1s) version is watched: must be named so
        self.assertEqual(self.m("volatility 15 (1s)"), ["Volatility 15 (1s) Index"])
        self.assertEqual(self.m("volatility 5"), [])
        self.assertEqual(self.m("volatility 50"), ["Volatility 50 Index"])

    def test_one_second_variants_are_distinct(self):
        self.assertEqual(self.m("volatility 75"), ["Volatility 75 Index"])
        self.assertEqual(self.m("volatility 75 (1s)"), ["Volatility 75 (1s) Index"])
        self.assertEqual(self.m("Volatility 75 (1s) Index"), ["Volatility 75 (1s) Index"])
        self.assertEqual(self.m("volatility 10 1s"), ["Volatility 10 (1s) Index"])

    def test_step_index(self):
        self.assertEqual(self.m("step index"), [])               # no number: ambiguous among the five, so none
        self.assertEqual(self.m("step index 100"), ["Step Index 100"])
        self.assertEqual(self.m("step 100"), ["Step Index 100"])
        self.assertEqual(self.m("step index 200"), ["Step Index 200"])
        self.assertEqual(self.m("step index 500"), ["Step Index 500"])
        self.assertEqual(self.m("step 300"), ["Step Index 300"])
        self.assertEqual(self.m("stepindex400"), ["Step Index 400"])
        self.assertEqual(self.m("volatility 25 index"), ["Volatility 25 Index"])
        self.assertEqual(self.m("volatility 25 index (1s)"), ["Volatility 25 (1s) Index"])


class TestParse(unittest.TestCase):
    def p(self, text, ids=("a3f7", "b9k2")):
        return parse_command(text, set(ids), config.ALL_SYMBOLS)

    def test_kinds(self):
        self.assertEqual(self.p("supprime A3F7").kind, "dismiss")
        self.assertEqual(self.p("Enlève l'opportunité EURUSD").kind, "dismiss")
        self.assertEqual(self.p("retire #a3f7").kind, "dismiss")
        self.assertEqual(self.p("efface xauusd").kind, "dismiss")
        self.assertEqual(self.p("rétablis A3F7").kind, "restore")
        self.assertEqual(self.p("annule la suppression de A3F7").kind, "restore")
        self.assertEqual(self.p("liste des opportunités supprimées").kind, "list_dismissed")
        self.assertEqual(self.p("montre les supprimées").kind, "list_dismissed")
        self.assertEqual(self.p("bonjour robot").kind, "unknown")
        self.assertEqual(self.p("").kind, "unknown")

    def test_ids_tf_side_all(self):
        p = self.p("supprime #A3F7 et b9k2")
        self.assertEqual(p.ids, ["a3f7", "b9k2"])
        for text, tf in (("supprime eurusd h1", "H1"), ("supprime eurusd 15m", "M15"), ("supprime eurusd 4h", "H4"),
                         ("supprime eurusd 45 min", "M45"), ("supprime eurusd d1", "D1"), ("supprime eurusd 1j", "D1"),
                         ("supprime eurusd", None)):
            self.assertEqual(self.p(text).tf, tf, text)
        self.assertEqual(self.p("supprime eurusd vente").side, "sell")
        self.assertEqual(self.p("supprime eurusd achat").side, "buy")
        self.assertEqual(self.p("supprime eurusd").side, None)
        self.assertTrue(self.p("supprime tout gbpusd").all)
        self.assertFalse(self.p("supprime gbpusd").all)

    def test_reason_is_split_and_not_used_for_targeting(self):
        p = self.p("supprime A3F7 car EURUSD est en range sur h4")
        self.assertEqual(p.ids, ["a3f7"])
        self.assertEqual(p.symbols, [])
        self.assertEqual(p.tf, None)
        self.assertIn("eurusd est en range", p.reason)
        self.assertEqual(self.p("enlève xauusd : pas de vraie cassure").reason, "pas de vraie cassure")
        self.assertEqual(self.p("enlève xauusd - structure sale").reason, "structure sale")
        self.assertEqual(self.p("enlève xauusd parce que c'est faux").reason, "c'est faux")

    def test_unknown_id_like_words_are_not_ids(self):
        p = self.p("supprime xauusd avec stop")
        self.assertEqual(p.ids, [])


class TestExecuteDismiss(unittest.TestCase):
    def setUp(self):
        self.setups = [
            make_setup(symbol="EURUSD", side="sell", tf="H1", t_left=1),
            make_setup(symbol="EURUSD", side="buy", tf="H4", t_left=2, lo=90, hi=91, entry=90.5),
            make_setup(symbol="XAUUSD", side="sell", tf="H1", t_left=3),
            make_setup(symbol="Volatility 75 Index", side="buy", tf="M30", t_left=4),
            make_setup(symbol="Volatility 75 (1s) Index", side="buy", tf="M30", t_left=5),
            make_setup(symbol="Boom 500 Index", side="buy", tf="H2", t_left=6),
        ]
        self.opps, self.st = world(*self.setups)

    def run_cmd(self, text):
        return execute(text, self.st, self.opps, NOW)

    def test_by_id_any_case_with_hash_in_a_sentence(self):
        o = by(self.opps, "XAUUSD")
        msg, changed = self.run_cmd("Salut, supprime #%s s'il te plait" % o.id.lower())
        self.assertTrue(changed)
        self.assertIn("Supprimée", msg)
        self.assertIn("XAUUSD", msg)
        self.assertEqual(len(self.st["dismissed"]), 1)
        self.assertNotIn(o.cid, self.st["clusters"])

    def test_by_symbol_when_unique(self):
        msg, changed = self.run_cmd("enlève XAUUSD")
        self.assertTrue(changed)
        self.assertEqual(self.st["dismissed"][0]["symbol"], "XAUUSD")

    def test_ambiguous_symbol_removes_nothing_and_lists_ids(self):
        msg, changed = self.run_cmd("supprime EURUSD")
        self.assertFalse(changed)
        self.assertEqual(self.st["dismissed"], [])
        self.assertIn("Plusieurs", msg)
        for o in self.opps:
            if o.symbol == "EURUSD":
                self.assertIn(o.id, msg)

    def test_side_and_timeframe_narrow_the_target(self):
        _, c1 = self.run_cmd("supprime eurusd vente")
        self.assertTrue(c1)
        self.assertEqual([d["side"] for d in self.st["dismissed"]], ["sell"])
        _, c2 = self.run_cmd("supprime eurusd h4")
        self.assertTrue(c2)
        self.assertEqual(len(self.st["dismissed"]), 2)

    def test_all_flag_removes_every_match(self):
        msg, changed = self.run_cmd("supprime tout EURUSD")
        self.assertTrue(changed)
        self.assertEqual(len(self.st["dismissed"]), 2)
        self.assertEqual(msg.count("Supprimée"), 2)

    def test_confluence_timeframe_can_be_used(self):
        a = make_setup(symbol="GBPUSD", side="sell", tf="H4", t_left=1, lo=100, hi=102, entry=101)
        b = make_setup(symbol="GBPUSD", side="sell", tf="M15", t_left=2, lo=100.5, hi=101.5, entry=101, atr=0.3)
        opps, st = world(a, b)
        self.assertEqual(len(opps), 1)
        msg, changed = execute("supprime gbpusd m15", st, opps, NOW)
        self.assertTrue(changed)

    def test_variant_names_are_not_mixed(self):
        _, changed = self.run_cmd("supprime volatility 75 (1s)")
        self.assertTrue(changed)
        self.assertEqual([d["symbol"] for d in self.st["dismissed"]], ["Volatility 75 (1s) Index"])
        _, changed = self.run_cmd("supprime volatility 75")
        self.assertTrue(changed)
        self.assertEqual(sorted(d["symbol"] for d in self.st["dismissed"]), ["Volatility 75 (1s) Index", "Volatility 75 Index"])

    def test_reason_is_stored_in_dismissal_and_feedback(self):
        o = by(self.opps, "Boom 500 Index")
        msg, _ = self.run_cmd("supprime %s car cassure sans vraie structure" % o.id)
        self.assertIn("Raison notée : cassure sans vraie structure", msg)
        d = self.st["dismissed"][0]
        self.assertEqual(d["reason"], "cassure sans vraie structure")
        fb = self.st["feedback"][-1]
        self.assertEqual((fb["type"], fb["id"], fb["reason"]), ("dismiss", o.id, "cassure sans vraie structure"))
        self.assertEqual(fb["setup"]["symbol"], "Boom 500 Index")
        self.assertIn("entry", fb["setup"])

    def test_error_messages(self):
        msg, changed = self.run_cmd("supprime ZZZ9")
        self.assertFalse(changed)
        msg, changed = self.run_cmd("supprime")
        self.assertFalse(changed)
        self.assertIn("l'ID", msg)
        msg, changed = self.run_cmd("supprime GBPUSD")           # known asset, nothing active on it
        self.assertFalse(changed)
        self.assertIn("Aucune opportunité", msg)
        msg, changed = self.run_cmd("bonjour")
        self.assertFalse(changed)
        self.assertIn("Exemples", msg)
        msg, changed = self.run_cmd("supprime A3F7 B9K2")
        self.assertFalse(changed)

    def test_known_and_unknown_ids_together(self):
        o = by(self.opps, "XAUUSD")
        msg, changed = self.run_cmd("supprime %s et QQQQ" % o.id)
        self.assertTrue(changed)
        self.assertEqual(len(self.st["dismissed"]), 1)

    def test_state_stays_json_serialisable(self):
        self.run_cmd("supprime xauusd car test")
        st2 = json.loads(json.dumps(self.st))
        self.assertEqual(st2["dismissed"][0]["symbol"], "XAUUSD")


class TestRestoreAndList(unittest.TestCase):
    def test_restore_flow(self):
        opps, st = world(make_setup(symbol="XAUUSD", t_left=1), make_setup(symbol="EURUSD", t_left=2, lo=90, hi=91, entry=90.5))
        o = by(opps, "XAUUSD")
        execute("supprime %s car bruit" % o.id, st, opps, NOW)
        msg, ch = execute("liste des supprimées", st, opps, NOW)
        self.assertFalse(ch)
        self.assertIn(o.id, msg)
        self.assertIn("bruit", msg)
        msg, ch = execute("rétablis %s" % o.id, st, opps, NOW + 5)
        self.assertTrue(ch)
        self.assertIn("Rétablie", msg)
        self.assertEqual(st["dismissed"], [])
        self.assertEqual(st["feedback"][-1]["type"], "restore")
        msg, ch = execute("rétablis %s" % o.id, st, opps, NOW + 6)
        self.assertFalse(ch)
        msg, ch = execute("liste des supprimées", st, opps, NOW)
        self.assertIn("Aucune opportunité supprimée", msg)

    def test_restore_by_symbol(self):
        opps, st = world(make_setup(symbol="XAUUSD", t_left=1))
        execute("supprime xauusd", st, opps, NOW)
        msg, ch = execute("remets xauusd", st, opps, NOW + 1)
        self.assertTrue(ch)
        self.assertEqual(st["dismissed"], [])


class TestDismissalPersistence(unittest.TestCase):
    def test_same_setups_stay_hidden_and_no_closed_event(self):
        s = make_setup(symbol="XAUUSD", t_left=1, status="approaching")
        opps, st = world(s)
        execute("supprime xauusd", st, opps, NOW)
        visible = apply_dismissals([s], st)
        self.assertEqual(visible, [])
        opps2 = cluster(visible)
        ev, st2 = plan_notifications(opps2, visible, st)
        self.assertEqual(ev, [])                                    # silently gone: no "closed" notification
        self.assertEqual(st2["clusters"], {})
        self.assertEqual(len(st2["dismissed"]), 1)

    def test_old_overlapping_setup_hidden_but_new_information_passes(self):
        h1 = make_setup(symbol="XAUUSD", tf="H1", t_left=1, lo=100, hi=101, entry=100.5)
        h1.t_detect = NOW - 7200
        opps, st = world(h1)
        execute("supprime xauusd", st, opps, NOW)
        # a higher-timeframe setup already closed before the dismissal, overlapping: same opportunity -> hidden
        old_h4 = make_setup(symbol="XAUUSD", tf="H4", t_left=50, lo=100.2, hi=100.9, entry=100.5)
        old_h4.t_detect = NOW - 3 * 3600 - TF_SECONDS["H4"] + 3600       # candle closed 3 h before... (open + 4h <= NOW)
        old_h4.t_detect = NOW - TF_SECONDS["H4"] - 600
        # detection candle closes AFTER the dismissal: new information
        new_h4 = make_setup(symbol="XAUUSD", tf="H4", t_left=60, lo=100.2, hi=100.9, entry=100.5)
        new_h4.t_detect = NOW - 600
        # far away zone: unrelated
        far = make_setup(symbol="XAUUSD", tf="H1", t_left=70, lo=120, hi=121, entry=120.5)
        far.t_detect = NOW - 7200
        # other side / other symbol: unrelated
        other_side = make_setup(symbol="XAUUSD", side="buy", tf="H1", t_left=80, lo=100, hi=101, entry=100.5)
        other_side.t_detect = NOW - 7200
        other_sym = make_setup(symbol="EURUSD", tf="H1", t_left=90, lo=100, hi=101, entry=100.5)
        other_sym.t_detect = NOW - 7200
        vis = apply_dismissals([h1, old_h4, new_h4, far, other_side, other_sym], st)
        keys = {s.key for s in vis}
        self.assertNotIn(h1.key, keys)
        self.assertNotIn(old_h4.key, keys)
        self.assertEqual(keys, {new_h4.key, far.key, other_side.key, other_sym.key})

    def test_dismissed_ids_can_be_pruned_after_ttl(self):
        opps, st = world(make_setup(symbol="XAUUSD", t_left=1))
        execute("supprime xauusd", st, opps, NOW)
        prune(st, NOW + 29 * 86400)
        self.assertEqual(len(st["dismissed"]), 1)
        prune(st, NOW + 31 * 86400)
        self.assertEqual(st["dismissed"], [])

    def test_feedback_log_is_capped(self):
        opps, st = world(make_setup(symbol="XAUUSD", t_left=1))
        o = opps[0]
        for i in range(520):
            dismiss_opportunity(st, o, NOW + i, "r%d" % i)
        self.assertEqual(len(st["feedback"]), 500)
        self.assertEqual(len(st["dismissed"]), 1)          # same id: one record
        self.assertEqual(st["feedback"][-1]["reason"], "r519")

    def test_restore_function(self):
        opps, st = world(make_setup(symbol="XAUUSD", t_left=1))
        dismiss_opportunity(st, opps[0], NOW, "x")
        self.assertIsNone(restore(st, "NOPE", NOW))
        self.assertIsNotNone(restore(st, opps[0].id.lower(), NOW))


if __name__ == "__main__":
    unittest.main()
