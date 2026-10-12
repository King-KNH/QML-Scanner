import json
import unittest

from helpers import T0, rw, with_times
from qml_scanner import config
from qml_scanner.commands import execute
from qml_scanner.notify import empty_state
from qml_scanner.scanner import run_cycle

DAYS = 90
SYMBOLS = ["EURUSD", "XAUUSD", "GBPUSD", "Boom 500 Index", "Crash 500 Index", "Volatility 75 Index", "Jump 25 Index", "Step Index 200",
           "USDJPY", "BTCUSD", "Boom 1000 Index", "Crash 300 Index", "Volatility 10 Index", "Jump 10 Index"]


def history(seed0=0, scale=1.0):
    h = {}
    for i, sym in enumerate(SYMBOLS):
        h[sym] = with_times(rw(seed0 + i, DAYS * 288, start=100.0 * scale))
    return h


class TestCycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hist = history(0)
        cls.now = max(c[-1][0] for c in cls.hist.values()) + 300
        cls.res = run_cycle(cls.hist, empty_state(), cls.now)

    def test_there_are_opportunities_to_test_with(self):
        self.assertGreater(len(self.res.opportunities), 3)
        self.assertGreater(len(self.res.all_setups), len(self.res.opportunities))

    def test_m5_is_never_scanned(self):
        self.assertTrue(all(s.tf != "M5" for s in self.res.all_setups))
        tfs = {s.tf for s in self.res.all_setups}
        self.assertTrue(tfs <= set(config.TIMEFRAMES))
        self.assertGreater(len(tfs), 3)

    def test_boom_crash_rule(self):
        for o in self.res.opportunities:
            if o.symbol.startswith("Boom"):
                self.assertEqual(o.side, "buy")
            if o.symbol.startswith("Crash"):
                self.assertEqual(o.side, "sell")
            for c in o.confluence:
                self.assertEqual(c.side, o.side)
                self.assertEqual(c.symbol, o.symbol)

    def test_every_opportunity_is_active_and_unique(self):
        keys = []
        for o in self.res.opportunities:
            self.assertTrue(o.primary.active)
            self.assertTrue(all(c.active for c in o.confluence))
            keys += o.keys
        self.assertEqual(len(keys), len(set(keys)))

    def test_first_cycle_notifies_each_opportunity_once(self):
        news = [e for e in self.res.events if e.kind == "new"]
        self.assertEqual(len(news), len(self.res.opportunities))
        self.assertEqual(len({e.id for e in news}), len(news))
        in_zone = [e for e in news if e.opp.primary.status == "entered"]
        self.assertEqual(len(self.res.alerts), len(in_zone))               # only the opportunities already in the zone are announced; the others show up in the pinned list
        self.assertTrue(all(a.startswith("🎯 PRIX DANS LA ZONE") for a in self.res.alerts))

    def test_second_identical_cycle_is_silent(self):
        again = run_cycle(self.hist, self.res.state, self.now + 900)
        self.assertEqual(again.events, [])
        self.assertEqual(sorted(o.id for o in again.opportunities), sorted(o.id for o in self.res.opportunities))
        self.assertEqual(again.pinned, run_cycle(self.hist, json.loads(json.dumps(self.res.state)), self.now + 900).pinned)

    def test_state_is_json_serialisable(self):
        json.dumps(self.res.state)

    def test_pinned_message_is_structured_and_complete(self):
        text = "\n".join(self.res.pinned)
        for o in self.res.opportunities:
            self.assertIn("#" + o.id, text)
            self.assertIn("▸ " + o.symbol, text)
        for m in self.res.pinned:
            self.assertLessEqual(len(m), 4096)
        cat_titles = [l for l in text.split("\n") if l.startswith("━━")]
        self.assertTrue(cat_titles)

    def test_deterministic(self):
        a = run_cycle(self.hist, empty_state(), self.now)
        self.assertEqual(a.pinned, self.res.pinned)
        self.assertEqual(a.alerts, self.res.alerts)

    def test_dismiss_end_to_end(self):
        st = json.loads(json.dumps(self.res.state))
        opps = self.res.opportunities
        target = opps[0]
        msg, changed = execute("supprime %s car pas valide à l'œil" % target.id, st, opps, self.now + 60)
        self.assertTrue(changed)
        self.assertIn(target.id, msg)
        r2 = run_cycle(self.hist, st, self.now + 900)
        self.assertNotIn(target.id, [o.id for o in r2.opportunities])
        self.assertNotIn("#" + target.id, "\n".join(r2.pinned))
        self.assertFalse([e for e in r2.events if e.id == target.id])        # no "closed", no "new"
        self.assertEqual(len(r2.opportunities), len(opps) - 1)
        r3 = run_cycle(self.hist, r2.state, self.now + 1800)
        self.assertEqual(r3.events, [])
        self.assertEqual(len(r3.opportunities), len(opps) - 1)
        # restore -> comes back as a new opportunity
        msg, changed = execute("rétablis %s" % target.id, r3.state, r3.opportunities, self.now + 1900)
        self.assertTrue(changed)
        r4 = run_cycle(self.hist, r3.state, self.now + 2700)
        self.assertEqual(len(r4.opportunities), len(opps))
        self.assertEqual([e.kind for e in r4.events], ["new"])
        self.assertEqual(r4.events[0].symbol, target.symbol)


class TestOtherInputs(unittest.TestCase):
    def test_missing_or_short_history_is_ignored(self):
        res = run_cycle({"EURUSD": [], "XAUUSD": with_times(rw(1, 50))}, empty_state(), T0 + 86400)
        self.assertEqual(res.opportunities, [])
        self.assertEqual(res.events, [])
        self.assertIn("Aucune", res.pinned[0])

    def test_last_price_override_changes_status_only(self):
        hist = history(0)
        now = max(c[-1][0] for c in hist.values()) + 300
        base = run_cycle(hist, empty_state(), now)
        o = base.opportunities[0]
        lp = {o.symbol: o.primary.entry}
        live = run_cycle(hist, empty_state(), now, last_prices=lp)
        same = [x for x in live.opportunities if x.symbol == o.symbol and x.side == o.side and x.primary.key == o.primary.key]
        self.assertTrue(same)
        self.assertEqual(same[0].primary.status, "entered")

    def test_window_and_timeframes_arguments(self):
        hist = {"EURUSD": history(0)["EURUSD"]}
        now = hist["EURUSD"][-1][0] + 300
        res = run_cycle(hist, empty_state(), now, tfs=["H1"])
        self.assertTrue(all(s.tf == "H1" for s in res.all_setups))


if __name__ == "__main__":
    unittest.main()


class TestAlertKinds(unittest.TestCase):
    """Only "price in the zone" and "back" are announced on Telegram; the rest lives in the pinned list."""

    @staticmethod
    def ev(kind, status="detected", opp=True):
        from types import SimpleNamespace
        from qml_scanner.notify import Event
        return Event(kind, "c", "EURUSD", "sell", opp=SimpleNamespace(primary=SimpleNamespace(status=status)) if opp else None)

    def test_only_in_zone_and_back_are_announced(self):
        from qml_scanner.scanner import _alert_event
        self.assertIsNone(_alert_event(self.ev("new", "detected")))
        self.assertIsNone(_alert_event(self.ev("new", "approaching")))
        self.assertIsNone(_alert_event(self.ev("approaching", "approaching")))
        self.assertIsNone(_alert_event(self.ev("closed", opp=False)))
        self.assertEqual(_alert_event(self.ev("entered", "entered")).kind, "entered")
        self.assertEqual(_alert_event(self.ev("returned", "entered")).kind, "returned")

    def test_new_opportunity_already_in_the_zone_is_announced_as_in_the_zone(self):
        from qml_scanner.scanner import _alert_event
        self.assertEqual(_alert_event(self.ev("new", "entered")).kind, "entered")

