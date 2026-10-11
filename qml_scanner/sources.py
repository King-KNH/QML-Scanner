"""Market-data sources.

DerivClient   public WebSocket (no login): active_symbols + candles. Several endpoints are tried in order, the first that
              answers wins (the new public endpoint, then the legacy ones). The transport is injectable (tests use a fake).
TwelveClient  fallback for forex / metals / crypto only (Deriv synthetic indices exist nowhere else). Quota-protected.
resolve_symbols()  maps the names Harold uses (AUDJPY, XAUUSD, Volatility 10 (1s) Index...) to Deriv symbol codes.
Everything here talks to the network only through the injected transport / http object.
"""
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

Candle = Tuple[int, float, float, float, float]

DEFAULT_DERIV_URLS = [
    "wss://api.derivws.com/trading/v1/options/ws/public",      # new public endpoint (no app id / token)
    "wss://ws.derivws.com/websockets/v3?app_id={legacy_id}",   # legacy
    "wss://ws.binaryws.com/websockets/v3?app_id={legacy_id}",  # legacy, older host name
]


class DerivError(Exception):
    def __init__(self, code: str, message: str, raw: Optional[Dict] = None):
        super().__init__("%s: %s" % (code, message))
        self.code, self.message, self.raw = code, message, raw or {}


class QuotaExceeded(Exception):
    pass


def deriv_urls(app_id: Optional[str] = None, urls: Optional[Sequence[str]] = None) -> List[str]:
    """Endpoint list. The legacy hosts need a NUMERIC app id; the new alphanumeric one is only kept for the new API."""
    legacy = app_id if (app_id and app_id.strip().isdigit()) else "1089"
    return [u.format(legacy_id=legacy) for u in (urls or DEFAULT_DERIV_URLS)]


class WSTransport:
    """Thin wrapper around websocket-client (imported lazily so the package works without it in tests)."""

    def __init__(self, url: str, timeout: float = 20.0):
        import websocket                                           # pip install websocket-client
        self.ws = websocket.create_connection(url, timeout=timeout, origin="https://developers.deriv.com")

    def send(self, text: str) -> None:
        self.ws.send(text)

    def recv(self) -> str:
        return self.ws.recv()

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


class DerivClient:
    def __init__(self, urls: Sequence[str], transport_factory: Callable = WSTransport, pace: float = 0.15,
                 sleep: Callable = time.sleep, timeout: float = 20.0):
        self.urls, self.factory, self.pace, self.sleep, self.timeout = list(urls), transport_factory, pace, sleep, timeout
        self.t = None
        self.url: Optional[str] = None
        self.errors: Dict[str, str] = {}
        self._req = 0

    # ---- connection
    def connect(self) -> str:
        for url in self.urls:
            try:
                t = self.factory(url, self.timeout)
                self.t, self.url = t, url
                self._send_recv({"ping": 1})
                return url
            except Exception as e:                      # keep why each endpoint failed (diagnosis)
                self.errors[url] = "%s: %s" % (type(e).__name__, str(e)[:200])
                if self.t is not None:
                    try:
                        self.t.close()
                    except Exception:
                        pass
                self.t, self.url = None, None
        raise DerivError("NoEndpoint", "aucun point d'accès Deriv n'a répondu : %s" % self.errors)

    def close(self) -> None:
        if self.t is not None:
            try:
                self.t.close()
            except Exception:
                pass
            self.t = None

    def _send_recv(self, payload: Dict) -> Dict:
        self._req += 1
        rid = self._req
        payload = dict(payload, req_id=rid)
        self.t.send(json.dumps(payload))
        for _ in range(200):                           # ignore unrelated frames (e.g. a late answer)
            msg = json.loads(self.t.recv())
            if msg.get("req_id") not in (None, rid):
                continue
            if msg.get("error"):
                err = msg["error"]
                raise DerivError(str(err.get("code", "Error")), str(err.get("message", "")), msg)
            return msg
        raise DerivError("NoAnswer", "pas de réponse pour la requête %d" % rid)

    def request(self, payload: Dict, retries: int = 2) -> Dict:
        if self.t is None:
            self.connect()
        last: Optional[Exception] = None
        for attempt in range(retries + 1):
            self.sleep(self.pace)
            try:
                return self._send_recv(payload)
            except DerivError as e:
                last = e
                if e.code in ("RateLimit", "RateLimitExceeded"):
                    self.sleep(2.0 * (attempt + 1))
                    continue
                raise
            except Exception as e:                      # lost connection: reconnect once and retry
                last = e
                try:
                    self.close()
                    self.connect()
                except Exception as e2:
                    raise DerivError("Connection", str(e2))
        raise last if last else DerivError("Unknown", "échec")

    # ---- data
    def active_symbols(self) -> List[Dict]:
        return self.request({"active_symbols": "brief"}).get("active_symbols", [])

    def candles(self, code: str, granularity: int, count: int = 5000, start: Optional[int] = None,
                end: object = "latest") -> List[Candle]:
        p: Dict = {"ticks_history": code, "style": "candles", "granularity": granularity, "end": end, "count": min(count, 5000)}
        if start is not None:
            p["start"] = int(start)
            p["adjust_start_time"] = 1
        msg = self.request(p)
        out = []
        for c in msg.get("candles", []):
            out.append((int(c["epoch"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])))
        return out

    def candles_back(self, code: str, granularity: int, total: int) -> List[Candle]:
        """The last `total` candles, fetched in pages of 5000 going backwards."""
        got: Dict[int, Candle] = {}
        end: object = "latest"
        while len(got) < total:
            page = self.candles(code, granularity, min(5000, total - len(got) + 1), end=end)
            if not page:
                break
            before = len(got)
            for c in page:
                got[c[0]] = c
            if len(got) == before:
                break
            end = min(c[0] for c in page) - 1
        return [got[k] for k in sorted(got)][-total:]


# ---------------------------------------------------------------------------------- symbol names
def norm_name(s: str) -> str:
    s = s.lower().replace("/", "")
    s = s.replace("gold", "xau").replace("silver", "xag")
    s = re.sub(r"\bindex\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def resolve_symbols(active: Sequence[Dict], wanted: Sequence[str]) -> Tuple[Dict[str, str], List[str], Dict[str, bool]]:
    """({wanted name: deriv code}, [names not found], {wanted name: market currently open})."""
    by = {}
    for a in active:
        for key in (a.get("underlying_symbol_name", ""), a.get("underlying_symbol", ""), a.get("display_name", ""), a.get("symbol", "")):
            k = norm_name(key)
            if k and k not in by:
                by[k] = a
    codes, missing, is_open = {}, [], {}
    for w in wanted:
        a = by.get(norm_name(w))
        if a is None:
            missing.append(w)
            continue
        codes[w] = a.get("underlying_symbol") or a["symbol"]
        is_open[w] = bool(a.get("exchange_is_open", 1)) and not a.get("is_trading_suspended", 0)
    return codes, missing, is_open


# ---------------------------------------------------------------------------------- Twelve Data
class TwelveClient:
    BASE = "https://api.twelvedata.com/time_series"
    INTERVALS = {300: "5min", 3600: "1h"}

    def __init__(self, api_key: str, http, pace: float = 7.7, budget: int = 6, sleep: Callable = time.sleep):
        self.key, self.http, self.pace, self.budget, self.sleep = api_key.strip(), http, pace, budget, sleep
        self.calls = 0

    @staticmethod
    def supports(name: str) -> bool:
        return bool(re.fullmatch(r"[A-Z]{6}", name))

    @staticmethod
    def td_symbol(name: str) -> str:
        return name[:3] + "/" + name[3:]

    def candles(self, name: str, granularity: int, count: Optional[int] = None, since: Optional[int] = None) -> List[Candle]:
        if self.calls >= self.budget:
            raise QuotaExceeded("budget Twelve Data de ce lancement atteint (%d requêtes)" % self.budget)
        if self.calls:
            self.sleep(self.pace)
        self.calls += 1
        params = {"symbol": self.td_symbol(name), "interval": self.INTERVALS[granularity], "order": "asc", "timezone": "UTC"}
        if since is not None:
            params["start_date"] = datetime.fromtimestamp(since, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            params["outputsize"] = min(count or 5000, 5000)
        r = self.http.get(self.BASE, headers={"Authorization": "apikey " + self.key}, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "error":
            raise DerivError("TwelveData", str(data.get("message")))
        out = []
        for v in data.get("values", []):
            dt = datetime.strptime(v["datetime"][:19], "%Y-%m-%d %H:%M:%S" if len(v["datetime"]) > 10 else "%Y-%m-%d")
            out.append((int(dt.replace(tzinfo=timezone.utc).timestamp()), float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])))
        return out
