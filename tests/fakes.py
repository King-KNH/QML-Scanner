import json

from helpers import rw, with_times
from qml_scanner.candles import aggregate


class FakeDeriv:
    """In-memory Deriv public WebSocket: ping, active_symbols, ticks_history (candles)."""

    def __init__(self, symbols, days=60, now=None, seed0=0, closed=(), start=100.0):
        # symbols: {display_name: code}
        self.symbols = dict(symbols)
        self.closed = set(closed)
        self.series = {}
        for i, (name, code) in enumerate(self.symbols.items()):
            m5 = with_times(rw(seed0 + i, days * 288, start=start))
            self.series[code] = {300: m5, 3600: aggregate(m5, 300, 3600)}
        last = max(s[300][-1][0] for s in self.series.values()) + 300
        self.now = now or last
        self.requests = []
        self.rate_limit_next = 0
        self.fail_next_recv = 0
        self.error_codes = {}

    def active(self):
        out = []
        for name, code in self.symbols.items():
            out.append({"symbol": code, "display_name": name, "exchange_is_open": 0 if name in self.closed else 1, "is_trading_suspended": 0})
        return out

    def handle(self, req):
        self.requests.append(req)
        rid = req.get("req_id")
        if self.rate_limit_next > 0:
            self.rate_limit_next -= 1
            return {"req_id": rid, "error": {"code": "RateLimit", "message": "too many requests"}}
        if "ping" in req:
            return {"req_id": rid, "ping": "pong", "msg_type": "ping"}
        if "active_symbols" in req:
            return {"req_id": rid, "active_symbols": self.active(), "msg_type": "active_symbols"}
        if "ticks_history" in req:
            code = req["ticks_history"]
            if code in self.error_codes:
                return {"req_id": rid, "error": {"code": self.error_codes[code], "message": "boom"}}
            if code not in self.series:
                return {"req_id": rid, "error": {"code": "InvalidSymbol", "message": "Symbol %s invalid" % code}}
            g = req["granularity"]
            data = self.series[code][g]
            # the forming candle is included, like the real API
            forming_t = self.now - self.now % g
            data = [c for c in data if c[0] <= forming_t]
            if forming_t > data[-1][0] and False:
                pass
            end = req.get("end", "latest")
            if end != "latest":
                data = [c for c in data if c[0] <= int(end)]
            if "start" in req:
                data = [c for c in data if c[0] >= int(req["start"])]
            count = min(int(req.get("count", 5000)), 5000)
            data = data[-count:]
            return {"req_id": rid, "msg_type": "candles",
                    "candles": [{"epoch": t, "open": o, "high": h, "low": l, "close": c} for (t, o, h, l, c) in data]}
        return {"req_id": rid, "error": {"code": "WrongResponse", "message": "unknown request"}}


class FakeTransport:
    def __init__(self, server):
        self.server = server
        self.q = []
        self.closed = False

    def send(self, text):
        req = json.loads(text)
        self.q.append(json.dumps(self.server.handle(req)))

    def recv(self):
        if self.server.fail_next_recv > 0:
            self.server.fail_next_recv -= 1
            raise ConnectionError("connexion perdue")
        return self.q.pop(0)

    def close(self):
        self.closed = True


def factory_for(server, bad_urls=()):
    def make(url, timeout):
        if any(b in url for b in bad_urls):
            raise ConnectionError("520 Cloudflare" if "derivws" in url else "refused")
        return FakeTransport(server)
    return make


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        if isinstance(self._d, Exception):
            raise self._d
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)


class FakeTelegramHttp:
    """In-memory Telegram Bot API for ONE chat."""

    def __init__(self, chat_id="42"):
        self.chat_id = chat_id
        self.messages = {}          # id -> text
        self.pinned = []
        self.next_id = 100
        self.inbox = []             # [{"update_id", "message": {...}}]
        self.calls = []
        self.fail = {}

    def push(self, text, chat_id=None, update_id=None):
        uid = update_id or (len(self.inbox) + 1)
        self.inbox.append({"update_id": uid, "message": {"chat": {"id": int(chat_id or self.chat_id)}, "text": text}})

    def post(self, url, json=None, timeout=None, headers=None):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))
        if method in self.fail:
            return FakeResp({"ok": False, "error_code": 400, "description": self.fail[method]})
        if method == "getMe":
            return FakeResp({"ok": True, "result": {"username": "QML_Test_Bot"}})
        if method == "sendMessage":
            self.next_id += 1
            self.messages[self.next_id] = json["text"]
            return FakeResp({"ok": True, "result": {"message_id": self.next_id}})
        if method == "editMessageText":
            mid = json["message_id"]
            if mid not in self.messages:
                return FakeResp({"ok": False, "error_code": 400, "description": "Bad Request: message to edit not found"})
            if self.messages[mid] == json["text"]:
                return FakeResp({"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"})
            self.messages[mid] = json["text"]
            return FakeResp({"ok": True, "result": True})
        if method == "pinChatMessage":
            self.pinned.append(json["message_id"])
            return FakeResp({"ok": True, "result": True})
        if method == "deleteMessage":
            self.messages.pop(json["message_id"], None)
            return FakeResp({"ok": True, "result": True})
        if method == "getUpdates":
            off = json.get("offset", 0)
            return FakeResp({"ok": True, "result": [u for u in self.inbox if u["update_id"] >= off]})
        return FakeResp({"ok": False, "error_code": 404, "description": "unknown method"})

    # Groq / Twelve Data style helpers are separate classes below


class FakeGroqHttp:
    """Scripted OpenAI-compatible endpoint. `script` = list of message dicts returned in order."""

    def __init__(self, script=None, error=None):
        self.script = list(script or [])
        self.error = error
        self.requests = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append(json)
        if self.error:
            raise self.error
        msg = self.script.pop(0) if self.script else {"role": "assistant", "content": "ok"}
        return FakeResp({"choices": [{"message": msg}]})


class FakeTwelveHttp:
    def __init__(self, values=None, error=None, status=200):
        self.values, self.error, self.status = values or [], error, status
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        if self.error:
            return FakeResp({"status": "error", "message": self.error})
        return FakeResp({"status": "ok", "values": self.values}, self.status)
