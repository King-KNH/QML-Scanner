import unittest

from helpers import build, build_struct, mirror, with_times
from qml_scanner.engine import scan_series
from qml_scanner.manual import add_manual, describe_diag, diagnose
from qml_scanner.qml_core import Params
from qml_scanner.report import KIND_FR, describe
from qml_scanner.selection import cluster

BASE = build(100, 110, 104, 120, 112, 126, 108, 118.0)


def scan(seq, **kw):
    p = Params(ext=3, atr_len=5, midzone=False, **kw)
    return scan_series("T", "M15", with_times(seq, 900), p)


class TestStructureBreakByBody(unittest.TestCase):
    def test_wick_only_break_is_not_a_setup(self):
        self.assertEqual(scan(build_struct()), [])
        self.assertEqual(len(scan(build_struct(), body_struct=False)), 1)

    def test_buy_side(self):
        self.assertEqual(scan(mirror(build_struct())), [])
        self.assertEqual(len(scan(mirror(build_struct()), body_struct=False)), 1)

    def test_later_body_close_validates_it(self):
        s = scan(build_struct(late_closes=True))
        self.assertEqual(len(s), 1)
        self.assertEqual(len(scan(mirror(build_struct(late_closes=True)))), 1)

    def test_diagnosis_names_the_structure_rule(self):
        st = {}
        rec = add_manual(st, "T", "M15", "sell", 1_800_000_000, zone=(119.5, 120.0), entry=120.0)
        d = diagnose(rec, with_times(build_struct(), 900), Params(ext=3, atr_len=5, midzone=False), base_sec=900)
        self.assertFalse(d["matched"])
        self.assertEqual([b["key"] for b in d["blocking"]], ["body_struct"])
        self.assertIn("cassure de structure", " ".join(describe_diag(d)))


class TestQmlZone(unittest.TestCase):
    def test_zone_spans_shoulder_to_head_for_a_sell(self):
        s = scan(BASE, mode="qmlz")
        self.assertEqual(len(s), 1)
        z = s[0]
        self.assertEqual(z.zone_kind, "QMLZ")
        self.assertAlmostEqual(z.zone_low, z.shoulder, 9)
        self.assertAlmostEqual(z.zone_high, z.head, 9)
        self.assertAlmostEqual(z.entry, z.shoulder, 9)
        self.assertGreater(z.zone_high, z.zone_low)

    def test_zone_spans_head_to_shoulder_for_a_buy(self):
        z = scan(mirror(BASE), mode="qmlz")[0]
        self.assertEqual((z.side, z.zone_kind), ("buy", "QMLZ"))
        self.assertAlmostEqual(z.zone_high, z.shoulder, 9)
        self.assertAlmostEqual(z.zone_low, z.head, 9)
        self.assertAlmostEqual(z.entry, z.shoulder, 9)

    def test_only_one_zone_type_at_a_time(self):
        self.assertEqual({s.zone_kind for s in scan(BASE, mode="mpl")}, {"MPL"})
        self.assertEqual({s.zone_kind for s in scan(BASE, mode="ob")}, {"OB"})
        self.assertEqual({s.zone_kind for s in scan(BASE, mode="qmlz")}, {"QMLZ"})
        self.assertEqual({s.zone_kind for s in scan(BASE, mode="all")}, {"MPL"})          # priority MPL > OB > Zone QML
        self.assertEqual({s.zone_kind for s in scan(BASE, mode="all", mar_ratio=0.99)}, {"QMLZ"})

    def test_report_names_the_zone_type(self):
        s = scan(BASE, mode="qmlz")[0]
        opp = cluster([s])[0]
        self.assertIn("Zone QML", describe(opp))
        self.assertEqual(KIND_FR["OB"], "Order Block")


if __name__ == "__main__":
    unittest.main()
