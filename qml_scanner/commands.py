"""Commands Harold can type to the bot (deterministic, no AI needed). The AI layer will call the same functions as tools.

  « supprime A3F7 »                         by short id (shown in every alert and in the pinned list)
  « enlève XAUUSD H1 vente car en range »   by asset (+ optional timeframe / side), reason after "car", ":" or "-"
  « supprime tout GBPUSD »                  every matching opportunity
  « rétablis A3F7 »                         undo a dismissal
  « liste des supprimées »                  show dismissed opportunities
  « ajuste A3F7 zone 4190 4199 ligne 4192 » correct the zone and/or the QML line of an opportunity
  « ajoute XAUUSD H1 vente zone 4190 4199 ligne 4192 tête 4221 support 4165 cassure 4150 »
                                            a setup Harold saw himself (zone or line required; head/support/break optional)
  « bilan »                                 what the robot learned from his removals / adjustments / proposals
  « applique 1 »                            activate suggestion n°1 of the last study ; « réglages » / « réinitialise les réglages »

If several opportunities match, nothing is removed: the bot lists them with their ids and asks which one.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import config
from . import learning, manual, overrides
from .dismiss import dismiss_opportunity, restore
from .selection import Opportunity

SIDE_FR = {"buy": "achat", "sell": "vente"}


def strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


@dataclass
class Parsed:
    kind: str                                   # dismiss | restore | list_dismissed | unknown
    ids: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    tf: Optional[str] = None
    side: Optional[str] = None
    all: bool = False
    reason: str = ""
    levels: Dict = field(default_factory=dict)      # zone (lo, hi), entry, head, support, extreme
    numbers: List[int] = field(default_factory=list)  # for « applique 1 et 3 »


_DISMISS = re.compile(r"\b(supprim\w*|enlev\w*|retir\w*|efface\w*|elimin\w*|vir\w*|invalid\w*|delete|remove|drop|ecarte\w*|ignore\w*)\b")
_RESTORE = re.compile(r"\b(restaur\w*|retabli\w*|remets?|remettre|undo|restore)\b|\bannul\w*\b.*\bsuppress\w*|\bsuppress\w*.*\bannul\w*")
_LIST = re.compile(r"\b(liste|montre\w*|affiche\w*|voir|quelles?)\b")
_REASON_SPLIT = re.compile(r"\s(?:car|parce que|parce qu'|puisque|pcq|vu que)\s|\s[:;—–-]\s")
_TF_DIRECT = re.compile(r"\b(m15|m30|m45|h1|h2|h4|h8|d1|m5)\b")
_TF_MIN = re.compile(r"\b(15|30|45)\s?(?:m|min|mn)\b")
_TF_HOUR = re.compile(r"\b([1248])\s?h\b")
_TF_DAY = re.compile(r"\b1\s?(?:d|j)\b")
_BUY = re.compile(r"\b(achat|acheter|buy|long)\b")
_SELL = re.compile(r"\b(vente|vendre|sell|short)\b")
_ALL = re.compile(r"\b(tout|tous|toutes)\b")
_RESET = re.compile(r"\b(reinitialis\w*|remets?|remettre|retabli\w*)\b.*\b(reglages?|parametres?|regles?)\b|\breset\b")
_APPLY = re.compile(r"\bappliqu\w*\b")
_PARAMS = re.compile(r"\b(reglages?|parametres?|regles)\b")
_STUDY = re.compile(r"\b(bilan|appris|apprentissage|etudi\w*|analyse\w*)\b")
_MODIFY = re.compile(r"\b(ajust\w*|modifi\w*|corrig\w*|change\w*|decal\w*|resserr\w*|elargi\w*)\b")
_ADD = re.compile(r"\b(ajout\w*|propos\w*|repere\w*|nouvel\w*|signale\w*|cree\w*|enregistre\w*)\b")
_NUM = re.compile(r"(?<![a-z0-9.,])\d+(?:[.,]\d+)?(?![a-z0-9])")
_KEYWORDS = [("zone", re.compile(r"\bzone\b")), ("entry", re.compile(r"\b(ligne|entree|qml)\b")), ("head", re.compile(r"\b(tete|head)\b")),
             ("support", re.compile(r"\bsupport\b")), ("extreme", re.compile(r"\b(cassure|break|extreme)\b"))]


def _symbol_patterns(name: str):
    toks = [t for t in re.findall(r"[a-z0-9]+", name.lower()) if t != "index"]
    sep = r"(?:[\s()_\-/]|index)*"          # the word "index" may appear anywhere between the tokens
    pats = [sep.join(re.escape(t) for t in toks)]
    if len(toks) == 1 and re.fullmatch(r"[a-z]{6}", toks[0]):
        pats.append(re.escape(toks[0][:3]) + r"[\s/_\-]*" + re.escape(toks[0][3:]))
    return [re.compile(r"(?<![0-9a-z])" + p + r"(?![0-9a-z])") for p in pats], frozenset(toks)


def match_symbols(low: str, universe: Iterable[str]) -> List[str]:
    """Symbols named in the text. The most specific name wins (« Volatility 75 (1s) » beats « Volatility 75 »)."""
    found: List[Tuple[str, frozenset]] = []
    for name in sorted(set(universe)):
        pats, toks = _symbol_patterns(name)
        if toks and any(p.search(low) for p in pats):
            found.append((name, toks))
    return sorted(n for n, t in found if not any(t < t2 for _, t2 in found))


def _strip_symbols(low: str, names: Iterable[str]) -> str:
    for name in names:
        pats, _ = _symbol_patterns(name)
        for p in pats:
            low = p.sub(" ", low)
    return low


def extract_levels(low: str, symbol_names: Iterable[str]) -> Dict:
    """zone (2 numbers), ligne/entrée/qml, tête, support, cassure (1 number each), read from the text after the keywords."""
    t = _strip_symbols(low, symbol_names)
    t = _TF_DIRECT.sub(" ", t)
    marks = []
    for name, rx in _KEYWORDS:
        for m in rx.finditer(t):
            marks.append((m.start(), m.end(), name))
    marks.sort()
    levels: Dict = {}
    nums = lambda seg: [float(x.replace(",", ".")) for x in _NUM.findall(seg)]
    if marks:
        for i, (st, en, name) in enumerate(marks):
            seg = t[en: marks[i + 1][0] if i + 1 < len(marks) else len(t)]
            v = nums(seg)
            if name == "zone" and len(v) >= 2:
                levels["zone"] = (min(v[0], v[1]), max(v[0], v[1]))
            elif name != "zone" and v:
                levels[name] = v[0]
    else:
        v = nums(t)
        if len(v) == 2:
            levels["zone"] = (min(v), max(v))
    return levels


def parse_command(text: str, known_ids: Set[str], universe: Iterable[str]) -> Parsed:
    low = strip_accents(text).lower()
    m = _REASON_SPLIT.search(low)
    head = low[:m.start()] if m else low
    reason = low[m.end():].strip() if m else ""
    if _RESET.search(head):
        kind = "reset_params"
    elif _APPLY.search(head):
        kind = "apply"
    elif _PARAMS.search(head) and not _MODIFY.search(head):
        kind = "params"
    elif _STUDY.search(head):
        kind = "study"
    elif _RESTORE.search(head):
        kind = "restore"
    elif _LIST.search(head) and re.search(r"\bsupprim\w*|\bsuppress\w*", head):
        kind = "list_dismissed"
    elif _MODIFY.search(head):
        kind = "modify"
    elif _ADD.search(head):
        kind = "add"
    elif _DISMISS.search(head):
        kind = "dismiss"
    else:
        return Parsed("unknown")
    tokens = re.findall(r"[a-z0-9]+", head)
    ids = [t for t in tokens if t in known_ids]
    tf = None
    mt = _TF_DIRECT.search(head)
    if mt:
        tf = mt.group(1).upper()
    else:
        mm, mh, md = _TF_MIN.search(head), _TF_HOUR.search(head), _TF_DAY.search(head)
        tf = ("M" + mm.group(1)) if mm else ("H" + mh.group(1)) if mh else ("D1" if md else None)
    side = "buy" if _BUY.search(head) else "sell" if _SELL.search(head) else None
    symbols = match_symbols(head, universe)
    p = Parsed(kind, ids=ids, symbols=symbols, tf=tf, side=side, all=bool(_ALL.search(head)), reason=reason)
    if kind == "apply":
        p.numbers = [int(x) for x in re.findall(r"\b(\d{1,2})\b", head)]
    if kind in ("modify", "add"):
        head_wo_ids = " ".join(t for t in re.split(r"\s+", head) if re.sub(r"[^a-z0-9]", "", t) not in known_ids)
        p.levels = extract_levels(head_wo_ids, symbols)
    return p


def _label_opp(o: Opportunity) -> str:
    return "[%s] %s %s %s" % (o.id, o.symbol, SIDE_FR[o.side], o.primary.tf)


def _label_rec(d: Dict) -> str:
    return "[%s] %s %s %s" % (d["id"], d["symbol"], SIDE_FR[d["side"]], d["tf"])


HELP = ("Je n'ai pas compris. Exemples : « supprime A3F7 », « ajuste A3F7 zone 4190 4199 », "
        "« ajoute XAUUSD H1 vente zone 4190 4199 ligne 4192 », « bilan », « applique 1 », « réglages », « liste des supprimées ».")


def _tf_ok(o: Opportunity, tf: Optional[str]) -> bool:
    return tf is None or o.primary.tf == tf or any(c.tf == tf for c in o.confluence)


def _find_targets(p: Parsed, opps: Sequence[Opportunity], by_id: Dict[str, Opportunity]):
    if p.ids:
        return [by_id[i] for i in p.ids if i in by_id], [i.upper() for i in p.ids if i not in by_id]
    if p.symbols:
        c = [o for o in opps if o.symbol in p.symbols and _tf_ok(o, p.tf) and (p.side is None or o.side == p.side)]
        return c, []
    return None, []


def _sorted(cands):
    return sorted({o.id: o for o in cands}.values(), key=lambda o: (config.SYMBOL_ORDER.get(o.symbol, 999), o.symbol, o.side, o.primary.tf))


def _fmt(x: float) -> str:
    return ("%.5f" % x).rstrip("0").rstrip(".") if abs(x) < 100 else ("%.2f" % x).rstrip("0").rstrip(".")


def _check_levels(side: str, lv: Dict) -> Optional[str]:
    """Light sanity checks of the structure levels Harold gives (kept clean for the learning)."""
    entry = lv.get("entry")
    if entry is None and lv.get("zone"):
        entry = lv["zone"][0] if side == "sell" else lv["zone"][1]
    sgn = 1 if side == "sell" else -1
    head, sup, ext = lv.get("head"), lv.get("support"), lv.get("extreme")
    if head is not None and entry is not None and sgn * head <= sgn * entry:
        return "La tête d'un QML %s doit être %s la ligne QML." % ("vendeur" if side == "sell" else "acheteur", "au-dessus de" if side == "sell" else "en dessous de")
    if sup is not None and entry is not None and sgn * sup >= sgn * entry:
        return "Le support d'un QML %s doit être %s la ligne QML." % ("vendeur" if side == "sell" else "acheteur", "en dessous de" if side == "sell" else "au-dessus de")
    if ext is not None and sup is not None and sgn * ext >= sgn * sup:
        return "La cassure doit être %s le support." % ("sous" if side == "sell" else "au-dessus du")
    return None


def classify(text: str, state: Dict, opps: Sequence[Opportunity], symbols: Iterable[str] = ()) -> Parsed:
    """Parse a message the way execute() does (used by the agent to decide between the instant parser and the AI)."""
    by_id = {o.id.lower() for o in opps if o.id}
    dis_ids = {d["id"].lower() for d in state.get("dismissed", [])}
    universe = set(config.ALL_SYMBOLS) | {o.symbol for o in opps} | {d["symbol"] for d in state.get("dismissed", [])} | set(symbols)
    return parse_command(text, by_id | dis_ids, universe)


def needs_interpretation(p: Parsed) -> bool:
    """True when the parser recognised a verb but has no target: free text such as « vire le setup sur l'or »."""
    if p.kind == "unknown":
        return True
    if p.kind in ("dismiss", "modify", "restore"):
        return not (p.ids or p.symbols)
    if p.kind == "add":
        return not p.symbols
    return False


def execute(text: str, state: Dict, opps: Sequence[Opportunity], now: int, symbols: Iterable[str] = (),
            history: Optional[Dict] = None, last_prices: Optional[Dict] = None) -> Tuple[str, bool]:
    """Apply a command. Returns (reply text, state_changed)."""
    state.setdefault("dismissed", [])
    by_id = {o.id.lower(): o for o in opps if o.id}
    dis_by_id = {d["id"].lower(): d for d in state["dismissed"]}
    universe = set(config.ALL_SYMBOLS) | {o.symbol for o in opps} | {d["symbol"] for d in state["dismissed"]} | set(symbols)
    p = parse_command(text, set(by_id) | set(dis_by_id), universe)

    if p.kind == "unknown":
        return HELP, False

    if p.kind == "study":
        st = learning.study(state, history=history, params=learning.params_from_state(state))
        return learning.format_study(st), True
    if p.kind == "params":
        return learning.show_params(state), False
    if p.kind == "reset_params":
        return learning.reset_params(state, now), True
    if p.kind == "apply":
        if not p.numbers:
            return "Dis « applique 1 » (le numéro vient de « bilan »).", False
        msgs, changed = [], False
        for n in p.numbers:
            ok, msg = learning.apply_suggestion(state, n, now)
            msgs.append(msg)
            changed = changed or ok
        return "\n".join(msgs), changed

    if p.kind == "list_dismissed":
        ds = sorted(state["dismissed"], key=lambda d: -d["t"])[:15]
        if not ds:
            return "Aucune opportunité supprimée.", False
        lines = ["🗑️ Opportunités supprimées (%d) :" % len(state["dismissed"])]
        for d in ds:
            lines.append("• %s%s" % (_label_rec(d), (" — " + d["reason"]) if d.get("reason") else ""))
        lines.append("Pour en rétablir une : « rétablis ID ».")
        return "\n".join(lines), False

    if p.kind == "add":
        if len(p.symbols) != 1:
            return ("Dis-moi l'actif (un seul), ex. « ajoute XAUUSD H1 vente zone 4190 4199 ligne 4192 »." if not p.symbols
                    else "Un seul actif à la fois : " + ", ".join(p.symbols) + "."), False
        sym = p.symbols[0]
        if p.tf == "M5":
            return "Le M5 est abandonné (trop de bruit). Timeframes : " + ", ".join(config.TIMEFRAMES) + ".", False
        if p.tf not in config.TIMEFRAMES:
            return "Précise le timeframe parmi : " + ", ".join(config.TIMEFRAMES) + ".", False
        if p.side is None:
            return "Précise le sens : achat ou vente.", False
        lv = p.levels
        if not lv.get("zone") and lv.get("entry") is None:
            return "Donne au moins la zone (« zone 4190 4199 ») ou la ligne QML (« ligne 4192 »).", False
        err = _check_levels(p.side, lv)
        if err:
            return err + " Rien n'a été enregistré.", False
        rec = manual.add_manual(state, sym, p.tf, p.side, now, zone=lv.get("zone"), entry=lv.get("entry"), head=lv.get("head"),
                                support=lv.get("support"), extreme=lv.get("extreme"), note=p.reason)
        lines = ["✋ Setup enregistré : %s %s %s [%s]" % (sym, SIDE_FR[p.side], p.tf, rec["id"]),
                 "Zone %s – %s · ligne %s" % (_fmt(rec["zone_low"]), _fmt(rec["zone_high"]), _fmt(rec["entry"]))]
        merged = [o for o in opps if o.symbol == sym and o.side == p.side and any(
            (c.zone_low <= rec["zone_high"] and rec["zone_low"] <= c.zone_high) for c in [o.primary] + o.confluence)]
        if merged:
            lines.append("Il recoupe l'opportunité [%s] : elles seront regroupées, ta version passe en premier." % merged[0].id)
        base = (history or {}).get(sym)
        if base:
            diag = manual.diagnose(rec, base, learning.params_from_state(state))
            rec["diag"] = diag
            lines += manual.describe_diag(diag)
        else:
            lines.append("(Analyse du robot : pas de données de marché disponibles pour l'instant.)")
        state.setdefault("feedback", []).append({"type": "manual", "t": int(now), "id": rec["id"], "symbol": sym, "side": p.side, "tf": p.tf,
                                                  "zone": [rec["zone_low"], rec["zone_high"]], "entry": rec["entry"], "reason": p.reason,
                                                  "matched": bool(rec["diag"] and rec["diag"].get("matched"))})
        del state["feedback"][:-500]
        return "\n".join(lines), True

    if p.kind == "modify":
        cands, missing = _find_targets(p, opps, by_id)
        if cands is None:
            return "Dis-moi laquelle ajuster : l'ID (ex. A3F7) ou l'actif.", False
        cands = _sorted(cands)
        if not cands:
            return "Aucune opportunité en cours ne correspond.", False
        if len(cands) > 1:
            lines = ["Plusieurs opportunités correspondent, rien n'a été modifié :"] + ["• " + _label_opp(o) for o in cands]
            lines.append("Précise l'ID (ex. %s)." % cands[0].id)
            return "\n".join(lines), False
        lv = p.levels
        if not lv.get("zone") and lv.get("entry") is None:
            return "Donne la nouvelle zone (« zone 4190 4199 ») et/ou la ligne QML (« ligne 4192 »).", False
        o = cands[0]
        s0 = o.primary
        if s0.zone_kind == "MANUEL":
            rec = next((m for m in state.get("manual", []) if m["key"] == s0.key), None)
            if rec is None:
                return "Je ne retrouve pas ce setup proposé.", False
            if lv.get("zone"):
                rec["zone_low"], rec["zone_high"] = lv["zone"]
            if lv.get("entry") is not None:
                rec["entry"] = lv["entry"]
            return "✏️ Ajusté : %s zone %s – %s · ligne %s" % (_label_opp(o), _fmt(rec["zone_low"]), _fmt(rec["zone_high"]), _fmt(rec["entry"])), True
        overrides.adjust(state, s0, o.id, now, zone=lv.get("zone"), entry=lv.get("entry"))
        ov = overrides.find_override(state, s0.key)
        lines = ["✏️ Ajusté : %s" % _label_opp(o), "Zone %s – %s · ligne %s" % (_fmt(ov["zone_low"]), _fmt(ov["zone_high"]), _fmt(ov["entry"]))]
        lines.append("Avant : zone %s – %s · ligne %s" % (_fmt(ov["original"]["zone_low"]), _fmt(ov["original"]["zone_high"]), _fmt(ov["original"]["entry"])))
        if p.reason:
            lines.append("Raison notée : %s" % p.reason)
        lines.append("Je suivrai désormais l'opportunité avec tes niveaux, et j'étudie ta correction.")
        return "\n".join(lines), True

    if p.kind == "dismiss":
        cands, missing = _find_targets(p, opps, by_id)
        if cands is None:
            return "Dis-moi laquelle supprimer : l'ID (ex. A3F7) ou l'actif (ex. « XAUUSD H1 vente »).", False
        cands = _sorted(cands)
        if not cands:
            what = ", ".join(p.symbols) if p.symbols else ", ".join(missing)
            return "Aucune opportunité en cours ne correspond à %s." % what, False
        if len(cands) > 1 and not (p.all or p.ids):
            lines = ["Plusieurs opportunités correspondent, je n'ai rien supprimé :"]
            lines += ["• " + _label_opp(o) for o in cands]
            lines.append("Précise l'ID (ex. %s) ou dis « supprime tout »." % cands[0].id)
            return "\n".join(lines), False
        lines = []
        for o in cands:
            dismiss_opportunity(state, o, now, p.reason)
            lines.append("🗑️ Supprimée : " + _label_opp(o))
        if p.reason:
            lines.append("Raison notée : %s" % p.reason)
        if missing:
            lines.append("ID introuvable : %s" % ", ".join(missing))
        lines.append("Elle ne reviendra pas tant que c'est le même setup.")
        return "\n".join(lines), True

    # restore
    if p.ids:
        recs = [dis_by_id[i] for i in p.ids if i in dis_by_id]
    elif p.symbols:
        recs = [d for d in state["dismissed"] if d["symbol"] in p.symbols and (p.side is None or d["side"] == p.side)
                and (p.tf is None or d["tf"] == p.tf)]
    else:
        recs = []
    if not recs:
        return "Aucune suppression à annuler. Dis « liste des supprimées » pour voir les IDs.", False
    if len(recs) > 1 and not (p.all or p.ids):
        lines = ["Plusieurs suppressions correspondent, rien n'a été rétabli :"] + ["• " + _label_rec(d) for d in recs]
        lines.append("Précise l'ID ou dis « rétablis tout ».")
        return "\n".join(lines), False
    lines = []
    for d in list(recs):
        restore(state, d["id"], now)
        lines.append("↩️ Rétablie : " + _label_rec(d))
    lines.append("Elle réapparaîtra au prochain scan si le setup est toujours valide.")
    return "\n".join(lines), True
