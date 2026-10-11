"""How the robot learns from Harold.

Raw material (all kept in the saved state):
  state["feedback"]   every deletion, adjustment, restore, manual proposal and applied suggestion (with the full setup snapshot)
  state["ledger"]     features of every opportunity that was notified (to compare « removed » with « kept »)
  state["manual"]     setups Harold proposed, with the diagnosis of why the robot missed them
Nothing is ever changed behind his back: study() builds NUMBERED SUGGESTIONS, and only « applique N » activates one.
Active changes live in state["params"] ({"global": {...detector parameters...}, "filters": {feature: [op, value]}}).
"""
import math
import re
from collections import Counter
from statistics import mean, median
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .engine import Setup
from .qml_core import Params

LEDGER_MAX = 2000
FEEDBACK_MAX = 500
PARAM_TYPES = {"strict": bool, "req_mom": bool, "mom_bars": int, "midzone": bool, "mode": str, "pd_full": bool, "ext": int,
               "near_atr": float, "mar_ratio": float, "mar_atr": float, "body_break": bool, "body_min": float, "ob_body": bool,
               "body_struct": bool, "body_struct_min": float}
PARAM_LABEL = {"strict": "structure précédente claire exigée", "req_mom": "forte dynamique exigée", "mom_bars": "bougies pour la dynamique",
               "midzone": "zones MPL en Midzone", "mode": "type de zone (mpl / ob / qmlz / both / all)", "pd_full": "OB entièrement en premium/discount",
               "ext": "longueur des pivots", "near_atr": "proximité zone / ligne QML (ATR)", "mar_ratio": "corps minimum du marubozu",
               "mar_atr": "taille minimum du marubozu (ATR)", "body_break": "cassure de la ligne QML par le corps",
               "body_min": "dépassement minimum de la clôture (ATR)", "ob_body": "OB : corps seulement",
               "body_struct": "cassure de structure (LL/HH) par le corps", "body_struct_min": "dépassement minimum de la clôture sur le support (ATR)"}
FEATURES = ("head_excess_atr", "leg_atr", "zone_h_atr", "disp_atr")
FEATURE_LABEL = {"head_excess_atr": "dépassement de la tête sur la ligne QML (ATR)", "leg_atr": "amplitude HH–LL (ATR)",
                 "zone_h_atr": "hauteur de la zone (ATR)", "disp_atr": "force de la bougie de déplacement (ATR)"}
_STOP = set("le la les un une des de du d l et en au aux ce cet cette ces est a pas ne n qui que qu il elle on je tu y se sur sous dans par pour "
            "avec sans mais ou donc car c s t m j plus tres trop peu au".split())


def _ensure(state: Dict) -> None:
    for k, v in (("feedback", []), ("ledger", []), ("manual", []), ("params", {"global": {}, "filters": {}}), ("suggestions", [])):
        state.setdefault(k, v)
    state["params"].setdefault("global", {})
    state["params"].setdefault("filters", {})


# ------------------------------------------------------------------------------------------------ features
def features_from_values(atr: float, head: float, shoulder: float, leg: float, zone_low: float, zone_high: float, disp: float) -> Dict[str, float]:
    if atr <= 0:
        return {f: 0.0 for f in FEATURES}
    return {"head_excess_atr": abs(head - shoulder) / atr, "leg_atr": leg / atr, "zone_h_atr": (zone_high - zone_low) / atr,
            "disp_atr": disp / atr}


def features(s: Setup) -> Dict[str, float]:
    return features_from_values(s.atr, s.head, s.shoulder, s.leg, s.zone_low, s.zone_high, s.disp)


def features_from_dict(d: Dict) -> Dict[str, float]:
    return features_from_values(d["atr"], d["head"], d["shoulder"], d["leg"], d["zone_low"], d["zone_high"], d["disp"])


# ------------------------------------------------------------------------------------------------ params store
def params_from_state(state: Dict) -> Params:
    p = Params()
    for k, v in state.get("params", {}).get("global", {}).items():
        if k in PARAM_TYPES:
            setattr(p, k, v)
    return p


def apply_filters(setups: Sequence[Setup], filters: Dict) -> List[Setup]:
    if not filters:
        return list(setups)
    out = []
    for s in setups:
        if s.zone_kind == "MANUEL":
            out.append(s)
            continue
        f = features(s)
        ok = True
        for name, (op, val) in filters.items():
            if name in f and not (f[name] >= val if op == ">=" else f[name] <= val):
                ok = False
                break
        if ok:
            out.append(s)
    return out


def record_new(state: Dict, opp, now: int) -> None:
    _ensure(state)
    s = opp.primary
    rec = {"id": opp.id, "t": int(now), "symbol": s.symbol, "category": config.category_of(s.symbol), "side": s.side, "tf": s.tf,
           "type": s.qml_type, "kind": s.zone_kind, "manual": s.zone_kind == "MANUEL"}
    rec.update(features(s))
    state["ledger"].append(rec)
    del state["ledger"][:-LEDGER_MAX]


# ------------------------------------------------------------------------------------------------ study
def _best_rule(dis: List[float], keep: List[float]) -> Optional[Dict]:
    """Best single-threshold filter separating removed (dis) from kept opportunities.
    Thresholds sit halfway between two neighbouring observed values (never exactly on a data point)."""
    vals = sorted(set(dis) | set(keep))
    if len(vals) < 2:
        return None
    cands = [(a + b) / 2 for a, b in zip(vals, vals[1:])]
    best = None
    for op in (">=", "<="):
        for t in cands:
            if op == ">=":
                removed_d = sum(1 for v in dis if v < t) / len(dis)
                removed_k = sum(1 for v in keep if v < t) / len(keep)
            else:
                removed_d = sum(1 for v in dis if v > t) / len(dis)
                removed_k = sum(1 for v in keep if v > t) / len(keep)
            gain = removed_d - removed_k
            if best is None or gain > best["gain"] + 1e-12:
                best = {"op": op, "value": t, "removed_dismissed": removed_d, "removed_kept": removed_k, "gain": gain}
    return best


def _effect(a: List[float], b: List[float]) -> float:
    """Standardised difference of means (0 = same distribution)."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va = sum((x - mean(a)) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mean(b)) ** 2 for x in b) / (len(b) - 1)
    sd = math.sqrt((va + vb) / 2)
    return abs(mean(a) - mean(b)) / sd if sd > 1e-12 else (0.0 if abs(mean(a) - mean(b)) < 1e-12 else 99.0)


def _words(texts: Iterable[str]) -> List[Tuple[str, int]]:
    c: Counter = Counter()
    for t in texts:
        for w in re.findall(r"[a-zàâçéèêëîïôûùüÿœ']{4,}", (t or "").lower()):
            if w not in _STOP:
                c[w] += 1
    return c.most_common(6)


def _shift_stats(vals: List[float]) -> Optional[Dict]:
    if not vals:
        return None
    return {"n": len(vals), "mean": mean(vals), "median": median(vals),
            "same_sign": max(sum(1 for v in vals if v > 0), sum(1 for v in vals if v < 0)) / len(vals)}


def study(state: Dict, min_n: int = 8, min_mod: int = 5, min_missed: int = 3, history: Optional[Dict] = None,
          params: Optional[Params] = None) -> Dict:
    """Study of everything Harold removed, adjusted or proposed. Returns a dict (also rebuilds state['suggestions'])."""
    _ensure(state)
    fb = state["feedback"]
    dismissed = [f for f in fb if f["type"] == "dismiss"]
    restored = {f["id"] for f in fb if f["type"] == "restore"}
    modified = [f for f in fb if f["type"] == "modify"]
    manual = [m for m in state["manual"]]
    out: Dict = {"n_dismissed": len(dismissed), "n_restored": len(restored), "n_modified": len(modified), "n_manual": len(manual),
                 "n_ledger": len(state["ledger"])}
    sugg: List[Dict] = []

    # --- dismissed: where and why
    groups = {}
    for name, fn in (("timeframe", lambda r: r["tf"]), ("catégorie", lambda r: r["category"]), ("type de zone", lambda r: r["kind"]),
                     ("type de QML", lambda r: r["type"]), ("sens", lambda r: r["side"])):
        tot = Counter(fn(r) for r in state["ledger"] if not r.get("manual"))
        dis_ids = {d["id"] for d in dismissed}
        rem = Counter(fn(r) for r in state["ledger"] if r["id"] in dis_ids and not r.get("manual"))
        rows = [(k, rem[k], tot[k]) for k in tot if tot[k] >= 5 and rem[k] > 0]
        rows.sort(key=lambda x: -x[1] / x[2])
        if rows:
            groups[name] = rows[:3]
    out["removal_by_group"] = groups
    out["reasons"] = _words(d.get("reason", "") for d in dismissed)

    # --- dismissed vs kept: features
    dis_ids = {d["id"] for d in dismissed} | {m["id"] for m in modified}
    keep = [r for r in state["ledger"] if r["id"] not in dis_ids and not r.get("manual")]
    dis_f = [features_from_dict(d["setup"]) for d in dismissed if d.get("setup")]
    feat = {}
    if len(dis_f) >= min_n and len(keep) >= min_n:
        for f in FEATURES:
            dv, kv = [x[f] for x in dis_f], [r[f] for r in keep]
            rule = _best_rule(dv, kv)
            eff = _effect(dv, kv)
            feat[f] = {"mean_dismissed": mean(dv), "mean_kept": mean(kv), "rule": rule, "effect": eff}
            if rule and eff >= 0.8 and rule["gain"] >= 0.4 and rule["removed_dismissed"] >= 0.5 and rule["removed_kept"] <= 0.25:
                v = round(rule["value"], 2)
                sugg.append({"kind": "filter", "key": f, "op": rule["op"], "value": v,
                             "text": "Garder seulement les opportunités avec %s %s %.2f (aurait retiré %d %% de tes suppressions et %d %% de celles que tu as gardées)"
                             % (FEATURE_LABEL[f], rule["op"], v, round(100 * rule["removed_dismissed"]), round(100 * rule["removed_kept"]))})
    out["features"] = feat
    out["features_enough"] = len(dis_f) >= min_n and len(keep) >= min_n
    out["features_need"] = {"dismissed": (len(dis_f), min_n), "kept": (len(keep), min_n)}

    # --- modified: how he corrects the zones
    prox, dist_, ent = [], [], []
    ob_body_votes = []
    for m in modified:
        atr = m["atr"]
        if atr <= 0:
            continue
        d = 1 if m["side"] == "sell" else -1
        o, n = m["orig"], m["new"]
        po, do = (o["zone_low"], o["zone_high"]) if d == 1 else (o["zone_high"], o["zone_low"])
        pn, dn = (n["zone_low"], n["zone_high"]) if d == 1 else (n["zone_high"], n["zone_low"])
        prox.append(d * (pn - po) / atr)
        dist_.append(d * (dn - do) / atr)
        ent.append(d * (n["entry"] - o["entry"]) / atr)
        if m.get("kind") == "OB" and len(m.get("zone_candle", [])) == 4:
            co, ch, cl, cc = m["zone_candle"]
            wick, body = (ch, max(co, cc)) if d == 1 else (cl, min(co, cc))
            ob_body_votes.append(abs(dn - body) < abs(dn - wick))
    out["modified"] = {"n": len(modified), "near_edge": _shift_stats(prox), "far_edge": _shift_stats(dist_), "line": _shift_stats(ent),
                       "ob_body_share": (sum(ob_body_votes) / len(ob_body_votes)) if ob_body_votes else None, "ob_n": len(ob_body_votes)}
    p = params or Params()
    if len(ob_body_votes) >= min_mod and sum(ob_body_votes) / len(ob_body_votes) >= 0.7 and not p.ob_body:
        sugg.append({"kind": "param", "key": "ob_body", "value": True,
                     "text": "Zones OB : utiliser le corps de la bougie plutôt que sa mèche (ton bord lointain est proche du corps dans %d %% de tes %d ajustements d'OB)"
                     % (round(100 * sum(ob_body_votes) / len(ob_body_votes)), len(ob_body_votes))})
    for name, vals in (("bord proche", prox), ("bord lointain", dist_), ("ligne QML", ent)):
        st = _shift_stats(vals)
        if st and st["n"] >= min_mod and abs(st["mean"]) >= 0.15 and st["same_sign"] >= 0.7:
            out.setdefault("zone_bias", []).append("Tu déplaces systématiquement le %s %s de %.2f ATR en moyenne (%d %% des cas, %d ajustements)."
                                                   % (name, "vers l'extérieur" if st["mean"] > 0 else "vers l'intérieur", abs(st["mean"]),
                                                      round(100 * st["same_sign"]), st["n"]))

    # --- manual proposals: what the robot misses
    diag = [m["diag"] for m in manual if m.get("diag")]
    matched = [d for d in diag if d.get("matched")]
    missed = [d for d in diag if not d.get("matched") and not d.get("error")]
    block = Counter()
    for d in missed:
        for b in d.get("blocking", []):
            block[(b["key"], b["value"])] += 1
        for pr in d.get("pairs", []):
            for k, v in zip(pr["keys"], pr["values"]):
                block[(k, v)] += 1
    ext_votes = Counter(e for d in missed for e in d.get("ext", []))
    out["manual"] = {"n": len(manual), "matched": len(matched), "missed": len(missed),
                     "blocking": [(k, v, c) for (k, v), c in block.most_common()], "ext": ext_votes.most_common(3),
                     "zone_delta": _shift_stats([d["delta"]["zone_high"] - d["delta"]["zone_low"] for d in matched if d.get("delta")])}
    if len(missed) >= min_missed:
        for (k, v), c in block.most_common():
            if c / len(missed) >= 0.6 and getattr(p, k, None) != v:
                sugg.append({"kind": "param", "key": k, "value": v,
                             "text": "Assouplir « %s » : %d de tes %d setups manqués seraient devenus visibles" % (PARAM_LABEL.get(k, k), c, len(missed))})
        if ext_votes:
            e, c = ext_votes.most_common(1)[0]
            if c / len(missed) >= 0.6 and e != p.ext:
                sugg.append({"kind": "param", "key": "ext", "value": e,
                             "text": "Longueur des pivots %d : %d de tes %d setups manqués apparaîtraient" % (e, c, len(missed))})

    if history:
        for sg in sugg:
            sg["impact"] = _impact(history, p, sg)
            if sg["impact"] is not None:
                sg["text"] += " · effet sur les données actuelles : %+d %% d'opportunités" % round(100 * sg["impact"])
    for i, sg in enumerate(sugg, 1):
        sg["n"] = i
    state["suggestions"] = sugg
    out["suggestions"] = sugg
    return out


def _impact(history: Dict, base: Params, sg: Dict) -> Optional[float]:
    """Relative change in the number of active setups if the suggestion were applied (current data)."""
    from .scanner import scan_symbol          # local import: scanner imports this module
    syms = sorted(history)[:40]
    changed = Params()
    changed.__dict__.update(base.__dict__)
    filt = {}
    if sg["kind"] == "param":
        setattr(changed, sg["key"], sg["value"])
    else:
        filt = {sg["key"]: [sg["op"], sg["value"]]}
    n0 = n1 = 0
    pbt0 = {tf: base for tf in config.TIMEFRAMES}
    pbt1 = {tf: changed for tf in config.TIMEFRAMES}
    for s in syms:
        a = [x for x in scan_symbol(s, history[s], pbt0) if x.active]
        b = apply_filters([x for x in scan_symbol(s, history[s], pbt1) if x.active], filt)
        n0 += len(a)
        n1 += len(b)
    return None if n0 == 0 else (n1 - n0) / n0


# ------------------------------------------------------------------------------------------------ apply / show / reset
def apply_suggestion(state: Dict, n: int, now: int) -> Tuple[bool, str]:
    _ensure(state)
    sg = next((s for s in state["suggestions"] if s["n"] == n), None)
    if sg is None:
        return False, "Je n'ai pas de suggestion n°%d. Dis « bilan » pour voir la liste à jour." % n
    if sg["kind"] == "param":
        typ = PARAM_TYPES[sg["key"]]
        val = sg["value"]
        if not isinstance(val, typ) and not (typ is float and isinstance(val, int)):
            return False, "Valeur invalide pour %s." % sg["key"]
        state["params"]["global"][sg["key"]] = typ(val)
        what = "%s = %s" % (PARAM_LABEL.get(sg["key"], sg["key"]), val)
    else:
        state["params"]["filters"][sg["key"]] = [sg["op"], sg["value"]]
        what = "%s %s %.2f" % (FEATURE_LABEL[sg["key"]], sg["op"], sg["value"])
    state["feedback"].append({"type": "apply", "t": int(now), "suggestion": {k: sg[k] for k in ("kind", "key", "value") if k in sg}, "text": sg["text"]})
    del state["feedback"][:-FEEDBACK_MAX]
    return True, "✅ Réglage activé : %s.\nIl s'applique dès le prochain scan (dis « réglages » pour voir tout ce qui est actif, « réinitialise les réglages » pour tout annuler)." % what


def show_params(state: Dict) -> str:
    _ensure(state)
    g, f = state["params"]["global"], state["params"]["filters"]
    if not g and not f:
        return "Aucun réglage appris n'est actif : le robot utilise les règles d'origine."
    lines = ["⚙️ Réglages actifs (appris avec toi) :"]
    for k, v in sorted(g.items()):
        lines.append("• %s : %s" % (PARAM_LABEL.get(k, k), v))
    for k, (op, v) in sorted(f.items()):
        lines.append("• filtre : %s %s %.2f" % (FEATURE_LABEL.get(k, k), op, v))
    return "\n".join(lines)


def reset_params(state: Dict, now: int) -> str:
    _ensure(state)
    state["params"] = {"global": {}, "filters": {}}
    state["feedback"].append({"type": "reset", "t": int(now)})
    return "↩️ Réglages réinitialisés : le robot repart des règles d'origine."


# ------------------------------------------------------------------------------------------------ report
def format_study(st: Dict) -> str:
    L = ["🧠 CE QUE J'AI APPRIS DE TOI",
         "Suppressions : %d · Ajustements : %d · Setups proposés : %d · Opportunités suivies : %d" % (st["n_dismissed"], st["n_modified"], st["n_manual"], st["n_ledger"])]
    if not (st["n_dismissed"] or st["n_modified"] or st["n_manual"]):
        L.append("Pas encore de données : supprime une opportunité, ajuste une zone ou propose un setup, et j'analyserai.")
        return "\n".join(L)
    if st["reasons"]:
        L.append("Mots qui reviennent dans tes raisons : " + ", ".join("%s (%d)" % w for w in st["reasons"]))
    for name, rows in st["removal_by_group"].items():
        L.append("Tu retires surtout par %s : %s" % (name, ", ".join("%s (%d/%d)" % r for r in rows)))
    if st["n_dismissed"]:
        if st["features_enough"]:
            for f, d in st["features"].items():
                L.append("• %s : supprimées %.2f en moyenne, gardées %.2f" % (FEATURE_LABEL[f], d["mean_dismissed"], d["mean_kept"]))
        else:
            nd, md = st["features_need"]["dismissed"]
            nk, mk = st["features_need"]["kept"]
            L.append("Comparaison supprimées / gardées : pas assez de données (%d/%d supprimées, %d/%d gardées)." % (nd, md, nk, mk))
    m = st["modified"]
    if m["n"]:
        L.append("Ajustements de zone : %d" % m["n"])
        for line in st.get("zone_bias", []):
            L.append("• " + line)
        if m["ob_n"]:
            L.append("• Sur les OB, ton bord lointain est plus proche du corps que de la mèche dans %d %% des cas (%d)." % (round(100 * m["ob_body_share"]), m["ob_n"]))
    mm = st["manual"]
    if mm["n"]:
        L.append("Tes setups proposés : %d (vus par le robot : %d, manqués : %d)" % (mm["n"], mm["matched"], mm["missed"]))
        for k, v, c in mm["blocking"][:4]:
            L.append("• règle « %s » à %s : aurait rendu visible %d setup(s) manqué(s)" % (PARAM_LABEL.get(k, k), v, c))
    if st["suggestions"]:
        L.append("")
        L.append("💡 SUGGESTIONS (rien n'est changé sans toi) :")
        for sg in st["suggestions"]:
            L.append("%d. %s" % (sg["n"], sg["text"]))
        L.append("Dis « applique 1 » (ou 2, 3…) pour activer une suggestion.")
    else:
        L.append("")
        L.append("Pas encore de suggestion fiable : il faut plus de cas pour être sûr.")
    return "\n".join(L)
