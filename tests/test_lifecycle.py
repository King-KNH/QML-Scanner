import unittest

from helpers import build, build_wick, leg, make_setup, mirror, with_times
from qml_scanner.engine import scan_series
from qml_scanner.notify import empty_state, plan_notifications
from qml_scanner.qml_core import Params
from qml_scanner.selection import cluster

P = Params(ext=3, atr_len=5)
BASE = build(100, 110, 104, 120, 112, 126, 108, 118.0)
AWAY = BASE + leg(118.0, 100.0, 10)          # closes well below the LL (108): the market left


def scan(seq, p=P, **kw):
    return scan_series("T", "M15", with_times(seq, 900), p, **kw)


class TestBodyBreakInEngine(unittest.TestCase):
    def test_wick_only_sweep_is_not_a_setup(self):
        w = build_wick()
        self.assertEqual(scan(w, Params(ext=3, atr_len=5, midzone=False)), [])
        on = scan(w, Params(ext=3, atr_len=5, midzone=False, body_break=False))
        self.assertEqual(len(on), 1)
        self.assertLess(on[0].head_excess, 1.0)          # tiny head: the double-bottom look of the screenshot

    def test_mirror_for_buys(self):
        w = mirror(build_wick())
        self.assertEqual(scan(w, Params(ext=3, atr_len=5, midzone=False)), [])
        self.assertEqual(len(scan(w, Params(ext=3, atr_len=5, midzone=False, body_break=False))), 1)

    def test_real_break_is_kept(self):
        s = scan(BASE)
        self.assertEqual(len(s), 1)
        self.assertGreater(s[0].head_excess, 5.0)


class TestParkedStatus(unittest.TestCase):
    def test_moved_away_is_parked_and_back_near_is_visible_again(self):
        s0 = scan(BASE)[0]
        self.assertIn(s0.status, ("detected", "approaching"))
        far = scan(AWAY)[0]
        self.assertTrue(far.away)
        self.assertEqual(far.status, "parked")
        self.assertFalse(far.active)
        back = scan(AWAY, last_price=far.entry - 0.3 * far.atr)[0]
        self.assertTrue(back.away)
        self.assertEqual(back.status, "approaching")
        self.assertTrue(back.active)

    def test_parked_is_never_confused_with_invalid_or_expired(self):
        far = scan(AWAY)[0]
        self.assertIsNone(far.t_invalid)
        self.assertNotEqual(far.status, "invalid")

    def test_mirror_buy_is_parked_too(self):
        far = scan(mirror(AWAY))[0]
        self.assertEqual((far.side, far.status), ("buy", "parked"))

    def test_a_wick_below_the_extreme_does_not_park(self):
        ext = scan(BASE)[0].extreme                      # the LL price
        wick = BASE + [(118.0, 118.2, ext - 3.0, ext + 0.3)]          # wick far below LL, but it CLOSES above it
        s = scan(wick)[0]
        self.assertFalse(s.away)
        self.assertNotEqual(s.status, "parked")
        body = BASE + [(118.0, 118.2, ext - 3.0, ext - 0.3)]          # the same candle closing below LL: away
        self.assertTrue(scan(body)[0].away)

    def test_touch_then_close_above_is_invalid_not_parked(self):
        seq = BASE + leg(118.0, 127.5, 3)                 # touches the line then closes beyond the head (zone QML broken): invalid, not parked
        self.assertEqual(scan(seq)[0].status, "invalid")


class TestParkedNotifications(unittest.TestCase):
    def opps(self, *setups):
        return cluster(list(setups))

    def test_erased_silently_then_returns_with_an_event(self):
        st = empty_state()
        s = make_setup(status="detected")
        ev, st = plan_notifications(self.opps(s), [s], st)
        self.assertEqual([e.kind for e in ev], ["new"])
        ident = list(st["clusters"].values())[0]["id"]
        parked = make_setup(status="parked")
        for _ in range(3):                                # stays away for several cycles: nothing, ever
            ev, st = plan_notifications(self.opps(parked), [parked], st)
            self.assertEqual(ev, [])
            self.assertEqual(len(st["clusters"]), 1)
            self.assertTrue(list(st["clusters"].values())[0]["parked"])
        back = make_setup(status="approaching")
        ev, st = plan_notifications(self.opps(back), [back], st)
        self.assertEqual([e.kind for e in ev], ["returned"])
        self.assertEqual(ev[0].id, ident)                 # same id: it is the same opportunity
        self.assertFalse(list(st["clusters"].values())[0].get("parked"))
        ev, st = plan_notifications(self.opps(back), [back], st)
        self.assertEqual(ev, [])
        gone = make_setup(status="parked")
        ev, st = plan_notifications([], [gone], st)       # away again
        ev, st = plan_notifications(self.opps(back), [back], st)
        self.assertEqual([e.kind for e in ev], ["returned"])   # each return is notified

    def test_parked_that_ends_is_dropped_without_a_closed_message(self):
        s = make_setup(status="detected")
        _, st = plan_notifications(self.opps(s), [s], empty_state())
        parked = make_setup(status="parked")
        _, st = plan_notifications([], [parked], st)
        for setups in ([], [make_setup(status="invalid")], [make_setup(status="expired")]):
            ev, st2 = plan_notifications([], setups, st)
            self.assertEqual(ev, [])
            self.assertEqual(st2["clusters"], {})

    def test_status_is_remembered_across_the_parking(self):
        s = make_setup(status="entered")
        _, st = plan_notifications(self.opps(s), [s], empty_state())
        parked = make_setup(status="parked")
        _, st = plan_notifications([], [parked], st)
        self.assertEqual(list(st["clusters"].values())[0]["status"], "entered")
        back = make_setup(status="entered")
        ev, st = plan_notifications(self.opps(back), [back], st)
        self.assertEqual([e.kind for e in ev], ["returned"])

    def test_unparked_opportunity_with_a_new_member_is_still_one_opportunity(self):
        a = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1, status="detected")
        _, st = plan_notifications(self.opps(a), [a], empty_state())
        _, st = plan_notifications([], [make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1, status="parked")], st)
        b = make_setup(tf="H4", lo=100.2, hi=101.2, entry=100.5, t_left=2, status="approaching", atr=2.0, leg_=30)
        a2 = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1, status="approaching")
        ev, st = plan_notifications(self.opps(a2, b), [a2, b], st)
        self.assertEqual([e.kind for e in ev], ["returned"])
        self.assertEqual(len(st["clusters"]), 1)


if __name__ == "__main__":
    unittest.main()
