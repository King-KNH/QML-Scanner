import itertools
import json
import unittest

from helpers import make_setup
from qml_scanner.notify import empty_state, plan_notifications
from qml_scanner.selection import cluster, correlation_tags


def opps_of(*setups):
    o = cluster(list(setups))
    correlation_tags(o)
    return o


def kinds(events):
    return [e.kind for e in events]


class TestLifecycle(unittest.TestCase):
    def test_full_life_of_an_opportunity(self):
        st = empty_state()
        s = make_setup(status="detected")
        ev, st = plan_notifications(opps_of(s), [s], st)
        self.assertEqual(kinds(ev), ["new"])
        ev, st = plan_notifications(opps_of(s), [s], st)
        self.assertEqual(ev, [])
        s2 = make_setup(status="approaching")
        ev, st = plan_notifications(opps_of(s2), [s2], st)
        self.assertEqual(kinds(ev), ["approaching"])
        ev, st = plan_notifications(opps_of(s2), [s2], st)
        self.assertEqual(ev, [])
        s3 = make_setup(status="entered")
        ev, st = plan_notifications(opps_of(s3), [s3], st)
        self.assertEqual(kinds(ev), ["entered"])
        s4 = make_setup(status="detected")            # price moved away: no regression, no event
        ev, st = plan_notifications(opps_of(s4), [s4], st)
        self.assertEqual(ev, [])
        self.assertEqual(list(st["clusters"].values())[0]["status"], "entered")
        inv = make_setup(status="invalid")
        ev, st = plan_notifications([], [inv], st)
        self.assertEqual(kinds(ev), ["closed"])
        self.assertEqual(ev[0].reason, "invalidated")
        self.assertTrue(ev[0].id)
        self.assertEqual(st["clusters"], {})

    def test_first_seen_already_approaching_is_a_single_new_event(self):
        s = make_setup(status="approaching")
        ev, _ = plan_notifications(opps_of(s), [s], empty_state())
        self.assertEqual(kinds(ev), ["new"])
        self.assertEqual(ev[0].opp.primary.status, "approaching")

    def test_expiry_needs_grace_cycles(self):
        s = make_setup(status="detected")
        _, st = plan_notifications(opps_of(s), [s], empty_state())
        ev, st = plan_notifications([], [], st, grace=2)
        self.assertEqual(ev, [])
        ev, st = plan_notifications(opps_of(s), [s], st)
        self.assertEqual(ev, [])
        _, st = plan_notifications([], [], st, grace=2)
        ev, st = plan_notifications([], [], st, grace=2)
        self.assertEqual(kinds(ev), ["closed"])
        self.assertEqual(ev[0].reason, "expired")
        self.assertEqual(st["clusters"], {})

    def test_invalid_member_closes_immediately(self):
        s = make_setup(status="detected")
        _, st = plan_notifications(opps_of(s), [s], empty_state())
        ev, _ = plan_notifications([], [make_setup(status="invalid")], st, grace=5)
        self.assertEqual(kinds(ev), ["closed"])
        self.assertEqual(ev[0].reason, "invalidated")


class TestIdentity(unittest.TestCase):
    def h1_m15(self, m15_status="detected"):
        h1 = make_setup(tf="H1", lo=100, hi=101, entry=100.5, atr=1.0, leg_=8, t_left=10)
        m15 = make_setup(tf="M15", lo=100.2, hi=100.9, entry=100.5, atr=0.3, leg_=9, t_left=20, status=m15_status)
        return h1, m15

    def test_primary_switch_is_silent_then_status_events_continue(self):
        h1, m15 = self.h1_m15()
        _, st = plan_notifications(opps_of(h1), [h1], empty_state())
        o2 = opps_of(h1, m15)
        self.assertEqual(len(o2), 1)
        ev, st = plan_notifications(o2, [h1, m15], st)
        self.assertEqual(ev, [])
        self.assertEqual(len(st["clusters"]), 1)
        _, m15b = self.h1_m15("approaching")
        ev, st = plan_notifications(opps_of(m15b), [m15b], st)
        self.assertEqual(kinds(ev), ["approaching"])
        self.assertEqual(len(st["clusters"]), 1)

    def test_primary_changed_flag_when_primary_moves_with_the_status(self):
        h1, _ = self.h1_m15()
        _, st = plan_notifications(opps_of(h1), [h1], empty_state())
        _, m15b = self.h1_m15("approaching")
        m15b.zone_low, m15b.zone_high = 100.3, 100.7      # still overlapping the H1 zone
        # only the M15 setup is left (the H1 one disappeared): same identity through the saved members? no common key
        ev, st2 = plan_notifications(opps_of(h1, m15b), [h1, m15b], st)
        self.assertEqual(len(st2["clusters"]), 1)
        self.assertEqual(kinds(ev), ["approaching"])
        self.assertTrue(ev[0].primary_changed)

    def test_merge_keeps_highest_status_and_does_not_renotify(self):
        a = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1, status="entered")
        b = make_setup(tf="H4", lo=110, hi=111, entry=110.5, t_left=2, status="detected")
        _, st = plan_notifications(opps_of(a, b), [a, b], empty_state())
        self.assertEqual(len(st["clusters"]), 2)
        bridge = make_setup(tf="M30", lo=100.9, hi=110.1, entry=105, t_left=3, atr=1.0, status="detected")
        merged = opps_of(a, b, bridge)
        self.assertEqual(len(merged), 1)
        ev, st = plan_notifications(merged, [a, b, bridge], st)
        self.assertEqual(ev, [])
        self.assertEqual(len(st["clusters"]), 1)
        self.assertEqual(list(st["clusters"].values())[0]["status"], "entered")

    def test_split_keeps_identity_with_the_old_primary_whatever_the_order(self):
        a = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1)
        b = make_setup(tf="H4", lo=100.5, hi=101.5, entry=101, t_left=2)
        first = opps_of(a, b)
        self.assertEqual(len(first), 1)
        _, st = plan_notifications(first, [a, b], empty_state())
        old_primary = first[0].primary.key
        old_id = first[0].id
        a2 = make_setup(tf="H1", lo=100, hi=101, entry=100.5, t_left=1)
        b2 = make_setup(tf="H4", lo=130, hi=131, entry=130.5, t_left=2)
        for order in itertools.permutations([a2, b2]):
            opps = cluster(list(order))
            self.assertEqual(len(opps), 2)
            for perm in itertools.permutations(opps):
                ev, st2 = plan_notifications(list(perm), [a2, b2], st)
                self.assertEqual(kinds(ev), ["new"])
                self.assertEqual(len(st2["clusters"]), 2)
                keeper = [o for o in perm if old_primary in o.keys][0]
                self.assertEqual(keeper.id, old_id)
                self.assertEqual(len({o.id for o in perm}), 2)
                self.assertNotEqual(ev[0].id, old_id)

    def test_ids_are_stable_unique_and_short(self):
        setups = [make_setup(symbol="Volatility %d Index" % i, t_left=i, lo=100 + 3 * i, hi=101 + 3 * i, entry=100.5 + 3 * i)
                  for i in range(60)]
        o1 = opps_of(*setups)
        _, st = plan_notifications(o1, setups, empty_state())
        ids = [o.id for o in o1]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(i) == 4 and i.isalnum() and i == i.upper() and "I" not in i and "O" not in i for i in ids))
        o2 = opps_of(*setups)
        _, st2 = plan_notifications(o2, setups, st)
        self.assertEqual(sorted(o.id for o in o2), sorted(ids))
        by_cid1 = {o.cid: o.id for o in o1}
        self.assertEqual({o.cid: o.id for o in o2}, by_cid1)

    def test_new_ids_never_reuse_a_dismissed_id(self):
        s = make_setup(symbol="AAA", t_left=1)
        o = opps_of(s)
        _, st = plan_notifications(o, [s], empty_state())
        taken = o[0].id
        st["dismissed"].append({"id": taken, "symbol": "ZZZ", "side": "sell", "tf": "H1", "members": [], "lo": 0, "hi": 0, "t": 0, "reason": ""})
        st["clusters"] = {}
        o2 = opps_of(make_setup(symbol="AAA", t_left=1))
        plan_notifications(o2, [], st)
        self.assertNotEqual(o2[0].id, taken)

    def test_state_is_json_serialisable_and_round_trips(self):
        s = make_setup(status="approaching")
        _, st = plan_notifications(opps_of(s), [s], empty_state())
        st2 = json.loads(json.dumps(st))
        ev, st3 = plan_notifications(opps_of(s), [s], st2)
        self.assertEqual(ev, [])
        self.assertEqual(st3["clusters"].keys(), st["clusters"].keys())
        self.assertIn("dismissed", st3)
        self.assertIn("feedback", st3)

    def test_empty_everything(self):
        ev, st = plan_notifications([], [], empty_state())
        self.assertEqual((ev, st["clusters"]), ([], {}))


if __name__ == "__main__":
    unittest.main()
