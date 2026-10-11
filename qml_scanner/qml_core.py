"""QML detection core (validated).
This module is the Python reference that was compared, bar by bar, with the delivered Pine script KING_QML.pine
(identical results on scenario and random series). Do not change its logic without re-running the parity tests.
Direction d: +1 = sell QM, -1 = buy QM (buy handled by mirroring prices).
"""

NAN = float("nan")


class Params:
    def __init__(self, **kw):
        self.ext = 10          # external structure pivot length
        self.atr_len = 14
        self.mar_ratio = 0.6   # marubozu: body / range minimum
        self.mar_atr = 0.5     # marubozu: body >= mar_atr * ATR
        self.mom_bars = 1      # candles after the engulfing in which a marubozu must appear
        self.req_mom = True    # strong momentum required
        self.near_atr = 0.5    # zone must be within this many ATR of the QML (left shoulder)
        self.mode = "qmlz"     # qmlz (zone QML seule) | mpl | ob | both | all
        self.sh_ratio = 0.5    # left shoulder: the QML line must reach at least this fraction of the way from the support (L) to the head (HH); 0 = off
        self.midzone = False   # allow MPL in the middle of the HH -> LL leg (entry on the zone, not on the QML line)
        self.strict = True     # reject QM whose prior structure is neither up nor down
        self.ob_body = False   # OB zone = body only (else full candle)
        self.pd_full = True    # OB must be entirely in premium/discount (else its 50% mean threshold)
        self.body_break = True  # the QML line (left shoulder level) must be broken by a candle BODY (a close beyond), not a wick
        self.body_min = 0.0     # minimum distance of that close beyond the line, in ATR
        self.body_struct = True  # the structure break (LL for a sell, HH for a buy: break of the support) must be a CLOSE beyond it, not a wick
        self.body_struct_min = 0.0  # minimum distance of that close beyond the support, in ATR
        self.max_bars = 300    # a zone stops being tracked this many bars after detection
        for k, v in kw.items():
            if not hasattr(self, k):
                raise AttributeError(k)
            setattr(self, k, v)


class QM:
    pass


class Detector:
    def __init__(self, p):
        self.p = p
        self.o, self.h, self.l, self.c, self.atr = [], [], [], [], []
        self.tr_hist = []
        self.atr_prev = NAN
        self.sw_t, self.sw_p, self.sw_b = [], [], []   # external swings: type(+1 high/-1 low), price, bar
        self.qms = []
        self.reg = set()

    # ---- transformed accessors (d = +1 sell, -1 buy) ----
    def Hh(self, d, i):
        return self.h[i] if d == 1 else -self.l[i]

    def Ll(self, d, i):
        return self.l[i] if d == 1 else -self.h[i]

    def Oo(self, d, i):
        return d * self.o[i]

    def Cc(self, d, i):
        return d * self.c[i]

    def bear_maru(self, d, i):
        rng = self.Hh(d, i) - self.Ll(d, i)
        body = self.Oo(d, i) - self.Cc(d, i)
        return (self.Cc(d, i) < self.Oo(d, i) and rng > 0
                and body / rng >= self.p.mar_ratio and body >= self.p.mar_atr * self.atr[i])

    def engulf(self, d, j):
        if j < 1 or not self.bear_maru(d, j):
            return False
        return (self.Cc(d, j) < self.Ll(d, j - 1)
                and (self.Hh(d, j) - self.Ll(d, j)) > (self.Hh(d, j - 1) - self.Ll(d, j - 1)))

    def momentum(self, d, j):
        if not self.p.req_mom:
            return True
        last = len(self.o) - 1
        for m in range(1, self.p.mom_bars + 1):
            if j + m <= last and self.bear_maru(d, j + m):
                return True
        return False

    # ---- main loop ----
    def on_bar(self, o, h, l, c):
        n = len(self.o)
        if n == 0:
            tr = h - l
        else:
            pc = self.c[n - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
        self.tr_hist.append(tr)
        L = self.p.atr_len
        if n < L - 1:
            a = NAN
        elif n == L - 1:
            a = sum(self.tr_hist[:L]) / L
        else:
            a = (tr + (L - 1) * self.atr_prev) / L
        self.atr_prev = a
        self.o.append(o); self.h.append(h); self.l.append(l); self.c.append(c)
        self.atr.append(0.0 if a != a else a)

        # 1) live update of zones created on earlier bars
        for q in self.qms:
            self.update(q, n)

        # 2) external pivots (confirmed ext bars later) and zigzag
        E = self.p.ext
        p = n - E
        if p >= E:
            ph = all(self.h[p] > self.h[k] for k in range(p - E, p)) and \
                 all(self.h[p] >= self.h[k] for k in range(p + 1, p + E + 1))
            pl = all(self.l[p] < self.l[k] for k in range(p - E, p)) and \
                 all(self.l[p] <= self.l[k] for k in range(p + 1, p + E + 1))
            t = 0
            if ph and pl:
                t = -1 if (self.sw_t and self.sw_t[-1] == 1) else 1
            elif ph:
                t = 1
            elif pl:
                t = -1
            if t != 0:
                price = self.h[p] if t == 1 else self.l[p]
                self.add_swing(t, price, p)
        if self.sw_t:                               # evaluated on every closed bar: the body-break rules can be met later
            self.detect(-self.sw_t[-1], n)

    def add_swing(self, t, price, bar):
        if not self.sw_t:
            self.sw_t.append(t); self.sw_p.append(price); self.sw_b.append(bar)
            return True
        if self.sw_t[-1] == t:
            if (t == 1 and price > self.sw_p[-1]) or (t == -1 and price < self.sw_p[-1]):
                self.sw_p[-1] = price
                self.sw_b[-1] = bar
                return True
            return False
        self.sw_t.append(t); self.sw_p.append(price); self.sw_b.append(bar)
        return True

    # ---- QM detection on the last swings ----
    def detect(self, d, now):
        n = len(self.sw_t)
        if n < 6:
            return
        e, c, b, a = n - 1, n - 2, n - 3, n - 4
        pv = lambda i: d * self.sw_p[i]
        if self.sw_t[e] != -d:
            return
        key = (d, self.sw_b[a])
        if key in self.reg:
            return
        if not (pv(c) > pv(a) and pv(e) < pv(b)):
            return
        if self.p.sh_ratio > 0 and not (pv(a) - pv(b) >= self.p.sh_ratio * (pv(c) - pv(b))):
            return
        if self.p.body_break:
            lbar, hbar = self.sw_b[b], self.sw_b[c]
            if not any(self.Cc(d, k) > pv(a) + self.p.body_min * self.atr[k] for k in range(lbar + 1, hbar + 1)):
                return
        if self.p.body_struct:
            hbar2 = self.sw_b[c]
            if not any(self.Cc(d, k) < pv(b) - self.p.body_struct_min * self.atr[k] for k in range(hbar2 + 1, now + 1)):
                return
        up = pv(a) > pv(n - 6) and pv(b) > pv(n - 5)
        dn = pv(a) < pv(n - 6) and pv(b) < pv(n - 5)
        if up:
            typ = "R"
        elif dn:
            typ = "C"
        elif self.p.strict:
            return
        else:
            typ = "R" if pv(a) > pv(n - 6) else "C"
        hb, lb, hhb, llb = self.sw_b[a], self.sw_b[b], self.sw_b[c], self.sw_b[e]
        pH, pHH, pLL = pv(a), pv(c), pv(e)
        z = None
        mpl_on = self.p.mode in ("mpl", "both", "all")
        ob_on = self.p.mode in ("ob", "both", "all")
        qz_on = self.p.mode in ("qmlz", "all")
        if mpl_on:
            z = self.zone_ls_mpl(d, hb, lb, pH, pHH, pLL)
        if z is None and ob_on:
            z = self.zone_ob(d, lb, hhb, llb, pH, pHH, pLL)
        if z is None and self.p.midzone and mpl_on:
            z = self.zone_mid(d, hhb, llb, pHH, pLL)
        if z is None and qz_on:
            z = ("QMLZ", pHH, pH, hhb, llb)       # QML zone: from the QML line (left shoulder) up to the head
        if z is None:
            return
        q = QM()
        q.d, q.typ = d, typ
        q.kind, q.tt, q.tb, q.left, q.zbar = z
        q.ent = q.tb if q.kind == "MPL-MID" else pH   # QML line = left shoulder level (except Midzone)
        q.hb, q.lb, q.hhb, q.llb = hb, lb, hhb, llb
        q.pH, q.pL, q.pHH, q.pLL = self.sw_p[a], self.sw_p[b], self.sw_p[c], self.sw_p[e]
        q.det = now
        q.touch = None
        q.inv = None
        q.done = False
        self.reg.add(key)
        self.qms.append(q)
        for k in range(llb + 1, len(self.o)):    # catch-up through the current bar
            self.update(q, k)

    def zone_ls_mpl(self, d, hb, lb, pH, pHH, pLL):
        for j in range(max(hb, 1), lb + 1):
            if not self.engulf(d, j) or not self.momentum(d, j):
                continue
            tt = max(self.Hh(d, j - 1), self.Hh(d, j))
            tb = self.Ll(d, j - 1)
            if tt < pH - self.p.near_atr * self.atr[j]:
                continue
            if tt > pHH or tb < pLL:
                continue
            return ("MPL", tt, tb, j - 1, j)
        return None

    def zone_ob(self, d, lb, hhb, llb, pH, pHH, pLL):
        """Order block of the QM leg: last bullish candle (transformed) before a bearish displacement,
        in premium (sell) / discount (buy) of the HH-LL range, fresh, and nearest to the QML line."""
        eq = (pHH + pLL) / 2
        best = None
        seen = set()
        for j in range(max(hhb, 1), llb + 1):
            if not (self.bear_maru(d, j) and self.Cc(d, j) < self.Ll(d, j - 1) and self.momentum(d, j)):
                continue
            i = -1
            for k in range(j - 1, lb - 1, -1):
                if self.Cc(d, k) > self.Oo(d, k):
                    i = k
                    break
            if i < 0 or i in seen:
                continue
            if not (self.Cc(d, j) < self.Ll(d, i)):
                continue
            seen.add(i)
            if self.p.ob_body:
                tt, tb = self.Cc(d, i), self.Oo(d, i)
            else:
                tt, tb = self.Hh(d, i), self.Ll(d, i)
            md = (tt + tb) / 2
            prem = (tb >= eq) if self.p.pd_full else (md >= eq)
            if not (prem and tt <= pHH and tb >= pLL):
                continue
            start = j + 1 + (self.p.mom_bars if self.p.req_mom else 0)
            if any(self.Hh(d, k) >= tb for k in range(start, llb + 1)):
                continue
            dist = 0.0 if (tb <= pH <= tt) else min(abs(tb - pH), abs(tt - pH))
            side = 0 if md >= pH else 1
            key = (dist, side, i)
            if best is None or key < best[0]:
                best = (key, ("OB", tt, tb, i, j))
        return None if best is None else best[1]

    def zone_mid(self, d, hhb, llb, pHH, pLL):
        for j in range(hhb + 1, llb + 1):
            if not self.engulf(d, j) or not self.momentum(d, j):
                continue
            tt = max(self.Hh(d, j - 1), self.Hh(d, j))
            tb = self.Ll(d, j - 1)
            if tt > pHH or tb < pLL:
                continue
            start = j + 1 + (self.p.mom_bars if self.p.req_mom else 0)
            if any(self.Hh(d, k) >= tb for k in range(start, llb + 1)):
                continue
            return ("MPL-MID", tt, tb, j - 1, j)
        return None

    def update(self, q, k):
        if q.done:
            return
        if k - q.det > self.p.max_bars:
            q.done = True
            return
        if q.touch is None and self.Hh(q.d, k) >= q.ent:
            q.touch = k
        if self.Cc(q.d, k) > max(q.tt, q.ent):
            q.inv = k
            q.done = True


def run(candles, **kw):
    det = Detector(Params(**kw))
    for (o, h, l, c) in candles:
        det.on_bar(o, h, l, c)
    return det
