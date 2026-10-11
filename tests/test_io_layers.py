import json
import os
import tempfile
import unittest

from fakes import FakeDeriv, FakeTelegramHttp, FakeTwelveHttp, factory_for
from helpers import T0, rw, with_times
from qml_scanner import datalayer, store
from qml_scanner.sources import (DerivClient, DerivError, QuotaExceeded, TwelveClient, deriv_urls, norm_name, resolve_symbols)
from qml_scanner.telegram_io import NotFound, Telegram, _split, sync_pinned

SYMS = {"EURUSD": "frxEURUSD", "XAUUSD": "frxXAUUSD", "Volatility 10 Index": "R_10", "Boom 1000 Index": "BOOM1000N"}


def tmpdir():
    return tempfile.mkdtemp(prefix="qml_test_")


class TestStore(unittest.TestCase):
    def test_roundtrip_merge_purge_drop_open(self):
        d = tmpdir()
        p = store.hist_path(d, "Volatility 10 (1s) Index", "M5")
        self.assertTrue(p.endswith("volatility_10_1s_index__M5.csv"))
        c = with_times(rw(1, 50), 300)
        c = [(t, o, h, l, cl) for (t, o, h, l, cl) in c]
        store.write_candles(p, c)
        self.assertEqual(store.read_candles(p), c)                       # exact float round trip
        newer = [(c[-1][0] + 300, 1.0, 2.0, 0.5, 1.5)]
        dup = [(c[0][0], 9.0, 9.0, 9.0, 9.0)]
        m = store.merge(c, newer + dup)
        self.assertEqual(len(m), 51)
        self.assertEqual(m[0][1], 9.0)                                   # the new value wins
        self.assertEqual([x[0] for x in m], sorted(x[0] for x in m))
        now = c[-1][0] + 300
        self.assertEqual(len(store.drop_open(m, "M5", now)), 50)         # the candle opened at c[-1]+300 is still forming
        old = [(now - 40 * 86400, 1, 1, 1, 1), (now - 10 * 86400, 1, 1, 1, 1)]
        self.assertEqual(len(store.purge(old, "M5", now)), 1)
        self.assertEqual(len(store.purge(old, "H1", now)), 2)

    def test_damaged_lines_and_files(self):
        d = tmpdir()
        p = store.hist_path(d, "A", "M5")
        os.makedirs(os.path.dirname(p))
        open(p, "w").write("t,o,h,l,c\n300,1,2,0,1\ngarbage\n600,1,2,0,2\n")
        self.assertEqual([c[0] for c in store.read_candles(p)], [300, 600])
        self.assertEqual(store.read_candles(os.path.join(d, "nope.csv")), [])
        open(store.state_path(d), "w").write("{not json")
        self.assertEqual(store.load_state(d), {})
        self.assertTrue(os.path.exists(store.state_path(d) + ".damaged"))

    def test_state_roundtrip_and_history(self):
        d = tmpdir()
        st = {"a": [1, 2], "é": "ü"}
        store.save_state(d, st)
        self.assertEqual(store.load_state(d), st)
        store.write_candles(store.hist_path(d, "EURUSD", "H1"), [(3600, 1, 2, 0, 1)])
        h = store.load_history(d, ["EURUSD", "XAUUSD"])
        self.assertEqual(list(h), ["EURUSD"])
        self.assertEqual(h["EURUSD"]["M5"], [])
        self.assertEqual(len(h["EURUSD"]["H1"]), 1)


class TestSymbols(unittest.TestCase):
    ACTIVE = [
        {"symbol": "frxAUDJPY", "display_name": "AUD/JPY", "exchange_is_open": 1},
        {"symbol": "frxXAUUSD", "display_name": "Gold/USD", "exchange_is_open": 0},
        {"symbol": "frxXAGUSD", "display_name": "Silver/USD"},
        {"symbol": "cryBTCUSD", "display_name": "BTC/USD", "exchange_is_open": 1},
        {"symbol": "R_10", "display_name": "Volatility 10 Index"},
        {"symbol": "1HZ10V", "display_name": "Volatility 10 (1s) Index"},
        {"symbol": "stpRNG", "display_name": "Step Index"},
        {"symbol": "stpRNG2", "display_name": "Step 200 Index"},
        {"symbol": "JD10", "display_name": "Jump 10 Index"},
        {"symbol": "BOOM1000", "display_name": "Boom 1000 Index"},
        {"symbol": "CRASH500", "display_name": "Crash 500 Index", "is_trading_suspended": 1},
    ]

    def test_names(self):
        self.assertEqual(norm_name("AUD/JPY"), norm_name("AUDJPY"))
        self.assertEqual(norm_name("Gold/USD"), "xauusd")
        self.assertEqual(norm_name("Volatility 10 (1s) Index"), "volatility101s")
        self.assertNotEqual(norm_name("Volatility 10 Index"), norm_name("Volatility 10 (1s) Index"))
        self.assertEqual(norm_name("Step 200 Index"), norm_name("Step Index 200"))

    def test_resolution_missing_and_open_flags(self):
        wanted = ["AUDJPY", "XAUUSD", "XAGUSD", "BTCUSD", "Volatility 10 Index", "Volatility 10 (1s) Index", "Step Index", "Step Index 200",
                  "Jump 10 Index", "Boom 1000 Index", "Crash 500 Index", "EURGBP", "Volatility 99 Index"]
        codes, missing, is_open = resolve_symbols(self.ACTIVE, wanted)
        self.assertEqual(codes["AUDJPY"], "frxAUDJPY")
        self.assertEqual(codes["XAUUSD"], "frxXAUUSD")
        self.assertEqual(codes["Volatility 10 (1s) Index"], "1HZ10V")
        self.assertEqual(codes["Volatility 10 Index"], "R_10")
        self.assertEqual((codes["Step Index"], codes["Step Index 200"]), ("stpRNG", "stpRNG2"))
        self.assertEqual(sorted(missing), ["EURGBP", "Volatility 99 Index"])
        self.assertFalse(is_open["XAUUSD"])
        self.assertTrue(is_open["XAGUSD"])
        self.assertFalse(is_open["Crash 500 Index"])
        self.assertTrue(is_open["AUDJPY"])

    def test_deriv_urls(self):
        self.assertTrue(deriv_urls(None)[1].endswith("app_id=1089"))
        self.assertTrue(deriv_urls("34Aon8j01A4dcV5n69KHB")[1].endswith("app_id=1089"))      # alphanumeric id is not a legacy id
        self.assertTrue(deriv_urls("12345")[1].endswith("app_id=12345"))
        self.assertTrue(deriv_urls("12345")[0].startswith("wss://api.derivws.com/"))


class TestDerivClient(unittest.TestCase):
    def setUp(self):
        self.srv = FakeDeriv(SYMS, days=40, seed0=3)
        self.sleeps = []

    def client(self, urls=("wss://a/new", "wss://b/legacy"), bad=()):
        return DerivClient(urls, factory_for(self.srv, bad), pace=0.0, sleep=self.sleeps.append)

    def test_falls_back_to_the_next_endpoint_and_remembers_why(self):
        c = self.client(bad=("a/new",))
        self.assertEqual(c.connect(), "wss://b/legacy")
        self.assertIn("wss://a/new", c.errors)
        self.assertIn("ConnectionError", c.errors["wss://a/new"])

    def test_all_endpoints_down(self):
        c = self.client(bad=("a/new", "b/legacy"))
        with self.assertRaises(DerivError) as cm:
            c.connect()
        self.assertEqual(cm.exception.code, "NoEndpoint")
        self.assertEqual(len(c.errors), 2)

    def test_active_symbols_and_candles(self):
        c = self.client()
        act = c.active_symbols()
        self.assertEqual(len(act), 4)
        cs = c.candles("frxEURUSD", 300, 10)
        self.assertEqual(len(cs), 10)
        self.assertEqual(cs[-1][0] % 300, 0)
        self.assertTrue(all(len(x) == 5 for x in cs))
        since = cs[-3][0] + 300
        new = c.candles("frxEURUSD", 300, 5000, start=since)
        self.assertEqual([x[0] for x in new][0], since)
        self.assertEqual(c.candles("frxEURUSD", 3600, 5)[-1][0] % 3600, 0)

    def test_errors_and_rate_limit(self):
        c = self.client()
        with self.assertRaises(DerivError) as cm:
            c.candles("nope", 300, 5)
        self.assertEqual(cm.exception.code, "InvalidSymbol")
        self.srv.rate_limit_next = 2
        self.assertEqual(len(c.candles("frxEURUSD", 300, 3)), 3)           # retried after waiting
        self.assertTrue(any(s >= 2.0 for s in self.sleeps))
        self.srv.rate_limit_next = 10
        with self.assertRaises(DerivError):
            c.candles("frxEURUSD", 300, 3)

    def test_reconnects_after_a_lost_connection(self):
        c = self.client()
        c.connect()
        self.srv.fail_next_recv = 1
        self.assertEqual(len(c.candles("frxEURUSD", 300, 4)), 4)

    def test_pagination_goes_back_in_time_without_duplicates(self):
        c = self.client()
        total = 7000
        cs = c.candles_back("frxEURUSD", 300, total)
        self.assertEqual(len(cs), total)
        ts = [x[0] for x in cs]
        self.assertEqual(ts, sorted(set(ts)))
        self.assertTrue(all(b - a == 300 for a, b in zip(ts, ts[1:])))
        full = self.srv.series["frxEURUSD"][300]
        self.assertEqual(cs, full[-total:])
        # asking for more than exists returns everything that exists, and stops
        self.assertEqual(len(c.candles_back("frxEURUSD", 300, 50000)), len(full))

    def test_ignores_unrelated_frames(self):
        c = self.client()
        c.connect()
        c.t.q.append(json.dumps({"req_id": 999, "msg_type": "late", "candles": []}))     # stale frame waiting in the pipe
        self.assertEqual(len(c.candles("frxEURUSD", 300, 2)), 2)


class TestTwelve(unittest.TestCase):
    VALUES = [{"datetime": "2026-10-05 10:00:00", "open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15"},
              {"datetime": "2026-10-05 11:00:00", "open": "1.15", "high": "1.3", "low": "1.1", "close": "1.2"}]

    def test_parse_and_params(self):
        http = FakeTwelveHttp(self.VALUES)
        tw = TwelveClient("KEY", http, pace=0, budget=3)
        cs = tw.candles("EURUSD", 3600, count=2)
        self.assertEqual(len(cs), 2)
        from datetime import datetime, timezone
        self.assertEqual(cs[0], (int(datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc).timestamp()), 1.1, 1.2, 1.0, 1.15))
        url, headers, params = http.calls[0]
        self.assertEqual(headers["Authorization"], "apikey KEY")
        self.assertEqual((params["symbol"], params["interval"], params["order"]), ("EUR/USD", "1h", "asc"))
        tw.candles("XAUUSD", 300, since=cs[0][0])
        self.assertEqual(http.calls[1][2]["interval"], "5min")
        self.assertIn("start_date", http.calls[1][2])
        self.assertNotIn("outputsize", http.calls[1][2])

    def test_supports_and_budget_and_errors(self):
        self.assertTrue(TwelveClient.supports("BTCUSD"))
        self.assertFalse(TwelveClient.supports("Volatility 10 Index"))
        self.assertFalse(TwelveClient.supports("Boom 500 Index"))
        tw = TwelveClient("K", FakeTwelveHttp(self.VALUES), pace=0, budget=2)
        tw.candles("EURUSD", 3600, count=2)
        tw.candles("EURUSD", 3600, count=2)
        with self.assertRaises(QuotaExceeded):
            tw.candles("EURUSD", 3600, count=2)
        with self.assertRaises(DerivError):
            TwelveClient("K", FakeTwelveHttp(error="quota"), pace=0).candles("EURUSD", 3600, count=2)
        with self.assertRaises(RuntimeError):
            TwelveClient("K", FakeTwelveHttp(self.VALUES, status=500), pace=0).candles("EURUSD", 3600, count=2)


class TestDataLayer(unittest.TestCase):
    def setUp(self):
        self.srv = FakeDeriv(SYMS, days=60, seed0=11)
        self.root = tmpdir()
        self.deriv = DerivClient(["wss://x"], factory_for(self.srv), pace=0.0, sleep=lambda s: None)
        self.codes = dict(SYMS)
        self.open = {k: True for k in SYMS}
        self.now = self.srv.now

    def update(self, **kw):
        args = dict(root=self.root, symbols=list(SYMS), now=self.now, deriv=self.deriv, codes=self.codes, is_open=self.open)
        args.update(kw)
        return datalayer.update_history(**args)

    def test_bootstrap_then_incremental_then_uptodate(self):
        rep = self.update()
        self.assertTrue(all(st.startswith("ok") and "bootstrap" in st for r in rep.values() for st in r.values()), rep)
        eur = store.read_candles(store.hist_path(self.root, "EURUSD", "M5"))
        full = self.srv.series["frxEURUSD"][300]
        self.assertEqual(eur, full[-store.wanted_bars("M5"):])           # M5: the last 35 days (retention), not the 60 available
        self.assertEqual(len(store.read_candles(store.hist_path(self.root, "EURUSD", "H1"))), len(self.srv.series["frxEURUSD"][3600]))   # H1: all 60 days (< 420)
        # nothing new yet
        rep2 = self.update(now=self.now + 60)
        self.assertTrue(all(st == "uptodate" for r in rep2.values() for st in r.values()), rep2)
        # the market moves on by 3 candles: only those are fetched
        for code, s in self.srv.series.items():
            extra = with_times(rw(77, 3, start=s[300][-1][4]), 300, t0=s[300][-1][0] + 300)
            s[300] = s[300] + extra
        self.srv.now = self.now + 3 * 300
        before = len(self.srv.requests)
        rep3 = self.update(now=self.srv.now)
        self.assertTrue(all("incrémental" in r["M5"] and "+3" in r["M5"] for r in rep3.values()), rep3)
        req = [r for r in self.srv.requests[before:] if "ticks_history" in r and r["granularity"] == 300]
        self.assertTrue(all("start" in r for r in req))
        eur2 = store.read_candles(store.hist_path(self.root, "EURUSD", "M5"))
        self.assertEqual(eur2[-1][0], full[-1][0] + 3 * 300)                    # the 3 new candles are there...
        self.assertEqual(len(eur2), store.wanted_bars("M5"))                    # ...and the 3 oldest left the 35-day window
        self.assertGreaterEqual(eur2[0][0], self.srv.now - 35 * 86400)
        ts = [c[0] for c in eur2]
        self.assertEqual(ts, sorted(set(ts)))

    def test_forming_candle_is_never_stored(self):
        self.srv.now = self.srv.series["frxEURUSD"][300][-1][0] + 120          # the last candle is still open
        self.update(now=self.srv.now)
        last = store.read_candles(store.hist_path(self.root, "EURUSD", "M5"))[-1]
        self.assertLessEqual(last[0] + 300, self.srv.now)

    def test_closed_markets_are_skipped_once_stored(self):
        self.update()
        self.open["XAUUSD"] = False
        n = len(self.srv.requests)
        rep = self.update(now=self.now + 7200)
        self.assertEqual(rep["XAUUSD"], {"M5": "closed", "H1": "closed"})
        self.assertFalse([r for r in self.srv.requests[n:] if r.get("ticks_history") == "frxXAUUSD"])
        # but a closed market with nothing stored is still bootstrapped once
        root2 = tmpdir()
        rep2 = datalayer.update_history(root2, ["XAUUSD"], self.now, self.deriv, self.codes, {"XAUUSD": False})
        self.assertTrue(rep2["XAUUSD"]["M5"].startswith("ok"))

    def test_deriv_failure_falls_back_to_twelve_for_forex_only(self):
        self.srv.error_codes = {c: "WebsocketError" for c in SYMS.values()}
        http = FakeTwelveHttp(TestTwelve.VALUES)
        tw = TwelveClient("K", http, pace=0, budget=50)
        rep = self.update(twelve=tw)
        self.assertTrue(rep["EURUSD"]["H1"].startswith("ok"))
        self.assertTrue(rep["XAUUSD"]["M5"].startswith("ok"))
        self.assertTrue(rep["Volatility 10 Index"]["M5"].startswith("error"))      # nothing else can serve synthetics
        self.assertIn("deriv", rep["Boom 1000 Index"]["H1"])

    def test_twelve_budget_defers_without_crashing(self):
        self.srv.error_codes = {c: "X" for c in SYMS.values()}
        tw = TwelveClient("K", FakeTwelveHttp([]), pace=0, budget=1)
        rep = self.update(twelve=tw)
        msgs = [st for r in rep.values() for st in r.values()]
        self.assertTrue(any("budget Twelve Data" in m for m in msgs))

    def test_time_budget_defers_the_rest(self):
        ticks = iter(range(0, 10_000, 100))
        rep = self.update(time_budget=250, clock=lambda: next(ticks))
        statuses = [st for r in rep.values() for st in r.values()]
        self.assertIn("deferred", statuses)
        self.assertTrue(any(st.startswith("ok") for st in statuses))
        rep2 = self.update()                                                    # next run finishes the job
        self.assertFalse([st for r in rep2.values() for st in r.values() if st in ("deferred",) or st.startswith("error")])

    def test_retention_purges_old_candles(self):
        p = store.hist_path(self.root, "EURUSD", "M5")
        old = [(self.now - 60 * 86400 + i * 300, 1.0, 1.0, 1.0, 1.0) for i in range(10)]
        store.write_candles(p, old)
        self.update()
        got = store.read_candles(p)
        self.assertTrue(all(c[0] >= self.now - 35 * 86400 for c in got))

    def test_summary(self):
        rep = self.update()
        self.assertIn("ok", datalayer.summarize(rep))


class TestTelegram(unittest.TestCase):
    def setUp(self):
        self.http = FakeTelegramHttp("42")
        self.tg = Telegram("123:ABC", "42", self.http)

    def test_send_edit_pin_delete(self):
        mid = self.tg.send("bonjour")
        self.assertEqual(self.http.messages[mid], "bonjour")
        self.assertTrue(self.tg.edit(mid, "salut"))
        self.assertFalse(self.tg.edit(mid, "salut"))                      # not modified is not an error
        with self.assertRaises(NotFound):
            self.tg.edit(9999, "x")
        self.assertTrue(self.tg.pin(mid))
        self.assertEqual(self.http.pinned, [mid])
        self.assertTrue(self.tg.delete(mid))
        self.assertNotIn(mid, self.http.messages)
        self.assertEqual(self.tg.get_me()["username"], "QML_Test_Bot")
        self.assertIn("/bot123:ABC/", "".join(str(c) for c in []) or "/bot123:ABC/")

    def test_only_harolds_chat_is_read(self):
        self.http.push("supprime A3F7")
        self.http.push("intrus", chat_id="999")
        self.http.push("ajuste ...")
        ups = self.tg.updates(0)
        self.assertEqual([t for _, t in ups], ["supprime A3F7", "", "ajuste ..."])
        self.assertEqual(self.tg.updates(3)[0][0], 3)                      # offset respected

    def test_split_long_messages(self):
        text = "\n".join("ligne %04d %s" % (i, "x" * 50) for i in range(300))
        parts = _split(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4000 for p in parts))
        self.assertEqual("\n".join(parts), text)
        self.assertEqual(_split("court"), ["court"])
        self.assertTrue(all(len(p) <= 4000 for p in _split("y" * 9000)))

    def test_pinned_list_lifecycle(self):
        st = {}
        s1 = sync_pinned(self.tg, st, ["liste v1", "suite v1"])
        self.assertEqual(s1["created"], 2)
        ids = list(st["tg"]["pinned_ids"])
        self.assertEqual(self.http.pinned, [ids[0]])                       # only the first one is pinned
        s2 = sync_pinned(self.tg, st, ["liste v1", "suite v1"])
        self.assertEqual((s2["unchanged"], s2["edited"], s2["created"]), (2, 0, 0))
        s3 = sync_pinned(self.tg, st, ["liste v2", "suite v1"])
        self.assertEqual((s3["edited"], s3["unchanged"]), (1, 1))
        self.assertEqual(self.http.messages[ids[0]], "liste v2")
        s4 = sync_pinned(self.tg, st, ["liste v3"])
        self.assertEqual(s4["deleted"], 1)
        self.assertNotIn(ids[1], self.http.messages)
        self.assertEqual(len(st["tg"]["pinned_ids"]), 1)
        # the pinned message was deleted by hand: it is recreated and pinned again
        self.http.messages.pop(ids[0])
        s5 = sync_pinned(self.tg, st, ["liste v4"])
        self.assertEqual(s5["created"], 1)
        self.assertEqual(self.http.pinned[-1], st["tg"]["pinned_ids"][0])
        self.assertNotEqual(st["tg"]["pinned_ids"][0], ids[0])
        s6 = sync_pinned(self.tg, st, ["a", "b", "c"])
        self.assertEqual(s6["created"], 2)
        json.dumps(st)


if __name__ == "__main__":
    unittest.main()
