"""One run of the scanner (what the GitHub workflow executes every 15 minutes).

    python -m qml_scanner.runner               normal run
    python -m qml_scanner.runner --diagnose    connectivity test (Deriv endpoints, Twelve Data, Telegram, Groq)

Order of a run:
 1. resolve the Deriv symbol codes of the watched assets (cached if Deriv does not answer);
 2. update the stored history (bootstrap, then incremental);
 3. read Harold's new Telegram messages and execute them (commands / Groq agent) against the CURRENT opportunities;
 4. run the QML scan cycle (his deletions / adjustments / proposals are already in the state);
 5. send the replies, then one alert per event, then refresh the pinned list; save everything.
"""
import copy
import os
import sys
import time
import traceback
from typing import Dict, List, Optional

from . import config, datalayer, store
from .agent import Agent, Ctx
from .scanner import run_cycle
from .sources import DerivClient, TwelveClient, WSTransport, deriv_urls, resolve_symbols
from .telegram_io import Telegram, sync_pinned


def _ensure_state(state: Dict) -> Dict:
    for k, v in (("clusters", {}), ("dismissed", []), ("feedback", []), ("manual", []), ("overrides", []), ("ledger", []),
                 ("params", {"global": {}, "filters": {}}), ("suggestions", []), ("tg", {}), ("symbols", {})):
        state.setdefault(k, v)
    return state


def run_once(root: str, now: int, deriv: Optional[DerivClient], twelve: Optional[TwelveClient], tg: Optional[Telegram],
             agent: Optional[Agent], symbols: Optional[List[str]] = None, time_budget: float = 480.0, log=print,
             clock=time.time) -> Dict:
    symbols = list(symbols or config.ALL_SYMBOLS)
    state = _ensure_state(store.load_state(root))
    stats: Dict = {"messages": 0, "events": 0, "opportunities": 0, "history": "", "missing": [], "pinned": {}}

    # 1) symbol codes
    codes, is_open = state["symbols"].get("codes", {}), state["symbols"].get("open", {})
    if deriv is not None:
        try:
            codes, missing, is_open = resolve_symbols(deriv.active_symbols(), symbols)
            state["symbols"] = {"codes": codes, "open": is_open, "missing": missing, "t": now}
        except Exception as e:
            log("liste des actifs Deriv indisponible : %s" % str(e)[:200])
    stats["missing"] = state["symbols"].get("missing", [])

    # 2) history
    report = datalayer.update_history(root, symbols, now, deriv, codes, is_open, twelve, time_budget, clock)
    stats["history"] = datalayer.summarize(report)
    stats["history_report"] = report
    log("historique : %s" % stats["history"])
    history = store.load_history(root, symbols)

    # 3) Harold's messages (executed on the opportunities of THIS moment; ids are stable)
    replies: List[str] = []
    if tg is not None and agent is not None:
        incoming = tg.updates(state["tg"].get("offset", 0))
        todo = [(uid, text) for uid, text in incoming]
        if todo:
            pre = run_cycle(history, copy.deepcopy(state), now)
            ctx = Ctx(state, pre.opportunities, now, history, {})
            for uid, text in todo:
                if text.strip():
                    replies.append(agent.handle(text, ctx))
                    stats["messages"] += 1
                state["tg"]["offset"] = uid + 1

    # 4) scan cycle
    res = run_cycle(history, state, now)
    state = res.state
    stats["opportunities"] = len(res.opportunities)
    stats["events"] = len(res.events)

    # 5) Telegram output
    if tg is not None:
        for r in replies:
            tg.send(r)
        for a in res.alerts:
            tg.send(a)
        reported = state["tg"].get("missing_reported", [])
        if stats["missing"] and sorted(stats["missing"]) != sorted(reported):
            tg.send("ℹ️ Actifs introuvables chez Deriv (ignorés pour l'instant) : " + ", ".join(stats["missing"]))
            state["tg"]["missing_reported"] = list(stats["missing"])
        stats["pinned"] = sync_pinned(tg, state, res.pinned)
    store.save_state(root, state)
    return stats


def _build(env: Dict[str, str]):
    import requests
    http = requests.Session()
    deriv = DerivClient(deriv_urls(env.get("DERIV_APP_ID")), WSTransport)
    twelve = TwelveClient(env["TWELVEDATA_API_KEY"], http) if env.get("TWELVEDATA_API_KEY") else None
    tg = Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], http) if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID") else None
    agent = Agent(env.get("GROQ_API_KEY"), http)
    return http, deriv, twelve, tg, agent


def _notify_error(root: str, tg: Optional[Telegram], text: str, now: int) -> None:
    """Tell Harold about a crash, at most once every 3 hours."""
    if tg is None:
        return
    flag = os.path.join(root, "last_error.txt")
    try:
        last = int(open(flag).read().strip()) if os.path.exists(flag) else 0
    except ValueError:
        last = 0
    if now - last < 3 * 3600:
        return
    try:
        tg.send("⚠️ Erreur du scanner QML : " + text[:600])
        os.makedirs(root, exist_ok=True)
        open(flag, "w").write(str(now))
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)
    root = env.get("DATA_DIR", "data")
    if "--diagnose" in argv:
        from .diagnose import main as diag_main
        return diag_main(env)
    now = int(time.time())
    http, deriv, twelve, tg, agent = _build(env)
    try:
        stats = run_once(root, now, deriv, twelve, tg, agent)
        print("OK :", {k: v for k, v in stats.items() if k != "history_report"})
        return 0
    except Exception as e:
        traceback.print_exc()
        _notify_error(root, tg, "%s: %s" % (type(e).__name__, e), now)
        return 1
    finally:
        deriv.close()


if __name__ == "__main__":
    sys.exit(main())
