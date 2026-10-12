import json
import os
import tempfile
import unittest

from fakes import FakeDeriv, FakeGroqHttp, FakeTelegramHttp, FakeTwelveHttp, factory_for
from helpers import make_history, make_setup
from qml_scanner import config, runner, store
from qml_scanner.agent import Agent, Ctx, TOOLS, plain_text
from qml_scanner.commands import HELP
from qml_scanner.diagnose import run_diagnostic
from qml_scanner.notify import empty_state, plan_notifications
from qml_scanner.selection import cluster, correlation_tags
from qml_scanner.sources import DerivClient, TwelveClient
from qml_scanner.telegram_io import Telegram

NOW = 1_800_000_000


def ctx_with(*setups):
    opps = cluster(list(setups))
    correlation_tags(opps)
    ev, st = plan_notifications(opps, list(setups), empty_state())
    return Ctx(st, opps, NOW, {}, {})


def call(name, args, cid="c1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def final(text):
    return {"role": "assistant", "content": text}


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.ctx = ctx_with(make_setup(symbol="XAUUSD", side="sell", tf="H1", t_left=1),
                            make_setup(symbol="EURUSD", side="buy", tf="H4", t_left=2, lo=90, hi=91, entry=90.5))
        self.xau = [o for o in self.ctx.opps if o.symbol == "XAUUSD"][0]

    def test_clear_commands_never_call_the_model(self):
        g = FakeGroqHttp()
        a = Agent("KEY", g)
        reply = a.handle("supprime %s car test" % self.xau.id, self.ctx)
        self.assertIn("Supprimée", reply)
        self.assertEqual(g.requests, [])
        self.assertTrue(self.ctx.changed)

    def test_free_text_goes_through_the_tool_loop(self):
        g = FakeGroqHttp([call("dismiss_opportunity", {"symbol": "XAUUSD", "reason": "pas de vraie cassure"}), final("C'est fait : XAUUSD supprimée.")])
        reply = Agent("KEY", g).handle("franchement le setup sur l'or ne me plaît pas, vire-le", self.ctx)
        self.assertEqual(reply, "C'est fait : XAUUSD supprimée.")
        self.assertEqual(len(self.ctx.state["dismissed"]), 1)
        self.assertEqual(self.ctx.state["dismissed"][0]["reason"], "pas de vraie cassure")
        self.assertTrue(self.ctx.changed)
        # the system prompt carried the real opportunities (ids) and the second call carried the tool result
        sys_prompt = g.requests[0]["messages"][0]["content"]
        self.assertIn(self.xau.id, sys_prompt)
        self.assertIn("n'inventes JAMAIS", sys_prompt)
        self.assertEqual(g.requests[1]["messages"][-1]["role"], "tool")
        self.assertIn("Supprimée", g.requests[1]["messages"][-1]["content"])
        self.assertEqual(g.requests[0]["model"], "openai/gpt-oss-120b")
        self.assertEqual(len(g.requests[0]["tools"]), len(TOOLS))

    def test_replies_are_plain_text_for_telegram(self):
        md = ("Sur le **Volatility 75** :\n\n| ID | Actif | Sens |\n|----|-------|------|\n"
              "| **XDYU** | Volatility 75 Index | **achat** |\n\n## Bilan\nAucune `entrée`.")
        reply = Agent("KEY", FakeGroqHttp([final(md)])).handle("que vois-tu sur le Volatility 75", self.ctx)
        self.assertNotIn("*", reply)
        self.assertNotIn("|", reply)
        self.assertNotIn("`", reply)
        self.assertNotIn("#", reply)
        self.assertIn("XDYU · Volatility 75 Index · achat", reply)
        self.assertIn("Sur le Volatility 75 :", reply)
        self.assertEqual(plain_text("EURUSD - achat : 1.0850 - 1.0860"), "EURUSD - achat : 1.0850 - 1.0860")   # ordinary dashes survive

    def test_adjust_add_restore_study_apply_tools(self):
        eur = [o for o in self.ctx.opps if o.symbol == "EURUSD"][0]
        g = FakeGroqHttp([call("adjust_opportunity", {"id": eur.id, "zone_low": 90.2, "zone_high": 91.5, "entry": 90.4, "reason": "zone trop étroite"}),
                          final("Zone ajustée.")])
        reply = Agent("K", g).handle("élargis la zone de l'euro, mets-la entre 90.2 et 91.5 avec la ligne à 90.4", self.ctx)
        self.assertEqual(reply, "Zone ajustée.")
        ov = self.ctx.state["overrides"][0]
        self.assertEqual((ov["zone_low"], ov["zone_high"], ov["entry"]), (90.2, 91.5, 90.4))
        a = Agent("K", FakeGroqHttp())
        out = a.run_tool("add_setup", {"symbol": "GBPUSD", "tf": "H1", "side": "vente", "zone_low": 1.3, "zone_high": 1.31, "entry": 1.3, "head": 1.32,
                                       "support": 1.28, "extreme": 1.27, "note": "vu à l'œil"}, self.ctx)
        self.assertIn("Setup enregistré", out)
        m = self.ctx.state["manual"][0]
        self.assertEqual((m["head"], m["support"], m["extreme"], m["note"]), (1.32, 1.28, 1.27, "vu a l'œil"))
        self.assertIn("Aucune suppression à annuler", a.run_tool("restore_opportunity", {"id": "ZZZZ"}, self.ctx))
        self.assertIn("ZZZZ", "ZZZZ" + a.run_tool("study", {}, self.ctx))
        self.assertIn("CE QUE J'AI APPRIS", a.run_tool("study", {}, self.ctx))
        self.assertIn("Aucun réglage", a.run_tool("show_settings", {}, self.ctx))
        self.assertIn("réinitialisés", a.run_tool("reset_settings", {}, self.ctx))
        self.assertIn("pas de suggestion", a.run_tool("apply_suggestion", {"n": 5}, self.ctx))
        self.assertIn("Aucune opportunité supprimée", a.run_tool("list_dismissed", {}, self.ctx))

    def test_get_opportunities_is_a_read_only_json_view(self):
        a = Agent("K", FakeGroqHttp())
        rows = json.loads(a.run_tool("get_opportunities", {}, self.ctx))
        self.assertEqual({r["symbol"] for r in rows}, {"XAUUSD", "EURUSD"})
        self.assertEqual({r["side"] for r in rows}, {"vente", "achat"})
        only = json.loads(a.run_tool("get_opportunities", {"symbol": "xau"}, self.ctx))
        self.assertEqual([r["symbol"] for r in only], ["XAUUSD"])
        self.assertEqual(self.ctx.state["dismissed"], [])

    def test_robustness(self):
        a = Agent("K", FakeGroqHttp([{"role": "assistant", "content": "", "tool_calls": [{"id": "x", "type": "function", "function": {"name": "dismiss_opportunity", "arguments": "{pas du json"}}]}, final("Dis-moi laquelle.")]))
        self.assertEqual(a.handle("bla bla", self.ctx), "Dis-moi laquelle.")
        self.assertEqual(self.ctx.state["dismissed"], [])
        self.assertIn("Outil inconnu", a.run_tool("format_disk", {}, self.ctx))
        loop = Agent("K", FakeGroqHttp([call("get_opportunities", {}, "a")] * 10), max_steps=3)
        self.assertIn("reformuler", loop.handle("bla", self.ctx))
        no_key = Agent("", FakeGroqHttp())
        self.assertIn("clé Groq absente", no_key.handle("bla bla", self.ctx))
        down = Agent("K", FakeGroqHttp(error=ConnectionError("timeout")))
        txt = down.handle("bla bla", self.ctx)
        self.assertIn("Je n'ai pas pu joindre l'IA", txt)
        self.assertIn("Exemples", txt)
        self.assertEqual(self.ctx.state["dismissed"], [])


SYMS = {"EURUSD": "frxEURUSD", "XAUUSD": "frxXAUUSD", "Volatility 75 Index": "R_75", "Boom 500 Index": "BOOM500", "Crash 300 Index": "CRASH300",
        "Jump 25 Index": "JD25", "GBPUSD": "frxGBPUSD", "Volatility 10 Index": "R_10"}


class TestRunner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FakeDeriv(SYMS, days=60, seed0=20)

    def make(self):
        root = tempfile.mkdtemp(prefix="qml_run_")
        http = FakeTelegramHttp("42")
        tg = Telegram("1:T", "42", http)
        deriv = DerivClient(["wss://x"], factory_for(self.srv), pace=0.0, sleep=lambda s: None)
        agent = Agent(None, FakeGroqHttp())
        return root, http, tg, deriv, agent

    def go(self, root, tg, deriv, agent, now=None, symbols=None, **kw):
        return runner.run_once(root, now or self.srv.now, deriv, kw.pop("twelve", None), tg, agent, symbols=symbols or list(SYMS), log=lambda *_: None, **kw)

    def test_full_life_of_the_scanner(self):
        root, http, tg, deriv, agent = self.make()
        s1 = self.go(root, tg, deriv, agent)
        self.assertGreater(s1["opportunities"], 0)
        self.assertEqual(s1["events"], s1["opportunities"])                  # each opportunity announced once, as "new"
        self.assertIn("ok", s1["history"])
        texts = list(http.messages.values())
        pinned_ids = store.load_state(root)["tg"]["pinned_ids"]
        alerts = [t for k, t in http.messages.items() if k not in pinned_ids]
        self.assertTrue(all(t.startswith(("🎯 PRIX DANS LA ZONE", "🔁")) for t in alerts))     # no 🆕 / ⚠️ / ❌ alert any more
        self.assertEqual(len(pinned_ids), 1)
        pinned_text = http.messages[pinned_ids[0]]
        self.assertIn("OPPORTUNITÉS QML EN COURS", pinned_text)
        self.assertEqual(http.pinned, pinned_ids)
        # rules of the strategy hold on everything that was announced
        self.assertNotIn("Boom 500 Index (vente)", pinned_text)
        for t in alerts:
            if "Boom 500 Index" in t:
                self.assertIn("ACHAT", t)
            if "Crash 300 Index" in t:
                self.assertIn("VENTE", t)
        self.assertNotIn(" M5 ", pinned_text)

        n_msgs = len(http.messages)
        s2 = self.go(root, tg, deriv, agent, now=self.srv.now + 60)       # nothing happened: complete silence
        self.assertEqual((s2["events"], s2["messages"]), (0, 0))
        self.assertEqual(len(http.messages), n_msgs)
        self.assertEqual((s2["pinned"]["created"], s2["pinned"]["deleted"]), (0, 0))        # at most the "mis à jour" stamp is edited, silently
        self.assertLessEqual(s2["pinned"]["edited"], 1)

        # Harold deletes one opportunity from Telegram
        st = store.load_state(root)
        target = list(st["clusters"].values())[0]
        http.push("supprime %s car pas valide à l'oeil" % target["id"])
        s3 = self.go(root, tg, deriv, agent, now=self.srv.now + 120)
        self.assertEqual(s3["messages"], 1)
        self.assertEqual(s3["opportunities"], s1["opportunities"] - 1)
        self.assertTrue(any("Supprimée" in t for t in http.messages.values()))
        new_pinned = http.messages[store.load_state(root)["tg"]["pinned_ids"][0]]
        self.assertNotIn("#" + target["id"], new_pinned)
        self.assertEqual(s3["events"], 0)
        self.assertEqual(store.load_state(root)["tg"]["offset"], 2)
        # the message is not processed twice
        s4 = self.go(root, tg, deriv, agent, now=self.srv.now + 180)
        self.assertEqual((s4["messages"], s4["events"]), (0, 0))
        self.assertEqual(len(store.load_state(root)["dismissed"]), 1)

    def test_a_stranger_cannot_command_the_bot(self):
        root, http, tg, deriv, agent = self.make()
        self.go(root, tg, deriv, agent)
        target = list(store.load_state(root)["clusters"].values())[0]
        http.push("supprime %s" % target["id"], chat_id="999")
        s = self.go(root, tg, deriv, agent, now=self.srv.now + 60)
        self.assertEqual(s["messages"], 0)
        self.assertEqual(store.load_state(root)["dismissed"], [])

    def test_manual_setup_and_study_through_telegram(self):
        root, http, tg, deriv, agent = self.make()
        self.go(root, tg, deriv, agent)
        price = self.srv.series["frxEURUSD"][300][-1][4]
        far = price * 1.3
        http.push("ajoute EURUSD H1 vente zone %.5f %.5f ligne %.5f" % (far, far + 0.5, far))
        http.push("bilan")
        s = self.go(root, tg, deriv, agent, now=self.srv.now + 60)
        self.assertEqual(s["messages"], 2)
        texts = list(http.messages.values())
        self.assertTrue(any("Setup enregistré" in t for t in texts))
        self.assertTrue(any("CE QUE J'AI APPRIS" in t for t in texts))
        st = store.load_state(root)
        self.assertEqual(len(st["manual"]), 1)
        self.assertIsNotNone(st["manual"][0]["diag"])
        pinned = http.messages[st["tg"]["pinned_ids"][0]]
        self.assertIn("✋", pinned)

    def test_symbols_missing_at_deriv_are_reported_once(self):
        root, http, tg, deriv, agent = self.make()
        syms = list(SYMS) + ["Volatility 250 (1s) Index"]
        s = self.go(root, tg, deriv, agent, symbols=syms)
        self.assertEqual(s["missing"], ["Volatility 250 (1s) Index"])
        self.assertEqual(len([t for t in http.messages.values() if "introuvables" in t]), 1)
        self.go(root, tg, deriv, agent, symbols=syms, now=self.srv.now + 60)
        self.assertEqual(len([t for t in http.messages.values() if "introuvables" in t]), 1)

    def test_deriv_down_uses_cached_codes_and_stored_history(self):
        root, http, tg, deriv, agent = self.make()
        s1 = self.go(root, tg, deriv, agent)
        broken = DerivClient(["wss://x"], factory_for(self.srv, bad_urls=("x",)), pace=0.0, sleep=lambda s: None)
        n = len(http.messages)
        s2 = self.go(root, tg, broken, agent, now=self.srv.now + 3000)
        self.assertEqual(s2["opportunities"], s1["opportunities"])             # the scan still works on the stored history
        self.assertGreaterEqual(len(http.messages), n)

    def test_state_survives_a_restart(self):
        root, http, tg, deriv, agent = self.make()
        s1 = self.go(root, tg, deriv, agent)
        snapshot = json.dumps(store.load_state(root), sort_keys=True)
        tg2 = Telegram("1:T", "42", http)
        s2 = self.go(root, tg2, DerivClient(["wss://x"], factory_for(self.srv), pace=0.0, sleep=lambda s: None), Agent(None, FakeGroqHttp()),
                      now=self.srv.now + 30)
        self.assertEqual(s2["events"], 0)
        a = json.loads(snapshot)
        b = store.load_state(root)
        self.assertEqual(set(a["clusters"]), set(b["clusters"]))

    def test_notify_error_is_throttled(self):
        root, http, tg, deriv, agent = self.make()
        runner._notify_error(root, tg, "boom", NOW)
        runner._notify_error(root, tg, "boom again", NOW + 600)
        runner._notify_error(root, tg, "much later", NOW + 4 * 3600)
        sent = [t for t in http.messages.values() if t.startswith("⚠️ Erreur")]
        self.assertEqual(len(sent), 2)


class TestDiagnostic(unittest.TestCase):
    def test_report_lists_what_works_and_what_fails(self):
        srv = FakeDeriv({"EURUSD": "frxEURUSD", "XAUUSD": "frxXAUUSD", "BTCUSD": "cryBTCUSD", "Volatility 10 Index": "R_10", "Boom 1000 Index": "BOOM1000"},
                        days=3, seed0=1)
        http = FakeTelegramHttp("42")
        tg = Telegram("1:T", "42", http)
        env = {"DERIV_APP_ID": "34Aon8j01A4dcV5n69KHB", "TWELVEDATA_API_KEY": "K", "GROQ_API_KEY": "G", "TELEGRAM_BOT_TOKEN": "1:T", "TELEGRAM_CHAT_ID": "42"}

        class Http(FakeGroqHttp):
            def get(self, *a, **k):
                return FakeTwelveHttp(TestTwelveValues).get(*a, **k)

        rep = run_diagnostic(env, Http([final("OK")]), factory_for(srv, bad_urls=("api.derivws.com",)), tg)
        self.assertIn("DIAGNOSTIC", rep)
        self.assertIn("❌ wss://api.derivws.com/trading/v1/options/ws/public", rep)
        self.assertIn("520 Cloudflare", rep)
        self.assertIn("✅ connexion : wss://ws.derivws.com/websockets/v3", rep)
        self.assertIn("retrouvés : 5/%d" % len(config.ALL_SYMBOLS), rep)
        self.assertIn("EURUSD M5 : 3 bougies", rep)
        self.assertIn("✅ Telegram : bot @QML_Test_Bot", rep)
        self.assertIn("✅ Groq", rep)
        self.assertIn("✅ Twelve Data", rep)
        rep2 = run_diagnostic({}, FakeGroqHttp(), factory_for(srv, bad_urls=("derivws", "binaryws")), None)
        self.assertIn("❌ wss://ws.binaryws.com", rep2)
        self.assertIn("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant", rep2)
        self.assertIn("pas de clé", rep2)


TestTwelveValues = [{"datetime": "2026-10-05 10:00:00", "open": "1.1", "high": "1.2", "low": "1.0", "close": "1.15"}]


if __name__ == "__main__":
    unittest.main()
