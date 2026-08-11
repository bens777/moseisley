"""Fake Telegram client for tests — captures outbound calls, serves canned files."""
from __future__ import annotations


class FakeTelegramClient:
    def __init__(self):
        self.sent_messages: list[dict] = []
        self.sent_voices: list[dict] = []
        self.callbacks_answered: list[dict] = []
        self.files: dict[str, bytes] = {}  # file_id -> bytes

    async def send_message(self, chat_id, text, *, reply_markup=None, parse_mode=None):
        self.sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"message_id": len(self.sent_messages)}

    async def send_voice(self, chat_id, voice_bytes, caption=None):
        self.sent_voices.append({"chat_id": chat_id, "voice": voice_bytes})
        return {"message_id": len(self.sent_voices)}

    async def get_file(self, file_id):
        return {"file_id": file_id, "file_path": f"voice/{file_id}.ogg"}

    async def download_file(self, file_path):
        file_id = file_path.split("/")[-1].removesuffix(".ogg")
        return self.files.get(file_id, b"")

    async def answer_callback_query(self, callback_query_id, text=None):
        self.callbacks_answered.append({"id": callback_query_id, "text": text})

    async def set_webhook(self, url, secret_token=None):
        return {}

    async def get_updates(self, offset=None, timeout=25):
        return []


def make_text_update(telegram_user_id: int, chat_id: int, text: str, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": telegram_user_id, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def make_voice_update(telegram_user_id: int, chat_id: int, file_id: str, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": telegram_user_id, "first_name": "Test"},
            "chat": {"id": chat_id, "type": "private"},
            "voice": {"file_id": file_id, "duration": 3},
        },
    }
