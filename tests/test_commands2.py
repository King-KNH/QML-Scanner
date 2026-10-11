import json
import unittest

from helpers import build, build_wick, make_history, make_setup, with_times
from qml_scanner import config, learning
from qml_scanner.commands import execute, extract_levels, parse_command, strip_accents
from qml_scanner.notify import empty_state, plan_notifications
from qml_scanner.scanner import run_cycle
from qml_scanner.selection import cluster, correlation_tags

NOW = 1_800_000_000


def parse(text, ids=("a3f7",)):
    return parse_command(text, set(ids), config.ALL_SYMBOLS)


def levels(text, names=()):
    return extract_levels(strip_accents(text).lower(), names)


class TestLevels(unittest.TestCase):
    def test_keywords(self):
        lv = levels("zone 4190 4199.4 ligne 4192 tête 4221 support 4165 cassure 4150")
        self.assertEqual(lv, {"zone": (4190.0, 4199.4), "entry": 4192.0, "head": 4221.0, "support": 4165.0, "extreme": 4150.0})

    def test_order_decimal_comma_and_reversed_zone(self):
        lv = levels("ligne 1,0860 zone de 1,0870 à 1,0850")
        self.assertEqual(lv, {"entry": 1.086, "zone": (1.085, 1.087)})
        self.assertEqual(levels("entrée 4192"), {"entry": 4192.0})

    def test_numbers_in_names_and_timeframes_are_ignored(self):
        self.assertEqual(levels("Volatility 75 Index h1 vente zone 450.2 455.1", ["Volatility 75 Index"]), {"zone": (450.2, 455.1)})
        self.assertEqual(levels("boom 500 index 15m zone 10 11", ["Boom 500 Index"]), {"zone": (10.0, 11.0)})
        self.assertEqual(levels("xauusd h4 4190 4199", ["XAUUSD"]), {"zone": (4190.0, 4199.0)})     # two bare numbers = zone

    def test_incomplete_or_ambiguous(self):
        self.assertEqual(levels("zone 4190"), {})
        self.assertEqual(levels("4190"), {})
        self.assertEqual(levels("rien du tout"), {})


class TestKinds(unittest.TestCase):
    def test_new_kinds(self):
        self.assertEqual(parse("bilan").kind, "study")
        self.assertEqual(parse("qu'est-ce que tu as appris ?").kind, "study")
        self.assertEqual(parse("fais le bilan de mes corrections").kind, "study")
        self.assertEqual(parse("applique 2").kind, "apply")
        self.assertEqual(parse("applique la suggestion 1 et 3").numbers, [1, 3])
        self.assertEqual(parse("montre les réglages").kind, "params")
        self.assertEqual(parse("réinitialise les réglages").kind, "reset_params")
        self.assertEqual(parse("remets les réglages par défaut").kind, "reset_params")
        self.assertEqual(parse("ajuste A3F7 zone 4190 4199").kind, "modify")
        self.assertEqual(parse("corrige la zone de XAUUSD zone 4190 4199").kind, "modify")
        self.assertEqual(parse("ajoute XAUUSD H1 vente zone 4190 4199").kind, "add")
        self.assertEqual(parse("j'ai repéré un setup sur EURUSD").kind, "add")
        self.assertEqual(parse("propose XAUUSD h1 achat ligne 4100").kind, "add")

    def test_old_kinds_are_unchanged(self):
        self.assertEqual(parse("supprime A3F7").kind, "dismiss")
        self.assertEqual(parse("remets A3F7").kind, "restore")
        self.assertEqual(parse("rétablis A3F7").kind, "restore")
        self.assertEqual(parse("liste des supprimées").kind, "list_dismissed")
        self.assertEqual(parse("bonjour").kind, "unknown")

    def test_add_parses_symbol_tf_side_levels(self):
        p = parse("ajoute Volatility 75 (1s) Index M30 achat zone 450.2 455.1 ligne 455 car vu à l'oeil")
        self.assertEqual((p.kind, p.symbols, p.tf, p.side), ("add", ["Volatility 75 (1s) Index"], "M30", "buy"))
        self.assertEqual(p.levels, {"zone": (450.2, 455.1), "entry": 455.0})
        self.assertEqual(p.reason, "vu à l'oeil".replace("à", "a"))


def world(symbols=("EURUSD", "XAUUSD", "Boom 500 Index", "Crash 300 Index", "Volatility 75 Index", "GBPUSD", "Jump 25 Index"), days=90):
    hist = make_history(list(symbols), days=days, seed0=0)
    now = max(c[-1][0] for c in hist.values()) + 300
    res = run_cycle(hist, empty_state(), now)
    return hist, now, res


class TestAdd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hist, cls.now, cls.res = world()
        cls.price = cls.hist["EURUSD"][-1][4]

    def fresh(self):
        return json.loads(json.dumps(self.res.state))

    def test_adds_and_diagnoses(self):
        st = self.fresh()
        far = self.price * 1.2
        msg, ch = execute("ajoute EURUSD H1 vente zone %.4f %.4f ligne %.4f" % (far, far + 0.5, far), st, self.res.opportunities, self.now,
                          history=self.hist)
        self.assertTrue(ch)
        self.assertIn("Setup enregistré", msg)
        self.assertEqual(len(st["manual"]), 1)
        rec = st["manual"][0]
        self.assertIn(rec["id"], msg)
        self.assertIsNotNone(rec["diag"])
        self.assertIn("ne l'avait pas repéré", msg)
        self.assertEqual(st["feedback"][-1]["type"], "manual")
        json.dumps(st)

    def test_validation_messages(self):
        st = self.fresh()
        ex = lambda t: execute(t, st, self.res.opportunities, self.now, history=self.hist)
        for text, needle in (("ajoute H1 vente zone 1 2", "l'actif"), ("ajoute EURUSD GBPUSD H1 vente zone 1 2", "Un seul actif"),
                             ("ajoute EURUSD vente zone 1 2", "timeframe"), ("ajoute EURUSD M5 vente zone 1 2", "abandonné"),
                             ("ajoute EURUSD H1 zone 1 2", "sens"), ("ajoute EURUSD H1 vente", "au moins la zone"),
                             ("ajoute EURUSD H1 vente zone 1.10 1.11 tête 1.05", "tête"),
                             ("ajoute EURUSD H1 achat zone 1.10 1.11 support 1.05", "support"),
                             ("ajoute EURUSD H1 vente ligne 1.10 support 1.0 cassure 1.05", "cassure")):
            msg, ch = ex(text)
            self.assertFalse(ch, text)
            self.assertIn(needle, msg, text)
        self.assertEqual(st["manual"], [] if "manual" in st else [])

    def test_merges_with_an_existing_opportunity(self):
        o = [x for x in self.res.opportunities if x.side == "sell" or x.side == "buy"][0]
        st = self.fresh()
        s = o.primary
        text = "ajoute %s %s %s zone %.5f %.5f" % (o.symbol, s.tf, "vente" if o.side == "sell" else "achat", s.zone_low, s.zone_high)
        msg, ch = execute(text, st, self.res.opportunities, self.now, history=self.hist)
        self.assertTrue(ch, msg)
        self.assertIn("[%s]" % o.id, msg)
        self.assertIn("regroupées", msg)

    def test_end_to_end_manual_setup_is_followed_and_outranks_the_robot(self):
        st = self.fresh()
        o = self.res.opportunities[0]
        s = o.primary
        side_fr = "vente" if o.side == "sell" else "achat"
        text = "ajoute %s %s %s zone %.5f %.5f ligne %.5f" % (o.symbol, s.tf, side_fr, s.zone_low, s.zone_high, s.entry)
        execute(text, st, self.res.opportunities, self.now, history=self.hist)
        r2 = run_cycle(self.hist, st, self.now + 900)
        same = [x for x in r2.opportunities if x.symbol == o.symbol and x.side == o.side and x.primary.zone_kind == "MANUEL"]
        self.assertEqual(len(same), 1)
        self.assertIn("✋", "\n".join(r2.pinned))
        self.assertEqual(r2.opportunities.count(same[0]), 1)
        r3 = run_cycle(self.hist, r2.state, self.now + 1800)
        self.assertEqual(r3.events, [])
        # he can delete it like any other opportunity
        msg, ch = execute("supprime %s car test" % same[0].id, r3.state, r3.opportunities, self.now + 1900)
        self.assertTrue(ch)
        r4 = run_cycle(self.hist, r3.state, self.now + 2700)
        self.assertFalse([x for x in r4.opportunities if x.primary.zone_kind == "MANUEL"])


class TestModify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hist, cls.now, cls.res = world()

    def fresh(self):
        return json.loads(json.dumps(self.res.state))

    def single(self):
        counts = {}
        for o in self.res.opportunities:
            counts[o.symbol] = counts.get(o.symbol, 0) + 1
        return [o for o in self.res.opportunities if counts[o.symbol] == 1][0]

    def test_adjust_by_id_then_followed_in_the_next_cycle(self):
        st = self.fresh()
        o = self.single()
        s = o.primary
        new_lo, new_hi = s.zone_low + 0.1 * s.atr, s.zone_high + 0.3 * s.atr
        msg, ch = execute("ajuste %s zone %.6f %.6f ligne %.6f car trop large" % (o.id, new_lo, new_hi, s.entry), st, self.res.opportunities, self.now)
        self.assertTrue(ch, msg)
        self.assertIn("Ajusté", msg)
        self.assertIn("Avant", msg)
        self.assertIn("trop large", msg)
        self.assertEqual(len(st["overrides"]), 1)
        r2 = run_cycle(self.hist, st, self.now + 900)
        adj = [x for x in r2.all_setups if x.key == s.key]
        self.assertEqual(len(adj), 1)
        self.assertTrue(adj[0].adjusted)
        self.assertAlmostEqual(adj[0].zone_low, new_lo, 5)
        self.assertAlmostEqual(adj[0].zone_high, new_hi, 5)
        self.assertEqual(r2.events, [])               # an adjustment does not re-notify the same opportunity
        self.assertEqual(st["feedback"][-1]["type"], "modify")

    def test_ambiguous_and_missing(self):
        st = self.fresh()
        dup = {}
        for o in self.res.opportunities:
            dup.setdefault(o.symbol, []).append(o)
        many = [v for v in dup.values() if len(v) > 1]
        if many:
            msg, ch = execute("ajuste %s zone 1 2" % many[0][0].symbol, st, self.res.opportunities, self.now)
            self.assertFalse(ch)
            self.assertIn("Plusieurs", msg)
        msg, ch = execute("ajuste %s" % self.single().id, st, self.res.opportunities, self.now)
        self.assertFalse(ch)
        self.assertIn("nouvelle zone", msg)
        msg, ch = execute("ajuste ZZZ9 zone 1 2", st, self.res.opportunities, self.now)
        self.assertFalse(ch)
        msg, ch = execute("ajuste zone 1 2", st, self.res.opportunities, self.now)
        self.assertFalse(ch)
        self.assertIn("laquelle", msg)

    def test_adjusting_a_manual_setup_edits_it(self):
        st = self.fresh()
        o = self.single()
        s = o.primary
        side_fr = "vente" if o.side == "sell" else "achat"
        execute("ajoute %s %s %s zone %.5f %.5f" % (o.symbol, s.tf, side_fr, s.zone_low, s.zone_high), st, self.res.opportunities, self.now, history=self.hist)
        r2 = run_cycle(self.hist, st, self.now + 900)
        m = [x for x in r2.opportunities if x.primary.zone_kind == "MANUEL"][0]
        msg, ch = execute("ajuste %s ligne %.8f" % (m.id, s.entry + 0.01), st, r2.opportunities, self.now + 1000)
        self.assertTrue(ch, msg)
        self.assertAlmostEqual(st["manual"][0]["entry"], s.entry + 0.01, 6)
        self.assertEqual(st["overrides"], [])


class TestLearningCommands(unittest.TestCase):
    def test_bilan_apply_params_reset_flow(self):
        st = {"feedback": [], "ledger": [], "manual": [], "params": {"global": {}, "filters": {}}, "suggestions": [], "dismissed": []}
        msg, ch = execute("bilan", st, [], NOW)
        self.assertIn("Pas encore de données", msg)
        self.assertTrue(ch)
        manual = [{"id": "P%d" % i, "diag": {"matched": False, "blocking": [{"key": "body_break", "value": False, "label": "b"}], "pairs": [], "ext": [], "delta": None}}
                  for i in range(4)]
        st["manual"] = manual
        msg, ch = execute("qu'as-tu appris ?", st, [], NOW)
        self.assertIn("SUGGESTIONS", msg)
        self.assertIn("1.", msg)
        msg, ch = execute("applique 1", st, [], NOW + 1)
        self.assertTrue(ch)
        self.assertIn("Réglage activé", msg)
        self.assertFalse(learning.params_from_state(st).body_break)
        msg, ch = execute("réglages", st, [], NOW + 2)
        self.assertIn("cassure de la ligne QML par le corps", msg)
        self.assertFalse(ch)
        msg, ch = execute("applique 9", st, [], NOW + 3)
        self.assertFalse(ch)
        msg, ch = execute("applique", st, [], NOW + 3)
        self.assertFalse(ch)
        msg, ch = execute("réinitialise les réglages", st, [], NOW + 4)
        self.assertTrue(ch)
        self.assertTrue(learning.params_from_state(st).body_break)

    def test_applied_parameter_really_changes_the_scan(self):
        hist = make_history(["EURUSD", "XAUUSD", "GBPUSD", "Volatility 75 Index", "Jump 25 Index", "USDJPY"], days=90, seed0=20)
        now = max(c[-1][0] for c in hist.values()) + 300
        base = run_cycle(hist, empty_state(), now)
        st = empty_state()
        st["params"] = {"global": {"body_break": False, "strict": False}, "filters": {}}
        relaxed = run_cycle(hist, st, now)
        self.assertGreaterEqual(len(relaxed.all_setups), len(base.all_setups))
        self.assertGreater(len(relaxed.all_setups), len(base.all_setups))
        st2 = empty_state()
        st2["params"] = {"global": {}, "filters": {"head_excess_atr": [">=", 1e9]}}
        none = run_cycle(hist, st2, now)
        self.assertEqual(none.opportunities, [])


if __name__ == "__main__":
    unittest.main()
