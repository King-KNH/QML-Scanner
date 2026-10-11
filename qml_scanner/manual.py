"""Setups that Harold identifies visually and gives to the robot (« ajoute XAUUSD H1 vente zone 4190 4199 ligne 4192 »).

1. add_manual()      registers the setup (symbol, timeframe, side, zone and/or QML line, optional head / support / break levels).
2. manual_setups()   turns the registered setups into Setup objects that go through the normal pipeline
                     (clustering, approach / entry / invalidation, notifications, pinned list). A manual setup always outranks
                     a robot setup of the same area.
3. diagnose()        asks: did the robot already see it? If not, WHICH rule(s) hid it? It re-runs the detection with one rule
                     relaxed at a time (then pairs) and reports the rules that make the setup appear. The answer is stored with
                     the setup: the learning study aggregates these « misses » to propose rule changes.
"""
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .candles import TF_SECONDS, build_timeframes, finest, frames_for
from .engine import Setup, scan_series
from .notify import short_id
from .qml_core import Params

FEEDBACK_MAX = 500

# (parameter, relaxed value, French explanation of the rule that was blocking)
RELAX = [
    ("body_break", False, "la cassure de la ligne QML doit se faire par le corps d'une bougie, pas par une mèche"),
    ("body_struct", False, "la cassure de structure (LL / HH) doit se faire par le corps d'une bougie, pas par une mèche"),
    ("strict", False, "la structure précédente doit être clairement haussière ou baissière"),
    ("req_mom", False, "une forte dynamique (2e bougie marubozu) est exigée"),
    ("midzone", True, "la zone MPL en Midzone n'était pas autorisée"),
    ("pd_full", False, "l'OB doit être entièrement en premium / discount"),
    ("near_atr", 2.0, "la zone doit être très proche de la ligne QML"),
    ("mar_ratio", 0.4, "le marubozu doit avoir un grand corps"),
    ("mode", "all", "le type de zone choisi était limité (MPL / OB) : la Zone QML l'aurait rendu visible"),
]
EXT_VARIANTS = [3, 5, 7, 15, 20]
PAIR_KEYS = ["body_break", "body_struct", "strict", "req_mom", "midzone", "pd_full"]


def _ensure(state: Dict) -> None:
    state.setdefault("manual", [])
    state.setdefault("feedback", [])
    state.setdefault("clusters", {})
    state.setdefault("dismissed", [])


def atr_wilder(candles: Sequence[Tuple], n: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    prev_c = None
    for (_, o, h, l, c) in candles:
        trs.append(h - l if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    if len(trs) < n:
        return sum(trs) / len(trs)
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (tr + (n - 1) * a) / n
    return a


def add_manual(state: Dict, symbol: str, tf: str, side: str, now: int, zone: Optional[Tuple[float, float]] = None,
               entry: Optional[float] = None, head: Optional[float] = None, support: Optional[float] = None,
               extreme: Optional[float] = None, note: str = "") -> Dict:
    _ensure(state)
    if zone is None and entry is None:
        raise ValueError("zone or entry line required")
    if zone is not None:
        lo, hi = min(zone), max(zone)
    else:
        lo = hi = entry
    if entry is None:
        entry = lo if side == "sell" else hi            # proximal edge: the first level price meets
    used = {c.get("id") for c in state["clusters"].values()} | {d["id"] for d in state["dismissed"]} | {m["id"] for m in state["manual"]}
    t = int(now)
    while True:
        key = "%s|%s|%s|%d" % (symbol, tf, side, t)
        ident = short_id(key)
        if ident not in used:
            break
        t += 1
    rec = {"id": ident, "key": key, "symbol": symbol, "tf": tf, "side": side, "zone_low": lo, "zone_high": hi, "entry": entry,
           "head": head, "support": support, "extreme": extreme, "t": t, "note": note, "diag": None}
    state["manual"].append(rec)
    return rec


def _status(rec: Dict, base: Sequence[Tuple], frames: Sequence[Tuple], atr: float, price: float, approach_atr: float,
            max_bars: int) -> str:
    d = 1 if rec["side"] == "sell" else -1
    entry_t = d * rec["entry"]
    top_t = max(d * rec["zone_low"], d * rec["zone_high"])
    limit = max(top_t, entry_t)
    tf_sec = TF_SECONDS[rec["tf"]]
    if any(c[0] >= rec["t"] and d * c[4] > limit for c in frames):
        return "invalid"
    if base and base[-1][0] - rec["t"] > max_bars * tf_sec:
        return "expired"
    price_t = d * price
    if price_t >= entry_t or any((c[2] if d == 1 else -c[3]) >= entry_t for c in base if c[0] >= rec["t"]):
        return "entered"
    if entry_t - price_t <= (approach_atr * atr if atr > 0 else 0.0):
        return "approaching"
    return "detected"


def manual_setups(state: Dict, history: Dict[str, Sequence[Tuple]], last_prices: Optional[Dict[str, float]] = None,
                  approach_atr: float = config.APPROACH_ATR, window: int = config.WINDOW, max_bars: int = 300) -> List[Setup]:
    out: List[Setup] = []
    for rec in state.get("manual", []):
        entry = history.get(rec["symbol"])
        base = finest(entry) if entry else []
        if not base:
            continue
        frames = frames_for(entry, rec["tf"], window)
        atr = atr_wilder(frames)
        price = (last_prices or {}).get(rec["symbol"], base[-1][4])
        entry = rec["entry"]
        head = rec["head"] if rec.get("head") is not None else entry
        support = rec["support"] if rec.get("support") is not None else entry
        extreme = rec["extreme"] if rec.get("extreme") is not None else entry
        d = 1 if rec["side"] == "sell" else -1
        status = _status(rec, base, frames, atr, price, approach_atr, max_bars)
        out.append(Setup(
            symbol=rec["symbol"], tf=rec["tf"], side=rec["side"], qml_type="MANUEL", zone_kind="MANUEL",
            entry=entry, zone_low=rec["zone_low"], zone_high=rec["zone_high"], shoulder=entry, support=support,
            head=head, extreme=extreme, t_left=rec["t"], t_support=rec["t"], t_head=rec["t"], t_break=rec["t"],
            t_detect=rec["t"], status=status, atr=atr, leg=abs(head - extreme), disp=0.0, last_price=price,
            distance=d * entry - d * price))
    return out


# ------------------------------------------------------------------------------------------------ diagnosis
def _variant(params: Params, **changes) -> Params:
    p = Params()
    p.__dict__.update(params.__dict__)
    p.__dict__.update(changes)
    return p


def _matches(s: Setup, rec: Dict, atr: float) -> bool:
    if s.side != rec["side"] or s.status in ("invalid", "expired"):
        return False
    tol = 0.5 * atr
    close_line = abs(s.entry - rec["entry"]) <= tol
    overlap = s.zone_low - tol <= rec["zone_high"] and rec["zone_low"] - tol <= s.zone_high
    if not (close_line or overlap):
        return False
    if rec.get("head") is not None and abs(s.head - rec["head"]) > 1.5 * atr:
        return False
    return True


def diagnose(rec: Dict, base: Sequence[Tuple], params: Optional[Params] = None, approach_atr: float = config.APPROACH_ATR,
             window: int = config.WINDOW, base_sec: int = config.BASE_SEC) -> Dict:
    """Did the robot see this setup? If not, which rule(s) hid it?"""
    p = params or Params()
    if isinstance(base, dict):
        frames = frames_for(base, rec["tf"], window)
    else:
        frames = build_timeframes(base, base_sec, [rec["tf"]], tail=window)[rec["tf"]]
    atr = atr_wilder(frames)
    res = {"matched": False, "blocking": [], "pairs": [], "ext": [], "atr": atr, "delta": None, "candles": len(frames)}
    if len(frames) < 40 or atr <= 0:
        res["error"] = "pas assez de bougies pour analyser"
        return res

    def found(pp: Params) -> Optional[Setup]:
        for s in scan_series(rec["symbol"], rec["tf"], frames, pp, approach_atr):
            if _matches(s, rec, atr):
                return s
        return None

    s0 = found(p)
    if s0 is not None:
        res["matched"] = True
        d = 1 if rec["side"] == "sell" else -1
        res["delta"] = {"zone_low": (s0.zone_low - rec["zone_low"]) / atr, "zone_high": (s0.zone_high - rec["zone_high"]) / atr,
                        "entry": (s0.entry - rec["entry"]) / atr, "kind": s0.zone_kind, "type": s0.qml_type, "key": s0.key}
        return res
    for key, val, label in RELAX:
        if getattr(p, key) == val:
            continue
        if found(_variant(p, **{key: val})) is not None:
            res["blocking"].append({"key": key, "value": val, "label": label})
    if not res["blocking"]:
        rel = {k: (v, l) for k, v, l in RELAX}
        keys = [k for k in PAIR_KEYS if getattr(p, k) != rel[k][0]]
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                if found(_variant(p, **{a: rel[a][0], b: rel[b][0]})) is not None:
                    res["pairs"].append({"keys": [a, b], "labels": [rel[a][1], rel[b][1]], "values": [rel[a][0], rel[b][0]]})
    for e in EXT_VARIANTS:
        if e != p.ext and found(_variant(p, ext=e)) is not None:
            res["ext"].append(e)
    return res


def describe_diag(d: Dict) -> List[str]:
    if d.get("error"):
        return ["Analyse impossible : " + d["error"] + "."]
    if d["matched"]:
        dl = d["delta"]
        return ["✅ Le robot l'avait déjà repéré (%s, %s). Écart avec ta zone : bord bas %+.2f ATR, bord haut %+.2f ATR, ligne %+.2f ATR."
                % (dl["type"], dl["kind"], dl["zone_low"], dl["zone_high"], dl["entry"])]
    lines = ["🧩 Le robot ne l'avait pas repéré."]
    if d["blocking"]:
        lines.append("Il serait apparu en assouplissant une seule règle :")
        lines += ["• " + b["label"] for b in d["blocking"]]
    elif d["pairs"]:
        lines.append("Il faut assouplir deux règles ensemble :")
        lines += ["• " + " + ".join(pp["labels"]) for pp in d["pairs"][:3]]
    elif d["ext"]:
        lines.append("Il apparaît avec une autre longueur de pivot (structure) : %s." % ", ".join(str(e) for e in d["ext"]))
    else:
        lines.append("Aucune règle seule ne l'explique : la structure que tu vois n'est pas celle que le robot lit (niveaux, sens ou timeframe).")
    lines.append("Je l'ai enregistré pour apprendre.")
    return lines
