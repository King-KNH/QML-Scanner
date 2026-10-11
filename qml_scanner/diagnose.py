"""First-run diagnostic: tells exactly what works from the machine that runs it (GitHub Actions).
It is the answer to « Deriv blocked us last time »: each endpoint is tested separately and the exact error is reported.
"""
import sys
import time
from typing import Dict, List, Optional

from . import config
from .sources import DerivClient, TwelveClient, WSTransport, deriv_urls, resolve_symbols
from .telegram_io import Telegram

SAMPLES = ["EURUSD", "XAUUSD", "BTCUSD", "Volatility 10 Index", "Boom 1000 Index"]


def check_deriv(urls: List[str], factory=WSTransport, now: Optional[int] = None, sleep=time.sleep) -> List[str]:
    lines: List[str] = []
    now = now or int(time.time())
    for url in urls:
        shown = url.split("?")[0]
        c = DerivClient([url], factory, pace=0.0, sleep=lambda s: None)
        try:
            c.connect()
        except Exception as e:
            lines.append("❌ %s\n    %s" % (shown, c.errors.get(url, str(e))[:240]))
            continue
        lines.append("✅ connexion : %s" % shown)
        try:
            active = c.active_symbols()
            codes, missing, is_open = resolve_symbols(active, config.ALL_SYMBOLS)
            lines.append("   actifs Deriv : %d · retrouvés : %d/%d" % (len(active), len(codes), len(config.ALL_SYMBOLS)))
            if missing:
                lines.append("   introuvables : " + ", ".join(missing[:20]) + (" …" if len(missing) > 20 else ""))
            closed = [s for s, o in is_open.items() if not o]
            if closed:
                lines.append("   marchés fermés maintenant : %d" % len(closed))
            for name in SAMPLES:
                code = codes.get(name)
                if not code:
                    lines.append("   %s : code introuvable" % name)
                    continue
                for gran, label in ((300, "M5"), (3600, "H1")):
                    try:
                        cs = c.candles(code, gran, 3)
                        age = (now - cs[-1][0]) // 60 if cs else None
                        lines.append("   %s %s : %d bougies, dernière il y a %s min" % (name, label, len(cs), age))
                    except Exception as e:
                        lines.append("   %s %s : ❌ %s" % (name, label, str(e)[:160]))
        except Exception as e:
            lines.append("   ❌ requêtes de données : %s" % str(e)[:200])
        finally:
            c.close()
    return lines


def check_twelve(key: Optional[str], http) -> List[str]:
    if not key:
        return ["— Twelve Data : pas de clé (TWELVEDATA_API_KEY), secours désactivé"]
    try:
        cs = TwelveClient(key, http, pace=0, budget=1).candles("EURUSD", 3600, count=3)
        return ["✅ Twelve Data : %d bougies EUR/USD H1" % len(cs)]
    except Exception as e:
        return ["❌ Twelve Data : %s" % str(e)[:200]]


def check_telegram(tg: Optional[Telegram]) -> List[str]:
    if tg is None:
        return ["❌ Telegram : TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant"]
    try:
        me = tg.get_me()
        return ["✅ Telegram : bot @%s joignable" % me.get("username", "?")]
    except Exception as e:
        return ["❌ Telegram : %s" % str(e)[:200]]


def check_groq(key: Optional[str], http, url: str, model: str) -> List[str]:
    if not key:
        return ["— Groq : pas de clé (GROQ_API_KEY), l'agent IA est désactivé (les commandes simples marchent)"]
    try:
        r = http.post(url, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                      json={"model": model, "messages": [{"role": "user", "content": "Réponds juste : OK"}], "max_tokens": 20}, timeout=40)
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"].get("content") or ""
        return ["✅ Groq (%s) : %s" % (model, txt.strip()[:40] or "réponse vide")]
    except Exception as e:
        return ["❌ Groq : %s" % str(e)[:200]]


def run_diagnostic(env: Dict[str, str], http, factory=WSTransport, tg: Optional[Telegram] = None) -> str:
    from .agent import GROQ_MODEL, GROQ_URL
    lines = ["🔧 DIAGNOSTIC DU SCANNER QML", ""]
    lines.append("Deriv (données de marché publiques) :")
    lines += check_deriv(deriv_urls(env.get("DERIV_APP_ID")), factory)
    lines.append("")
    lines += check_twelve(env.get("TWELVEDATA_API_KEY"), http)
    lines += check_telegram(tg)
    lines += check_groq(env.get("GROQ_API_KEY"), http, GROQ_URL, GROQ_MODEL)
    return "\n".join(lines)


def main(env: Dict[str, str]) -> int:
    import requests
    http = requests.Session()
    tg = Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], http) if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID") else None
    report = run_diagnostic(env, http, WSTransport, tg)
    print(report)
    if tg is not None:
        try:
            tg.send(report[:3900])
        except Exception as e:
            print("envoi Telegram impossible :", e)
    return 0


if __name__ == "__main__":
    import os
    sys.exit(main(dict(os.environ)))
