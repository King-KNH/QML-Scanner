"""Telegram layer (Bot API over HTTPS). `http` is any object with .post(url, json=..., timeout=...) like `requests`.

- Only messages coming from Harold's chat id are ever read; everything else is ignored.
- The pinned list is kept up to date by EDITING the same message(s) (no notification noise); new alerts are separate messages.
"""
import hashlib
from typing import Dict, List, Optional, Sequence, Tuple


class TelegramError(Exception):
    def __init__(self, description: str, code: int = 0):
        super().__init__(description)
        self.description, self.code = description, code


class NotFound(TelegramError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, http, base: str = "https://api.telegram.org"):
        self.token, self.chat_id, self.http, self.base = token.strip(), str(chat_id).strip(), http, base

    def _call(self, method: str, **params) -> object:
        r = self.http.post("%s/bot%s/%s" % (self.base, self.token, method), json=params, timeout=25)
        try:
            data = r.json()
        except Exception:
            raise TelegramError("réponse illisible (HTTP %s)" % getattr(r, "status_code", "?"))
        if not data.get("ok"):
            desc = str(data.get("description", "erreur"))
            code = int(data.get("error_code", 0) or 0)
            if "not found" in desc.lower() or "message to edit" in desc.lower() or "message can't be edited" in desc.lower():
                raise NotFound(desc, code)
            raise TelegramError(desc, code)
        return data.get("result")

    def get_me(self) -> Dict:
        return self._call("getMe")

    def send(self, text: str, silent: bool = False) -> int:
        chunks = _split(text)
        mid = 0
        for ch in chunks:
            res = self._call("sendMessage", chat_id=self.chat_id, text=ch, disable_web_page_preview=True, disable_notification=silent)
            mid = res["message_id"]
        return mid

    def edit(self, message_id: int, text: str) -> bool:
        try:
            self._call("editMessageText", chat_id=self.chat_id, message_id=message_id, text=text, disable_web_page_preview=True)
            return True
        except TelegramError as e:
            if "not modified" in e.description.lower():
                return False
            raise

    def pin(self, message_id: int) -> bool:
        try:
            self._call("pinChatMessage", chat_id=self.chat_id, message_id=message_id, disable_notification=True)
            return True
        except TelegramError:
            return False

    def delete(self, message_id: int) -> bool:
        try:
            self._call("deleteMessage", chat_id=self.chat_id, message_id=message_id)
            return True
        except TelegramError:
            return False

    def updates(self, offset: int = 0) -> List[Tuple[int, str]]:
        """[(update_id, text)] of the new text messages written by Harold. Other chats are ignored (but consumed)."""
        res = self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message"]) or []
        out = []
        for u in res:
            m = u.get("message") or {}
            if str((m.get("chat") or {}).get("id")) != self.chat_id or not m.get("text"):
                out.append((u["update_id"], ""))
                continue
            out.append((u["update_id"], m["text"]))
        return out


def _split(text: str, limit: int = 4000) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def sync_pinned(tg: Telegram, state: Dict, texts: Sequence[str]) -> Dict[str, int]:
    """Make the pinned list message(s) equal to `texts`: edit in place, create what is missing, delete what is extra."""
    st = state.setdefault("tg", {})
    ids: List[int] = st.setdefault("pinned_ids", [])
    hashes: List[str] = st.setdefault("pinned_hash", [])
    stats = {"edited": 0, "created": 0, "deleted": 0, "unchanged": 0}
    for i, text in enumerate(texts):
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if i < len(ids):
            if hashes[i] == h:
                stats["unchanged"] += 1
                continue
            try:
                tg.edit(ids[i], text)
                hashes[i] = h
                stats["edited"] += 1
                continue
            except NotFound:
                mid = tg.send(text, silent=True)
                ids[i], hashes[i] = mid, h
                stats["created"] += 1
                if i == 0:
                    tg.pin(mid)
                continue
        mid = tg.send(text, silent=True)
        ids.append(mid)
        hashes.append(h)
        stats["created"] += 1
        if i == 0:
            tg.pin(mid)
    for extra in ids[len(texts):]:
        tg.delete(extra)
        stats["deleted"] += 1
    del ids[len(texts):]
    del hashes[len(texts):]
    return stats
