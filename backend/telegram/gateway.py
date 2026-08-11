"""Moseisley.sh Telegram Gateway (§19-22).

The platform owns Telegram. Updates flow:
Telegram → Gateway → identity/session → (STT if voice) → context loader → agent → reply.
External agents never own the channel and never see Telegram credentials.
"""
from __future__ import annotations

import hashlib
import logging
import secrets as pysecrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audio import stt as stt_mod
from backend.audio import tts as tts_mod
from backend.core import killswitch
from backend.core.models import Budget, TelegramBinding, TelegramPairingCode, User
from backend.ledger import service as ledger
from backend.providers.registry import NoProviderAvailable
from backend.telegram.api import TelegramClient

logger = logging.getLogger("mychief.telegram")

PAIRING_TTL_MINUTES = 10
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no confusable chars

HELP_TEXT = (
    "*Moseisley.sh commands*\n"
    "/link CODE — link this Telegram to your Moseisley.sh account\n"
    "/status — goals, approvals and system state\n"
    "/agent — show or switch the active agent (/agent native)\n"
    "/voice on|off — voice replies\n"
    "/pause — pause all agents\n"
    "/resume — resume\n"
    "/spending on|off — enable/disable agent spending\n"
    "/help — this message\n\n"
    "Or just talk to me — text or voice notes."
)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def create_pairing_code(db: AsyncSession, user_id: str) -> str:
    code = "".join(pysecrets.choice(PAIRING_ALPHABET) for _ in range(6))
    db.add(TelegramPairingCode(
        user_id=user_id,
        code_hash=_hash_code(code),
        expires_at=datetime.now(UTC) + timedelta(minutes=PAIRING_TTL_MINUTES),
    ))
    await db.flush()
    return code


async def _binding_for_telegram_user(db: AsyncSession, telegram_user_id: str) -> TelegramBinding | None:
    return (
        await db.execute(select(TelegramBinding).where(TelegramBinding.telegram_user_id == telegram_user_id))
    ).scalar_one_or_none()


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


async def _try_link(db: AsyncSession, code: str, telegram_user_id: str, telegram_chat_id: str) -> str:
    now = datetime.now(UTC)
    row = (
        await db.execute(select(TelegramPairingCode).where(TelegramPairingCode.code_hash == _hash_code(code)))
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or _as_utc(row.expires_at) < now:
        return "That code is invalid or expired. Generate a new one in the Moseisley.sh dashboard."
    existing = await _binding_for_telegram_user(db, telegram_user_id)
    if existing is not None and existing.user_id != row.user_id:
        return "This Telegram account is already linked to another Moseisley.sh account."
    row.used_at = now
    if existing is None:
        by_user = (
            await db.execute(select(TelegramBinding).where(TelegramBinding.user_id == row.user_id))
        ).scalar_one_or_none()
        if by_user is not None:
            by_user.telegram_user_id = telegram_user_id
            by_user.telegram_chat_id = telegram_chat_id
        else:
            db.add(TelegramBinding(user_id=row.user_id, telegram_user_id=telegram_user_id,
                                   telegram_chat_id=telegram_chat_id))
    else:
        existing.telegram_chat_id = telegram_chat_id
    await ledger.record(db, row.user_id, "telegram_linked", actor_type="user")
    await db.flush()
    return "Linked. You can now run your business from this chat. Try /status or just tell me a goal."


async def _status_text(db: AsyncSession, user: User) -> str:
    from backend.life_kernel import world_model

    w = await world_model.snapshot(db, user.id)
    paused = await killswitch.is_on(db, user.id, killswitch.PAUSE_ALL_AGENTS)
    lines = ["*Moseisley.sh status*"]
    lines.append(f"Goals: {len(w['goals'])} active")
    for g in w["goals"][:3]:
        lines.append(f"  • {g['title']} ({g['progress']:.0%})")
    lines.append(f"Pending approvals: {w['pending_approvals']}")
    lines.append(f"Open opportunities: {len(w['open_opportunities'])}")
    lines.append(f"System: {'PAUSED' if paused else 'active'}")
    return "\n".join(lines)


async def _handle_command(
    db: AsyncSession, text: str, telegram_user_id: str, telegram_chat_id: str,
    binding: TelegramBinding | None, user: User | None,
) -> str:
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd == "/start":
        return ("Welcome to *Moseisley.sh* — your AI crew, working for you.\n\n"
                "Link your account: open the Moseisley.sh dashboard → Connections → Telegram, "
                "then send me:\n/link YOURCODE")
    if cmd == "/link":
        if not args:
            return "Usage: /link CODE (get a code from the Moseisley.sh dashboard)"
        return await _try_link(db, args[0].upper(), telegram_user_id, telegram_chat_id)
    if cmd == "/help":
        return HELP_TEXT

    if binding is None or user is None:
        return "This chat isn't linked yet. Open the Moseisley.sh dashboard and send /link CODE."

    if cmd == "/status":
        return await _status_text(db, user)
    if cmd == "/voice":
        mode = (args[0].lower() if args else "").strip()
        if mode == "on":
            binding.voice_reply_mode = "text_and_voice"
            return "Voice replies ON."
        if mode == "off":
            binding.voice_reply_mode = "off"
            return "Voice replies OFF."
        return f"Voice reply mode: {binding.voice_reply_mode}. Use /voice on or /voice off."
    if cmd == "/pause":
        await killswitch.set_switch(db, user.id, killswitch.PAUSE_ALL_AGENTS, True)
        await ledger.record(db, user.id, "system_paused", actor_type="user")
        return "Crew paused. /resume to continue."
    if cmd == "/resume":
        await killswitch.set_switch(db, user.id, killswitch.PAUSE_ALL_AGENTS, False)
        await ledger.record(db, user.id, "system_resumed", actor_type="user")
        return "Resumed."
    if cmd == "/spending":
        mode = (args[0].lower() if args else "").strip()
        budget = (
            await db.execute(select(Budget).where(Budget.user_id == user.id, Budget.scope == "treasury"))
        ).scalar_one_or_none()
        if mode in ("on", "off"):
            if budget is None:
                budget = Budget(user_id=user.id, scope="treasury")
                db.add(budget)
            budget.spending_enabled = mode == "on"
            await ledger.record(db, user.id, "kill_switch_changed", actor_type="user",
                                payload={"spending_enabled": budget.spending_enabled})
            return f"Agent spending {'ENABLED' if budget.spending_enabled else 'DISABLED'}."
        state = "ON" if (budget and budget.spending_enabled) else "OFF"
        return f"Agent spending is {state}. Use /spending on or /spending off."
    if cmd == "/agent":
        from backend.agents import registry as agent_registry

        if args:
            try:
                agent = await agent_registry.set_active_by_name(db, user.id, args[0])
                return f"Active agent: *{agent.display_name}*."
            except agent_registry.AgentNotFound:
                return f"No agent named '{args[0]}'. Use /agent to list."
        agents = await agent_registry.list_agents(db, user.id)
        lines = ["*Your agents*"]
        for a in agents:
            marker = "●" if a.is_active else "○"
            lines.append(f"{marker} {a.display_name} ({a.adapter_type})")
        lines.append("Switch with /agent NAME")
        return "\n".join(lines)
    return "Unknown command. /help for the list."


class Gateway:
    """Processes Telegram updates. Instantiate with a TelegramClient (or fake in tests)."""

    def __init__(self, client: TelegramClient):
        self.client = client

    async def process_update(self, db: AsyncSession, update: dict) -> None:
        try:
            if "callback_query" in update:
                await self._handle_callback(db, update["callback_query"])
            elif "message" in update:
                await self._handle_message(db, update["message"])
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("telegram update processing failed")
            chat_id = (update.get("message") or {}).get("chat", {}).get("id")
            if chat_id:
                try:
                    await self.client.send_message(chat_id, "Something went wrong processing that. Try again.")
                except Exception:
                    logger.exception("failed to send telegram error reply")

    async def _resolve(self, db: AsyncSession, message: dict) -> tuple[TelegramBinding | None, User | None]:
        telegram_user_id = str(message.get("from", {}).get("id", ""))
        binding = await _binding_for_telegram_user(db, telegram_user_id)
        if binding is None:
            return None, None
        user = (await db.execute(select(User).where(User.id == binding.user_id))).scalar_one_or_none()
        return binding, user

    async def _handle_message(self, db: AsyncSession, message: dict) -> None:
        chat_id = message.get("chat", {}).get("id")
        telegram_user_id = str(message.get("from", {}).get("id", ""))
        if not chat_id or not telegram_user_id:
            return
        binding, user = await self._resolve(db, message)
        text = message.get("text", "")

        if text.startswith("/"):
            reply = await _handle_command(db, text, telegram_user_id, str(chat_id), binding, user)
            await self.client.send_message(chat_id, reply)
            return

        if binding is None or user is None:
            await self.client.send_message(
                chat_id, "This chat isn't linked yet. Open the Moseisley.sh dashboard and send /link CODE."
            )
            return

        if "voice" in message or "audio" in message:
            text = await self._transcribe_voice(db, user, message)
            if text is None:
                await self.client.send_message(
                    chat_id, "I couldn't transcribe that voice note (no STT provider configured)."
                )
                return

        if not text:
            return

        if await killswitch.is_on(db, user.id, killswitch.PAUSE_ALL_AGENTS):
            await self.client.send_message(chat_id, "Your crew is paused. /resume to continue.")
            return

        from backend.agents import router as agent_router

        reply = await agent_router.route_message(db, user, text, channel="telegram")
        await self._send_reply(db, binding, chat_id, reply)

    async def _transcribe_voice(self, db: AsyncSession, user: User, message: dict) -> str | None:
        """Voice pipeline (§22): download → STT → delete temp audio."""
        voice = message.get("voice") or message.get("audio") or {}
        file_id = voice.get("file_id")
        if not file_id:
            return None
        try:
            provider = await stt_mod.resolve_stt(db, user.id)
        except (NoProviderAvailable, stt_mod.SttError):
            return None
        file_info = await self.client.get_file(file_id)
        audio = await self.client.download_file(file_info.get("file_path", ""))
        try:
            transcript = await provider.transcribe(audio, filename="voice.ogg")
        finally:
            del audio  # temporary audio is not persisted anywhere
        return transcript.strip() or None

    async def _send_reply(self, db: AsyncSession, binding: TelegramBinding, chat_id, reply: str) -> None:
        mode = binding.voice_reply_mode
        if mode in ("voice_only", "text_and_voice"):
            try:
                tts = await tts_mod.resolve_tts(db, binding.user_id)
                audio = await tts.synthesize(reply)
                await self.client.send_voice(chat_id, audio)
                if mode == "voice_only":
                    return
            except Exception:
                logger.warning("tts failed; falling back to text")
        await self.client.send_message(chat_id, reply)

    async def _handle_callback(self, db: AsyncSession, callback: dict) -> None:
        """Inline-button callbacks — used by Treasury approvals (appr:<id>:approve|deny)."""
        data = callback.get("data", "")
        callback_id = callback.get("id", "")
        telegram_user_id = str(callback.get("from", {}).get("id", ""))
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        binding = await _binding_for_telegram_user(db, telegram_user_id)
        if binding is None:
            await self.client.answer_callback_query(callback_id, "Not linked.")
            return
        if data.startswith("appr:"):
            from backend.treasury import approvals

            _, approval_id, action = data.split(":", 2)
            try:
                result = await approvals.resolve(
                    db, binding.user_id, approval_id,
                    approve=(action == "approve"), channel="telegram",
                )
                await self.client.answer_callback_query(callback_id, "Done.")
                if chat_id:
                    await self.client.send_message(chat_id, result)
            except approvals.ApprovalError as e:
                await self.client.answer_callback_query(callback_id, str(e))
        elif data.startswith("dev:"):
            # Dev Agent merge approvals (third pass §25): dev:<approval_id>:approve|deny
            from backend.agents import dev as dev_svc

            _, approval_id, action = data.split(":", 2)
            try:
                result = await dev_svc.resolve_approval(
                    db, binding.user_id, approval_id,
                    approve=(action == "approve"), channel="telegram",
                )
                await self.client.answer_callback_query(callback_id, "Done.")
                if chat_id:
                    await self.client.send_message(
                        chat_id, f"Dev proposal {result['status']}.")
            except dev_svc.DevError as e:
                await self.client.answer_callback_query(callback_id, str(e))
        else:
            await self.client.answer_callback_query(callback_id)
