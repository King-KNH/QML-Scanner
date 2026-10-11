"""Plain-text message builders for Telegram (French). No I/O here.

The global message is a structured list: category -> asset -> opportunities, most advanced status first.
Each opportunity carries a short id (#A3F7) that Harold can use in a command (« supprime A3F7 »).
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from . import config
from .engine import STATUS_RANK
from .notify import Event
from .selection import Opportunity

TG_LIMIT = 4096
SAFE_LIMIT = 3900
STATUS_FR = {"detected": "détecté", "approaching": "à l'approche de la zone", "entered": "prix dans la zone / sur la ligne QML"}
STATUS_ICON = {"detected": "🆕", "approaching": "⚠️", "entered": "🎯"}
KIND_FR = {"MPL": "MPL", "OB": "Order Block", "MPL-MID": "MPL Midzone", "QMLZ": "Zone QML", "MANUEL": "proposée par toi"}


def fmt_price(x: float) -> str:
    a = abs(x)
    if a >= 1000:
        return "%.2f" % x
    if a >= 100:
        return "%.3f" % x
    if a >= 10:
        return "%.4f" % x
    return "%.5f" % x


def _side(o: Opportunity) -> str:
    return "🔴 VENTE" if o.side == "sell" else "🟢 ACHAT"


WAT = timezone(timedelta(hours=1))      # heure du Cameroun


def _ts(t: int) -> str:
    return datetime.fromtimestamp(t, tz=WAT).strftime("%d/%m %H:%M") + " (heure du Cameroun)"


def describe(o: Opportunity) -> str:
    s = o.primary
    lines = [
        "%s · %s · %s · %s" % (_side(o), s.symbol, s.tf, s.qml_type),
        "Zone d'entrée (%s) : %s – %s" % (KIND_FR.get(s.zone_kind, s.zone_kind), fmt_price(s.zone_low), fmt_price(s.zone_high)),
        "Ligne QML (entrée) : %s" % fmt_price(s.entry),
        "Tête : %s · Cassure : %s" % (fmt_price(s.head), fmt_price(s.extreme)),
        "Prix actuel : %s · Statut : %s" % (fmt_price(s.last_price), STATUS_FR[s.status]),
    ]
    if o.confluence:
        lines.append("Confluence : " + ", ".join(c.tf for c in o.confluence))
    for t in o.tags:
        lines.append("↔ " + t)
    lines.append("Épaule gauche : %s" % _ts(s.t_left))
    if s.zone_kind == "MANUEL":
        lines.append("✋ Setup proposé par toi")
    elif s.adjusted:
        lines.append("✏️ Zone / ligne ajustée par toi")
    if o.id:
        lines.append("ID : %s (pour la retirer : « supprime %s »)" % (o.id, o.id))
    return "\n".join(lines)


def format_alert(ev: Event) -> str:
    if ev.kind == "closed":
        why = "invalidé" if ev.reason == "invalidated" else "expiré"
        ident = (" [%s]" % ev.id) if ev.id else ""
        return "❌ Setup %s (%s)%s : %s." % (ev.symbol, "vente" if ev.side == "sell" else "achat", ident, why)
    head = {"new": "🆕 NOUVELLE OPPORTUNITÉ", "approaching": "⚠️ PRIX À L'APPROCHE", "entered": "🎯 PRIX DANS LA ZONE",
            "returned": "🔁 DE RETOUR : le prix revient vers cette opportunité"}[ev.kind]
    extra = "\n(timeframe principal mis à jour)" if ev.primary_changed else ""
    return "%s\n%s%s" % (head, describe(ev.opp), extra)


def _opp_line(o: Opportunity) -> str:
    s = o.primary
    mark = "✋ " if s.zone_kind == "MANUEL" else ("✏️ " if s.adjusted else "")
    parts = ["%s %s %s%s · %s · %s" % (STATUS_ICON[s.status], "🔴" if o.side == "sell" else "🟢", mark,
                                       "Vente" if o.side == "sell" else "Achat", s.tf, s.qml_type),
             "%s–%s" % (fmt_price(s.zone_low), fmt_price(s.zone_high)),
             "ligne %s" % fmt_price(s.entry)]
    if o.confluence:
        parts.append("conf. " + "/".join(c.tf for c in o.confluence))
    if o.id:
        parts.append("#" + o.id)
    return "   " + " · ".join(parts)


def _counts(opps: Sequence[Opportunity]) -> str:
    n = {k: 0 for k in STATUS_RANK}
    for o in opps:
        n[o.primary.status] += 1
    bits = []
    if n["entered"]:
        bits.append("🎯 %d dans la zone" % n["entered"])
    if n["approaching"]:
        bits.append("⚠️ %d à l'approche" % n["approaching"])
    if n["detected"]:
        bits.append("🆕 %d détectée%s" % (n["detected"], "s" if n["detected"] > 1 else ""))
    return " · ".join(bits)


def _grouped(opps: Sequence[Opportunity]):
    """[(category, [(symbol, [opps...]), ...]), ...] in the configured order."""
    cats: Dict[str, Dict[str, List[Opportunity]]] = {}
    for o in opps:
        cats.setdefault(config.category_of(o.symbol), {}).setdefault(o.symbol, []).append(o)
    order = [c for c in config.CATEGORY_ORDER if c in cats] + sorted(c for c in cats if c not in config.CATEGORY_ORDER)
    out = []
    for c in order:
        syms = sorted(cats[c], key=lambda s: (config.SYMBOL_ORDER.get(s, 10_000), s))
        out.append((c, [(s, sorted(cats[c][s], key=lambda o: (-STATUS_RANK[o.primary.status], -o.score, o.side, o.primary.tf)))
                        for s in syms]))
    return out


def format_pinned(opps: Sequence[Opportunity], now_epoch: Optional[int] = None) -> List[str]:
    """Structured global message. Returns a list of messages (normally one), each under the Telegram limit.
    Splits happen between assets; the category title is repeated at the top of a continuation."""
    head = "📌 OPPORTUNITÉS QML EN COURS · %d" % len(opps)
    if not opps:
        return [head + "\nAucune opportunité active."]
    top = [head, _counts(opps)]
    if now_epoch:
        top.append("Mis à jour : " + _ts(now_epoch))
    messages: List[str] = []
    cur = "\n".join(top)
    last_cat = None
    for cat, assets in _grouped(opps):
        total = sum(len(v) for _, v in assets)
        cat_title = "━━ %s (%d) ━━" % (cat.upper(), total)
        for sym, items in assets:
            block = ["▸ " + sym] + [_opp_line(o) for o in items]
            need = "\n".join(block)
            title = "" if last_cat == cat else "\n\n" + cat_title
            if len(cur) + len(title) + 1 + len(need) > SAFE_LIMIT:
                messages.append(cur)
                cur = "📌 (suite)"
                title = "\n\n" + cat_title
            cur += title + "\n" + need
            last_cat = cat
    messages.append(cur)
    return messages
