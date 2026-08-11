"""Thin Telegram Bot API client (httpx). Moseisley.sh owns the gateway (§19)."""
from __future__ import annotations

import httpx


class TelegramClient:
    def __init__(self, token: str, base_url: str = "https://api.telegram.org"):
        self.token = token
        self.base_url = base_url

    def _url(self, method: str) -> str:
        return f"{self.base_url}/bot{self.token}/{method}"

    async def call(self, method: str, **params) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(self._url(method), json={k: v for k, v in params.items() if v is not None})
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {data.get('description', resp.status_code)}")
        return data["result"]

    async def send_message(self, chat_id: str | int, text: str, *,
                           reply_markup: dict | None = None, parse_mode: str | None = "Markdown") -> dict:
        try:
            return await self.call("sendMessage", chat_id=chat_id, text=text,
                                   reply_markup=reply_markup, parse_mode=parse_mode)
        except RuntimeError:
            # Markdown parse errors: retry as plain text rather than dropping the reply.
            return await self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=reply_markup)

    async def send_voice(self, chat_id: str | int, voice_bytes: bytes, caption: str | None = None) -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self._url("sendVoice"),
                data={"chat_id": str(chat_id), **({"caption": caption} if caption else {})},
                files={"voice": ("reply.ogg", voice_bytes, "audio/ogg")},
            )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram sendVoice failed: {data.get('description')}")
        return data["result"]

    async def get_file(self, file_id: str) -> dict:
        return await self.call("getFile", file_id=file_id)

    async def download_file(self, file_path: str) -> bytes:
        url = f"{self.base_url}/file/bot{self.token}/{file_path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    async def set_webhook(self, url: str, secret_token: str | None = None) -> dict:
        return await self.call("setWebhook", url=url, secret_token=secret_token)

    async def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        return await self.call("getUpdates", offset=offset, timeout=timeout)
