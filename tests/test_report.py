import unittest

from helpers import make_setup
from qml_scanner import config
from qml_scanner.notify import empty_state, plan_notifications
from qml_scanner.report import SAFE_LIMIT, TG_LIMIT, fmt_price, format_alert, format_pinned
from qml_scanner.selection import cluster, correlation_tags


def build(*setups, state=None):
    opps = cluster(list(setups))
    correlation_tags(opps)
    ev, st = plan_notifications(opps, list(setups), state or empty_state())
    return opps, ev, st


class TestFormat(unittest.TestCase):
    def test_price_format(self):
        self.assertEqual(fmt_price(4192.3), "4192.30")
        self.assertEqual(fmt_price(150.123456), "150.123")
        self.assertEqual(fmt_price(12.3456789), "12.3457")
        self.assertEqual(fmt_price(1.085234), "1.08523")

    def test_alert_texts_and_id(self):
        s = make_setup(symbol="XAUUSD", tf="H1", side="sell", status="approaching", lo=4190, hi=4199.4, entry=4192.3)
        opps, ev, st = build(s)
        txt = format_alert(ev[0])
        for needle in ("NOUVELLE OPPORTUNITÉ", "VENTE", "XAUUSD", "H1", "QML-R", "4190.00", "4199.40", "4192.30",
                       "à l'approche", "ID : " + opps[0].id, "supprime " + opps[0].id):
            self.assertIn(needle, txt)
        s2 = make_setup(symbol="XAUUSD", tf="H1", side="sell", status="entered", lo=4190, hi=4199.4, entry=4192.3)
        _, ev2, st2 = build(s2, state=st)
        self.assertIn("PRIX DANS LA ZONE", format_alert(ev2[0]))
        ev3, _ = plan_notifications([], [make_setup(symbol="XAUUSD", status="invalid")], st2)
        txt3 = format_alert(ev3[0])
        self.assertIn("invalidé", txt3)
        self.assertIn("XAUUSD", txt3)
        self.assertIn("[" + opps[0].id + "]", txt3)

    def test_confluence_and_correlation_tags_are_shown(self):
        a = make_setup(symbol="EURUSD", side="buy", tf="H4", t_left=1)
        b = make_setup(symbol="EURUSD", side="buy", tf="M15", t_left=2, lo=100.2, hi=100.8, atr=0.3)
        c = make_setup(symbol="GBPUSD", side="buy", tf="H1", t_left=3)
        opps, ev, _ = build(a, b, c)
        txt = "\n".join(format_pinned(opps))
        self.assertIn("conf.", txt)
        eur = [e for e in ev if e.symbol == "EURUSD"][0]
        self.assertIn("Confluence", format_alert(eur))
        self.assertIn("Même thèse USD ↓", format_alert(eur))


class TestPinnedStructure(unittest.TestCase):
    def test_grouped_by_category_then_asset_in_configured_order(self):
        items = [
            make_setup(symbol="Crash 500 Index", side="sell", t_left=1, status="detected"),
            make_setup(symbol="GBPUSD", side="buy", t_left=2, status="approaching"),
            make_setup(symbol="XAUUSD", side="sell", t_left=3, status="entered"),
            make_setup(symbol="EURUSD", side="sell", t_left=4, status="detected"),
            make_setup(symbol="EURUSD", side="buy", t_left=5, tf="H4", lo=90, hi=91, entry=90.5, status="entered"),
            make_setup(symbol="Boom 1000 Index", side="buy", t_left=6, status="detected"),
            make_setup(symbol="Volatility 75 Index", side="buy", t_left=7, status="detected"),
            make_setup(symbol="Jump 10 Index", side="sell", t_left=8, status="approaching"),
            make_setup(symbol="Step Index 200", side="buy", t_left=9, status="detected"),
        ]
        opps, _, _ = build(*items)
        msgs = format_pinned(opps, now_epoch=1_700_000_000)
        self.assertEqual(len(msgs), 1)
        text = msgs[0]
        titles = [l for l in text.split("\n") if l.startswith("━━")]
        self.assertEqual([t.split(" (")[0] for t in titles],
                         ["━━ FOREX", "━━ MÉTAUX & CRYPTO", "━━ STEP", "━━ VOLATILITY", "━━ JUMP", "━━ BOOM", "━━ CRASH"])
        self.assertIn("━━ FOREX (3) ━━", text)
        assets = [l for l in text.split("\n") if l.startswith("▸")]
        self.assertEqual(assets, ["▸ EURUSD", "▸ GBPUSD", "▸ XAUUSD", "▸ Step Index 200", "▸ Volatility 75 Index",
                                  "▸ Jump 10 Index", "▸ Boom 1000 Index", "▸ Crash 500 Index"])
        lines = text.split("\n")
        i = lines.index("▸ EURUSD")
        self.assertIn("🎯", lines[i + 1])        # entered first
        self.assertIn("🆕", lines[i + 2])        # then detected
        for o in opps:
            self.assertIn("#" + o.id, text)
        self.assertIn("🎯 2 dans la zone", text)
        self.assertIn("⚠️ 2 à l'approche", text)
        self.assertIn("🆕 5 détectées", text)
        self.assertIn("Mis à jour", text)
        self.assertIn("OPPORTUNITÉS QML EN COURS · 9", text)

    def test_unknown_category_goes_last(self):
        opps, _, _ = build(make_setup(symbol="EURUSD", t_left=1), make_setup(symbol="ZZZ", t_left=2))
        text = format_pinned(opps)[0]
        self.assertLess(text.index("FOREX"), text.index("AUTRES"))

    def test_split_keeps_blocks_whole_and_repeats_category_title(self):
        setups = []
        for i, sym in enumerate(config.ALL_SYMBOLS):
            side = "buy" if sym.startswith("Boom") else "sell" if sym.startswith("Crash") else ("buy" if i % 2 else "sell")
            setups.append(make_setup(symbol=sym, side=side, tf="H1", lo=100 + i, hi=101 + i, entry=100.5 + i, t_left=i,
                                     status=["detected", "approaching", "entered"][i % 3]))
        opps, _, _ = build(*setups)
        self.assertEqual(len(opps), len(config.ALL_SYMBOLS))
        msgs = format_pinned(opps, now_epoch=1_700_000_000)
        self.assertGreater(len(msgs), 1)
        for m in msgs:
            self.assertLessEqual(len(m), TG_LIMIT)
            self.assertLessEqual(len(m), SAFE_LIMIT)
        cats = [c.upper() for c in config.CATEGORY_ORDER]
        for m in msgs[1:]:
            lines = [l for l in m.split("\n") if l.strip()]
            self.assertEqual(lines[0], "📌 (suite)")
            self.assertTrue(lines[1].startswith("━━ "))                      # category title repeated on top
            self.assertIn(lines[1].split(" (")[0][3:], cats)
            self.assertTrue(lines[2].startswith("▸"))                        # never starts in the middle of an asset block
        text = "\n".join(msgs)
        for sym in config.ALL_SYMBOLS:
            self.assertEqual(text.count("▸ %s\n" % sym), 1, sym)
        for m in msgs:
            for l in m.split("\n"):
                if l.startswith("   "):
                    self.assertIn("#", l)
        # category order is preserved across messages
        seen = [t.split(" (")[0][3:] for t in text.split("\n") if t.startswith("━━ ")]
        dedup = [c for i, c in enumerate(seen) if i == 0 or c != seen[i - 1]]
        self.assertEqual(dedup, cats)

    def test_empty(self):
        self.assertEqual(len(format_pinned([])), 1)
        self.assertIn("Aucune", format_pinned([])[0])


if __name__ == "__main__":
    unittest.main()
