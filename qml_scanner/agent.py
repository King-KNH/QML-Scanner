"""The conversational agent.

1. Messages that are clear commands (« supprime A3F7 », « ajoute XAUUSD H1 vente zone … ») are executed directly by the tested
   deterministic parser: instant, free, no model involved.
2. Everything else goes to Groq (OpenAI-compatible API, same key / model as Shadow Retracement) with TOOLS: the model only
   translates Harold's words into tool calls and explains the answers. It never invents levels or opportunities: it reads them
   through get_opportunities and acts through the same functions as the commands.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import commands
from .report import fmt_price
from .selection import Opportunity

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
SIDE_FR = {"buy": "achat", "sell": "vente"}

SYSTEM = (
    "Tu es l'assistant personnel de trading de Harold, spécialiste de la stratégie QML (Quasimodo Level). "
    "Tu réponds en français, de façon courte et claire. "
    "Le robot détecte les opportunités ; Harold les valide visuellement et prend ses trades lui-même avec sa gestion du risque : "
    "tu ne passes jamais d'ordre et tu ne donnes pas de conseil d'exécution. "
    "Tu ne prédis jamais la direction du marché. "
    "Tu n'inventes JAMAIS un niveau, une opportunité ou un ID : utilise get_opportunities. "
    "Tu n'agis (supprimer, ajuster, ajouter, appliquer un réglage) que sur demande explicite de Harold ; en cas de doute, pose UNE question. "
    "Après une action, confirme en une phrase ce qui a été fait. "
    "Tu écris en texte brut pour Telegram : jamais de gras, d'étoiles, de titres ni de tableaux. "
    "Une opportunité par ligne, sous la forme « #ID · Actif · Achat ou Vente · TF · zone bas–haut · ligne · statut »."
)


def plain_text(text: str) -> str:
    """Telegram shows the raw characters: drop markdown (bold, headings, code ticks) and turn table rows into one line each."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if "-" in s and re.fullmatch(r"\|?[\s:\-|]+\|?", s):          # table separator or horizontal rule
            continue
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            s = " · ".join(c.strip() for c in s.strip("|").split("|"))
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = s.replace("**", "").replace("__", "").replace("`", "")
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _tool(name: str, description: str, props: Dict, required: Sequence[str] = ()) -> Dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props, "required": list(required)}}}


_S = {"type": "string"}
_N = {"type": "number"}
TOOLS = [
    _tool("get_opportunities", "Liste les opportunités QML en cours (ID, actif, sens, timeframe, zone, ligne, statut).",
          {"symbol": dict(_S, description="filtre facultatif sur l'actif")}),
    _tool("dismiss_opportunity", "Supprime une opportunité que Harold juge non valide. Donne l'id OU l'actif (+ tf, sens).",
          {"id": _S, "symbol": _S, "tf": _S, "side": {"type": "string", "enum": ["achat", "vente"]}, "reason": _S}),
    _tool("adjust_opportunity", "Corrige la zone et/ou la ligne QML d'une opportunité existante.",
          {"id": _S, "zone_low": _N, "zone_high": _N, "entry": _N, "reason": _S}, ["id"]),
    _tool("add_setup", "Enregistre un setup repéré visuellement par Harold (actif exact, timeframe, sens, zone et/ou ligne).",
          {"symbol": _S, "tf": {"type": "string", "enum": ["M15", "M30", "M45", "H1", "H2", "H4", "H8", "D1"]},
           "side": {"type": "string", "enum": ["achat", "vente"]}, "zone_low": _N, "zone_high": _N, "entry": _N,
           "head": _N, "support": _N, "extreme": _N, "note": _S}, ["symbol", "tf", "side"]),
    _tool("restore_opportunity", "Annule la suppression d'une opportunité (id).", {"id": _S}, ["id"]),
    _tool("list_dismissed", "Liste les opportunités supprimées.", {}),
    _tool("study", "Bilan de ce que le robot a appris des suppressions, ajustements et setups proposés, avec suggestions numérotées.", {}),
    _tool("apply_suggestion", "Active la suggestion numéro n du dernier bilan.", {"n": {"type": "integer"}}, ["n"]),
    _tool("show_settings", "Montre les réglages appris actuellement actifs.", {}),
    _tool("reset_settings", "Réinitialise tous les réglages appris.", {}),
]


@dataclass
class Ctx:
    state: Dict
    opps: List[Opportunity]
    now: int
    history: Dict = field(default_factory=dict)
    last_prices: Dict = field(default_factory=dict)
    changed: bool = False


def _num(x) -> str:
    return "%.10g" % float(x)


class Agent:
    def __init__(self, api_key: Optional[str], http, model: str = GROQ_MODEL, max_steps: int = 4, url: str = GROQ_URL):
        self.key, self.http, self.model, self.max_steps, self.url = (api_key or "").strip(), http, model, max_steps, url

    # ---------------------------------------------------------------- entry point
    def handle(self, text: str, ctx: Ctx) -> str:
        parsed = commands.classify(text, ctx.state, ctx.opps)
        if not commands.needs_interpretation(parsed):
            reply, ch = commands.execute(text, ctx.state, ctx.opps, ctx.now, history=ctx.history, last_prices=ctx.last_prices)
            ctx.changed = ctx.changed or ch
            return reply
        reply = commands.HELP
        if not self.key:
            return reply + "\n(L'agent IA n'est pas configuré : clé Groq absente.)"
        try:
            return self._chat(text, ctx)
        except Exception as e:
            return "Je n'ai pas pu joindre l'IA (%s).\n%s" % (str(e)[:120], reply)

    # ---------------------------------------------------------------- model loop
    def _snapshot(self, ctx: Ctx) -> str:
        if not ctx.opps:
            return "Aucune opportunité en cours."
        lines = ["Opportunités en cours (ID | actif | sens | tf | type | zone | ligne | statut) :"]
        for o in ctx.opps[:40]:
            s = o.primary
            lines.append("%s | %s | %s | %s | %s | %s–%s | %s | %s" % (o.id, s.symbol, SIDE_FR[o.side], s.tf, s.qml_type, fmt_price(s.zone_low),
                                                                 fmt_price(s.zone_high), fmt_price(s.entry), s.status))
        return "\n".join(lines)

    def _post(self, messages: List[Dict]) -> Dict:
        r = self.http.post(self.url, headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
                           json={"model": self.model, "messages": messages, "tools": TOOLS, "tool_choice": "auto",
                                 "temperature": 0.2, "max_tokens": 700}, timeout=60)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    def _chat(self, text: str, ctx: Ctx) -> str:
        messages = [{"role": "system", "content": SYSTEM + "\n\n" + self._snapshot(ctx)}, {"role": "user", "content": text}]
        for _ in range(self.max_steps):
            msg = self._post(messages)
            calls = msg.get("tool_calls") or []
            if not calls:
                return plain_text(msg.get("content") or "") or "(pas de réponse)"
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except ValueError:
                    args = {}
                result = self.run_tool(c["function"]["name"], args, ctx)
                messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": result})
        return "J'ai fait ce que j'ai pu, mais la demande est trop longue à traiter : peux-tu la reformuler plus simplement ?"

    # ---------------------------------------------------------------- tools
    def _exec(self, text: str, ctx: Ctx) -> str:
        reply, ch = commands.execute(text, ctx.state, ctx.opps, ctx.now, history=ctx.history, last_prices=ctx.last_prices)
        ctx.changed = ctx.changed or ch
        return reply

    def run_tool(self, name: str, a: Dict, ctx: Ctx) -> str:
        reason = (" car " + str(a["reason"]).strip()) if a.get("reason") else ""
        if name == "get_opportunities":
            sym = (a.get("symbol") or "").lower().replace(" ", "")
            rows = []
            for o in ctx.opps:
                s = o.primary
                if sym and sym not in s.symbol.lower().replace(" ", ""):
                    continue
                rows.append({"id": o.id, "symbol": s.symbol, "side": SIDE_FR[o.side], "tf": s.tf, "type": s.qml_type, "zone": [s.zone_low, s.zone_high],
                             "entry": s.entry, "head": s.head, "status": s.status, "confluence": [c.tf for c in o.confluence], "tags": o.tags})
            return json.dumps(rows, ensure_ascii=False)
        if name == "dismiss_opportunity":
            if a.get("id"):
                return self._exec("supprime %s%s" % (a["id"], reason), ctx)
            if not a.get("symbol"):
                return "Il faut l'id ou l'actif."
            return self._exec("supprime %s %s %s%s" % (a["symbol"], a.get("tf") or "", a.get("side") or "", reason), ctx)
        if name == "adjust_opportunity":
            t = "ajuste %s" % a["id"]
            if a.get("zone_low") is not None and a.get("zone_high") is not None:
                t += " zone %s %s" % (_num(a["zone_low"]), _num(a["zone_high"]))
            if a.get("entry") is not None:
                t += " ligne %s" % _num(a["entry"])
            return self._exec(t + reason, ctx)
        if name == "add_setup":
            t = "ajoute %s %s %s" % (a["symbol"], a["tf"], a["side"])
            if a.get("zone_low") is not None and a.get("zone_high") is not None:
                t += " zone %s %s" % (_num(a["zone_low"]), _num(a["zone_high"]))
            for key, word in (("entry", "ligne"), ("head", "tete"), ("support", "support"), ("extreme", "cassure")):
                if a.get(key) is not None:
                    t += " %s %s" % (word, _num(a[key]))
            if a.get("note"):
                t += " car " + str(a["note"])
            return self._exec(t, ctx)
        if name == "restore_opportunity":
            return self._exec("retablis %s" % a.get("id", ""), ctx)
        if name == "list_dismissed":
            return self._exec("liste des supprimees", ctx)
        if name == "study":
            return self._exec("bilan", ctx)
        if name == "apply_suggestion":
            return self._exec("applique %d" % int(a.get("n", 0)), ctx)
        if name == "show_settings":
            return self._exec("reglages", ctx)
        if name == "reset_settings":
            return self._exec("reinitialise les reglages", ctx)
        return "Outil inconnu : %s" % name
