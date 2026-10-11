import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qml_scanner.engine import Setup  # noqa: E402

T0 = (1_700_000_000 // 86400) * 86400   # a 00:00 UTC epoch


def leg(p0, p1, n):
    step = (p1 - p0) / n
    out, o = [], p0
    for i in range(n):
        c = o + step
        w = abs(step) * (0.10 + 0.013 * (i % 4))
        out.append((o, max(o, c) + w, min(o, c) - w, c))
        o = c
    return out


def crafted_ls(H):
    c1 = (H - 0.4, H, H - 0.5, H - 0.1)
    c2 = (H - 0.05, H, H - 2.2, H - 2.1)
    c3 = (H - 2.1, H - 2.0, H - 4.3, H - 4.2)
    return [c1, c2, c3], H - 4.2


def build(P0, PH, PL, H, L, HH, LL, retest):
    seq = leg(P0, PH, 8) + leg(PH, PL, 8) + leg(PL, H - 0.4, 10)
    ls, cur = crafted_ls(H)
    seq += ls
    seq += leg(cur, L, 6) + leg(L, HH, 8) + leg(HH, LL, 10) + leg(LL, retest, 8)
    return seq


def mirror(seq):
    return [(-o, -l, -h, -c) for (o, h, l, c) in seq]


def with_times(seq, sec=300, t0=T0):
    return [(t0 + i * sec, o, h, l, c) for i, (o, h, l, c) in enumerate(seq)]


def rw(seed, n=3000, start=100.0):
    r = random.Random(seed)
    out, p, vol = [], start, 0.5
    for i in range(n):
        if i % 80 == 0:
            vol = r.choice([0.3, 0.6, 1.0, 1.6])
        o = p
        c = o + r.gauss(0, vol)
        h = max(o, c) + abs(r.gauss(0, vol * 0.3))
        l = min(o, c) - abs(r.gauss(0, vol * 0.3))
        out.append((o, h, l, c))
        p = c
    return out


def make_setup(symbol="EURUSD", tf="H1", side="sell", lo=100.0, hi=101.0, entry=100.5, atr=1.0, leg_=10.0,
               disp=2.0, status="detected", t_left=1000, kind="OB", typ="QML-R"):
    return Setup(symbol=symbol, tf=tf, side=side, qml_type=typ, zone_kind=kind, entry=entry, zone_low=lo, zone_high=hi,
                 shoulder=entry, support=lo - 5, head=hi + 5, extreme=lo - 8, t_left=t_left, t_support=t_left + 1,
                 t_head=t_left + 2, t_break=t_left + 3, t_detect=t_left + 4, status=status, atr=atr, leg=leg_,
                 disp=disp, last_price=entry - 5, distance=5.0)


def build_wick(P0=100, PH=110, PL=104, L=112, LL=108, retest=118.0, head_wick=120.4, close_top=119.2, H=120.0):
    """Sell QM whose head takes the left-shoulder line (120) only with a WICK: every close stays below the line."""
    seq = leg(P0, PH, 8) + leg(PH, PL, 8) + leg(PL, H - 0.4, 10)
    ls, cur = crafted_ls(H)
    seq += ls
    seq += leg(cur, L, 6) + leg(L, H - 1.0, 7)
    seq.append((H - 1.0, head_wick, H - 1.3, close_top))
    seq += leg(close_top, LL, 10) + leg(LL, retest, 8)
    return seq


def make_history(symbols, days=90, seed0=0, start=100.0):
    return {sym: with_times(rw(seed0 + i, days * 288, start=start)) for i, sym in enumerate(symbols)}


def build_struct(late_closes=False, retest=118.0, H=120.0, L=112.0, HH=126.0, LL=108.0):
    """Sell QM whose structure break below the support L only happens with a WICK (every close stays above L),
    unless late_closes: a slow drift later closes below L without making a new swing."""
    seq = leg(100, 110, 8) + leg(110, 104, 8) + leg(104, H - 0.4, 10)
    ls, cur = crafted_ls(H)
    seq += ls
    seq += leg(cur, L, 6) + leg(L, HH, 8) + leg(HH, L + 1.5, 8)
    seq.append((L + 1.5, L + 1.7, LL, L + 0.5))
    if late_closes:
        for i in range(12):
            o = L + 0.5 - 0.1 * i
            seq.append((o, o + 0.05, o - 0.15, o - 0.1))
        seq += leg(L - 0.7, retest, 8)
    else:
        seq += leg(L + 0.5, retest, 10)
    return seq
