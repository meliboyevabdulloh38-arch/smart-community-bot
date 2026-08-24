"""
Smart Community Bot — production-minded Render test build.

This module keeps the service self-contained for the first deployment: SQLite stores
chat settings and processed update IDs, while optional AI/voice/image integrations
are exposed through server-side environment variables without hard-coded secrets.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request
from telegram import ChatPermissions, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smart-community-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me").strip()
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
AI_API_URL = os.environ.get("AI_API_URL", "").strip()
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()
DB_PATH = Path(os.environ.get("BOT_DB_PATH", "/tmp/smart-community-bot.sqlite3"))
WEBHOOK_PATH = f"/telegram-webhook/{hashlib.sha256(WEBHOOK_SECRET.encode('utf-8')).hexdigest()[:24]}"

app = FastAPI(title="Smart Community Bot")
update_lock = asyncio.Lock()
telegram_ready = False

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set. Add it in Render Environment Variables.")


class Store:
    """Small SQLite persistence layer for restart-safe bot state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    processed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'uzbek',
                    welcome_enabled INTEGER NOT NULL DEFAULT 1,
                    anti_spam_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    points INTEGER NOT NULL DEFAULT 0,
                    warnings INTEGER NOT NULL DEFAULT 0,
                    muted_until REAL NOT NULL DEFAULT 0,
                    last_message_hash TEXT NOT NULL DEFAULT '',
                    last_message_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS filters (
                    chat_id INTEGER NOT NULL,
                    phrase TEXT NOT NULL,
                    PRIMARY KEY (chat_id, phrase)
                );
                """
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def mark_update_once(self, update_id: int) -> bool:
        with self.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO processed_updates(update_id, processed_at) VALUES (?, ?)",
                    (update_id, time.time()),
                )
                conn.execute(
                    "DELETE FROM processed_updates WHERE processed_at < ?",
                    (time.time() - 7 * 86400,),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def ensure_chat(self, chat_id: int, title: str = "") -> None:
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO chats(chat_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at""",
                (chat_id, title or "", now, now),
            )

    def chat(self, chat_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone()

    def set_chat_option(self, chat_id: int, option: str, value: int) -> None:
        if option not in {"welcome_enabled", "anti_spam_enabled"}:
            raise ValueError(option)
        with self.connect() as conn:
            conn.execute(f"UPDATE chats SET {option}=?, updated_at=? WHERE chat_id=?", (value, time.time(), chat_id))

    def ensure_user(self, chat_id: int, user_id: int, name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO users(chat_id,user_id,display_name)
                   VALUES(?,?,?)
                   ON CONFLICT(chat_id,user_id) DO UPDATE SET display_name=excluded.display_name""",
                (chat_id, user_id, name or ""),
            )

    def add_points(self, chat_id: int, user_id: int, points: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET points=points+? WHERE chat_id=? AND user_id=?", (points, chat_id, user_id))

    def add_warning(self, chat_id: int, user_id: int) -> int:
        with self.connect() as conn:
            conn.execute("UPDATE users SET warnings=warnings+1 WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            row = conn.execute("SELECT warnings FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
            return int(row["warnings"] if row else 0)

    def set_muted(self, chat_id: int, user_id: int, until: float) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET muted_until=? WHERE chat_id=? AND user_id=?", (until, chat_id, user_id))

    def record_message(self, chat_id: int, user_id: int, text: str) -> tuple[str, float]:
        now = time.time()
        digest = hashlib.sha256(text.lower().strip().encode("utf-8")).hexdigest()
        with self.connect() as conn:
            row = conn.execute("SELECT last_message_hash,last_message_at FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
            conn.execute("UPDATE users SET last_message_hash=?, last_message_at=? WHERE chat_id=? AND user_id=?", (digest, now, chat_id, user_id))
            return (str(row["last_message_hash"]), float(row["last_message_at"])) if row else ("", 0.0)

    def stats(self, chat_id: int) -> tuple[int, int, int]:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS members, COALESCE(SUM(points),0) AS points, COALESCE(SUM(warnings),0) AS warnings FROM users WHERE chat_id=?", (chat_id,)).fetchone()
            return int(row["members"]), int(row["points"]), int(row["warnings"])

    def leaderboard(self, chat_id: int, limit: int = 10) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT display_name,points FROM users WHERE chat_id=? ORDER BY points DESC LIMIT ?", (chat_id, limit)).fetchall())

    def add_filter(self, chat_id: int, phrase: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO filters(chat_id,phrase) VALUES(?,?)", (chat_id, phrase.lower().strip()))

    def remove_filter(self, chat_id: int, phrase: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM filters WHERE chat_id=? AND phrase=?", (chat_id, phrase.lower().strip()))

    def filters(self, chat_id: int) -> list[str]:
        with self.connect() as conn:
            return [str(row["phrase"]) for row in conn.execute("SELECT phrase FROM filters WHERE chat_id=? ORDER BY phrase", (chat_id,)).fetchall()]


store = Store(DB_PATH)
telegram_app = Application.builder().token(BOT_TOKEN or "000000:invalid").updater(None).build()

UZ_CYR_MARKERS = set("қғҳўҚҒҲЎ")
RUS_MARKERS = set("ыэъёЫЭЪЁ")
EN_MARKERS = {"the", "what", "how", "why", "hello", "please", "can", "help", "is", "are"}
LINK_RE = re.compile(r"(?:https?://|t\.me/|www\.)", re.IGNORECASE)
MENTION_RE = re.compile(r"@\w+")


def detect_language(text: str) -> str:
    lower = text.lower()
    words = set(re.findall(r"[\wʻ’'-]+", lower, flags=re.UNICODE))
    if any(char in text for char in UZ_CYR_MARKERS):
        return "uz_cyrillic"
    if any(char in text for char in RUS_MARKERS):
        return "russian"
    if words & EN_MARKERS:
        return "english"
    if any(word in lower for word in ("salom", "assalomu", "qanday", "nima", "uchun", "kerak", "boladi", "bo‘ladi")):
        return "uzbek"
    return "uzbek"


def reply_text(language: str, first_name: str, question: str) -> str:
    """Return a useful local response when no external AI provider is configured."""
    name = first_name or "do‘st"
    normalized = question.lower().strip()
    is_greeting = any(word in normalized for word in ("salom", "assalomu", "привет", "здравствуйте", "hello", "hi"))
    asks_how = any(word in normalized for word in ("qanday", "qanaqa", "как", "how"))
    asks_what = any(word in normalized for word in ("nima", "что", "what"))
    asks_why = any(word in normalized for word in ("nega", "nima uchun", "почему", "why"))

    if language == "russian":
        if is_greeting:
            return f"Здравствуйте, {name}! Я вас слышу. О какой теме поговорим?"
        if asks_how:
            return f"Похоже, вы спрашиваете о способе действия: «{question}». Уточните цель или ситуацию, и я разложу ответ по шагам."
        if asks_what:
            return f"Вы спрашиваете, что означает «{question}». Уточните термин или предмет — я объясню простыми словами и приведу пример."
        if asks_why:
            return f"Вопрос «{question}» требует причины и контекста. Что именно произошло или какой результат вы хотите понять?"
        return f"Я понял вашу мысль: «{question}». Давайте разберём её по сути: какой ответ вам нужен — объяснение, решение или совет?"
    if language == "english":
        if is_greeting:
            return f"Hello, {name}! I’m listening. What would you like to discuss?"
        if asks_how:
            return f"Your question is about a method: “{question}”. Tell me the goal or situation, and I’ll break it into clear steps."
        if asks_what:
            return f"You are asking what “{question}” means. Name the term or subject, and I’ll explain it simply with an example."
        if asks_why:
            return f"The question “{question}” needs some context. What happened, or which result are you trying to understand?"
        return f"I understand the point you raised: “{question}”. Should I give an explanation, a practical solution, or a balanced opinion?"
    if language == "uz_cyrillic":
        if is_greeting:
            return f"Ассалому алайкум, {name}! Сизни эшитяпман. Қайси мавзу ҳақида суҳбатлашамиз?"
        if asks_how:
            return f"Саволингиз усул ҳақида: «{question}». Мақсадингиз ёки вазиятни ёзинг, жавобни босқичма-босқич тушунтираман."
        if asks_what:
            return f"Сиз «{question}» нималигини сўраяпсиз. Термин ёки мавзуни аниқроқ ёзсангиз, содда мисол билан тушунтираман."
        if asks_why:
            return f"«{question}» саволига жавоб бериш учун вазият керак. Нима содир бўлди ёки қайси натижани тушунмоқчисиз?"
        return f"Фикрингизни тушундим: «{question}». Қайси жавоб керак: тушунтириш, амалий ечим ёки холис фикр?"
    if is_greeting:
        return f"Assalomu alaykum, {name}! Sizni eshityapman. Qaysi mavzuda suhbatlashamiz?"
    if asks_how:
        return f"Savolingiz usul haqida: “{question}”. Maqsadingiz yoki vaziyatni yozing, javobni bosqichma-bosqich tushuntiraman."
    if asks_what:
        return f"Siz “{question}” nimani anglatishini so‘rayapsiz. Atama yoki mavzuni aniqroq yozsangiz, sodda misol bilan tushuntiraman."
    if asks_why:
        return f"“{question}” savoliga aniq javob berish uchun vaziyat kerak. Nima sodir bo‘ldi yoki qaysi natijani tushunmoqchisiz?"
    return f"Fikringizni tushundim: “{question}”. Sizga qaysi biri kerak — tushuntirish, amaliy yechim yoki xolis fikr?"


def target_user(update: Update):
    message = update.effective_message
    if message and message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    return None


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type == ChatType.PRIVATE:
        return True
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await is_admin(update, context):
        return True
    await update.effective_message.reply_text("Bu buyruqni faqat guruh admini ishlatishi mumkin.")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Salom, {user.first_name if user else 'do‘st'}! Men Smart Community Botman.\n\n"
        "Savolingizni yozing yoki /yordam buyrug‘ini bosing. Guruhda meni @mention qiling yoki xabarimga reply qiling."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Smart Community Bot buyruqlari:\n\n"
        "/start — ishga tushirish\n/yordam — imkoniyatlar\n/til — til rejimi\n/holat — server holati\n"
        "/oyin — tezkor savol-o‘yin\n/ball — ballar reytingi\n\n"
        "Admin buyruqlari:\n/ogohlantir, /jim, /jimdanchiqar, /blok, /blokdanchiqar, /hayda\n"
        "/statistika, /filtr, /xulosa, /sozlamalar\n\n"
        "Guruhda oddiy xabarlar ham avtomatik ko‘rib chiqiladi; admin buyruqlari nishon foydalanuvchining xabariga reply qilib ishlatiladi."
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Men o‘zbek lotin, o‘zbek kiril, rus va ingliz tillarini aniqlab javob beraman.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Bot ishlayapti. Webhook, SQLite xotira va takroriy update himoyasi faol.")


async def points_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    rows = store.leaderboard(chat.id)
    if not rows:
        await update.effective_message.reply_text("Hali ball to‘plagan foydalanuvchilar yo‘q.")
        return
    lines = ["🏆 Ballar reytingi:"]
    for index, row in enumerate(rows, 1):
        lines.append(f"{index}. {row['display_name'] or 'Foydalanuvchi'} — {row['points']} ball")
    await update.effective_message.reply_text("\n".join(lines))


async def game_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🧠 Tezkor savol: Agar guruhda 100 ta xabar bo‘lsa va bot faqat foydali savollarga javob bersa, bu nimani anglatadi?\n\n"
        "Javobingizni yozing — bot sizga ball beradi."
    )


async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = target_user(update)
    if not target:
        await update.effective_message.reply_text("Foydalanuvchi xabariga reply qilib /ogohlantir buyrug‘ini yuboring.")
        return
    chat = update.effective_chat
    store.ensure_user(chat.id, target.id, target.full_name)
    count = store.add_warning(chat.id, target.id)
    if count >= 3:
        try:
            await context.bot.ban_chat_member(chat.id, target.id)
            await update.effective_message.reply_text(f"{target.full_name} 3 ta ogohlantirish oldi va guruhdan bloklandi.")
        except Exception:
            await update.effective_message.reply_text(f"{target.full_name} uchun {count}-ogohlantirish berildi. Avtomatik bloklashga ruxsat yetarli emas.")
    else:
        await update.effective_message.reply_text(f"{target.full_name} ga ogohlantirish berildi: {count}/3.")


async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = target_user(update)
    chat = update.effective_chat
    if not target or not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Guruhda foydalanuvchi xabariga reply qilib /jim [daqiqа] yuboring.")
        return
    minutes = 10
    if context.args and context.args[0].isdigit():
        minutes = max(1, min(int(context.args[0]), 10080))
    until = time.time() + minutes * 60
    try:
        await context.bot.restrict_chat_member(chat.id, target.id, ChatPermissions(can_send_messages=False), until_date=datetime.fromtimestamp(until, tz=timezone.utc))
        store.ensure_user(chat.id, target.id, target.full_name)
        store.set_muted(chat.id, target.id, until)
        await update.effective_message.reply_text(f"{target.full_name} {minutes} daqiqaga jim qilindi.")
    except Exception:
        await update.effective_message.reply_text("Jim qilish amalga oshmadi. Botga admin huquqi va xabarlarni cheklash ruxsati kerak.")


async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = target_user(update)
    chat = update.effective_chat
    if not target or not chat:
        await update.effective_message.reply_text("Foydalanuvchi xabariga reply qilib /jimdanchiqar yuboring.")
        return
    try:
        await context.bot.restrict_chat_member(chat.id, target.id, ChatPermissions(can_send_messages=True, can_send_other_messages=True, can_add_web_page_previews=True))
        store.set_muted(chat.id, target.id, 0)
        await update.effective_message.reply_text(f"{target.full_name} yana yozishi mumkin.")
    except Exception:
        await update.effective_message.reply_text("Jimlikni olib tashlash amalga oshmadi. Bot admin ekanini tekshiring.")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = target_user(update)
    chat = update.effective_chat
    if not target or not chat:
        await update.effective_message.reply_text("Foydalanuvchi xabariga reply qilib /blok yuboring.")
        return
    try:
        await context.bot.ban_chat_member(chat.id, target.id)
        await update.effective_message.reply_text(f"{target.full_name} guruhdan bloklandi.")
    except Exception:
        await update.effective_message.reply_text("Bloklash amalga oshmadi. Bot admin huquqini tekshiring.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = target_user(update)
    chat = update.effective_chat
    if not target or not chat:
        await update.effective_message.reply_text("Foydalanuvchi xabariga reply qilib /blokdanchiqar yuboring.")
        return
    try:
        await context.bot.unban_chat_member(chat.id, target.id, only_if_banned=True)
        await update.effective_message.reply_text(f"{target.full_name} blokdan chiqarildi.")
    except Exception:
        await update.effective_message.reply_text("Blokdan chiqarish amalga oshmadi.")


async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    target = target_user(update)
    chat = update.effective_chat
    if not target or not chat:
        await update.effective_message.reply_text("Foydalanuvchi xabariga reply qilib /hayda yuboring.")
        return
    try:
        await context.bot.ban_chat_member(chat.id, target.id)
        await context.bot.unban_chat_member(chat.id, target.id)
        await update.effective_message.reply_text(f"{target.full_name} guruhdan chiqarildi.")
    except Exception:
        await update.effective_message.reply_text("Foydalanuvchini chiqarish amalga oshmadi.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    members, points, warnings = store.stats(chat.id)
    await update.effective_message.reply_text(f"📊 Guruh statistikasi:\nKuzatilgan a’zolar: {members}\nJami ball: {points}\nOgohlantirishlar: {warnings}")


async def filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    if not context.args:
        current = store.filters(chat.id)
        await update.effective_message.reply_text("Filtrlar: " + (", ".join(current) if current else "yo‘q") + "\nQo‘shish: /filtr + so‘z\nO‘chirish: /filtr - so‘z")
        return
    phrase = " ".join(context.args).strip()
    if phrase.startswith("+"):
        store.add_filter(chat.id, phrase[1:].strip())
        await update.effective_message.reply_text("Filtr qo‘shildi.")
    elif phrase.startswith("-"):
        store.remove_filter(chat.id, phrase[1:].strip())
        await update.effective_message.reply_text("Filtr o‘chirildi.")
    else:
        await update.effective_message.reply_text("Foydalanish: /filtr + so‘z yoki /filtr - so‘z")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    row = store.chat(chat.id)
    welcome = "yoqilgan" if row and row["welcome_enabled"] else "o‘chirilgan"
    spam = "yoqilgan" if row and row["anti_spam_enabled"] else "o‘chirilgan"
    await update.effective_message.reply_text(f"⚙️ Sozlamalar:\nKutib olish: {welcome}\nAnti-spam: {spam}\n\nYoqish/o‘chirish keyingi boshqaruv menyusida kengaytiriladi.")


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    members, points, warnings = store.stats(chat.id)
    await update.effective_message.reply_text(f"📝 Guruh xulosasi:\n{members} ta a’zo kuzatildi, {points} ball to‘plandi, {warnings} ta ogohlantirish qayd etildi.")


async def welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    store.ensure_chat(chat.id, chat.title or "")
    row = store.chat(chat.id)
    if row and not row["welcome_enabled"]:
        return
    names = ", ".join(member.full_name for member in (message.new_chat_members or []))
    if names:
        await message.reply_text(f"Xush kelibsiz, {names}! Guruh qoidalariga rioya qiling. Savol bo‘lsa meni @mention qiling.")


async def maybe_ai_reply(question: str, language: str) -> Optional[str]:
    if not (AI_API_URL and AI_API_KEY):
        return None
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"messages": [{"role": "system", "content": "Answer in the user's language. Be thoughtful, safe, and concise."}, {"role": "user", "content": question}]}
    if AI_MODEL:
        payload["model"] = AI_MODEL
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(AI_API_URL, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content")
                if content:
                    return str(content)[:3900]
    except Exception as exc:
        logger.warning("Optional AI provider failed: %s", exc)
    return None


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or not message.text:
        return
    text = message.text.strip()
    store.ensure_chat(chat.id, chat.title or "")
    store.ensure_user(chat.id, user.id, user.full_name)
    store.add_points(chat.id, user.id, 1)

    if chat.type != ChatType.PRIVATE:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
                old_hash, old_time = store.record_message(chat.id, user.id, text)
                repeated = old_hash and old_hash == hashlib.sha256(text.lower().encode("utf-8")).hexdigest() and time.time() - old_time < 20
                row = store.chat(chat.id)
                filters_list = store.filters(chat.id)
                blocked_phrase = next((phrase for phrase in filters_list if phrase and phrase in text.lower()), None)
                if row and row["anti_spam_enabled"] and (blocked_phrase or repeated or (LINK_RE.search(text) and len(text) < 35)):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return
        except Exception:
            pass

        # Automatic conversation mode: every non-command group message reaches the
        # response engine after moderation checks. Mentions and replies are still
        # supported, but are no longer required.

    clean = MENTION_RE.sub("", text).strip()
    language = detect_language(clean)
    ai_reply = await maybe_ai_reply(clean, language)
    await message.reply_text(ai_reply or reply_text(language, user.first_name, clean))


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    await message.reply_text("Fayl qabul qilindi. Ovozdan matn va rasm/OCR tahlili uchun qo‘shimcha server integratsiyasi keyingi bosqichda yoqiladi.")


async def process_update_once(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.update_id is not None and not store.mark_update_once(update.update_id):
        logger.info("Ignoring duplicate Telegram update %s", update.update_id)
        return
    await telegram_app.process_update(update)


telegram_app.add_handler(CommandHandler(["start"], start))
telegram_app.add_handler(CommandHandler(["yordam", "help"], help_command))
telegram_app.add_handler(CommandHandler(["til", "language"], language_command))
telegram_app.add_handler(CommandHandler(["holat", "status"], status_command))
telegram_app.add_handler(CommandHandler(["ball", "points"], points_command))
telegram_app.add_handler(CommandHandler(["oyin", "game"], game_command))
telegram_app.add_handler(CommandHandler(["ogohlantir", "warn"], warn_command))
telegram_app.add_handler(CommandHandler(["jim", "mute"], mute_command))
telegram_app.add_handler(CommandHandler(["jimdanchiqar", "unmute"], unmute_command))
telegram_app.add_handler(CommandHandler(["blok", "ban"], ban_command))
telegram_app.add_handler(CommandHandler(["blokdanchiqar", "unban"], unban_command))
telegram_app.add_handler(CommandHandler(["hayda", "kick"], kick_command))
telegram_app.add_handler(CommandHandler(["statistika", "stats"], stats_command))
telegram_app.add_handler(CommandHandler(["filtr", "filter"], filter_command))
telegram_app.add_handler(CommandHandler(["sozlamalar", "settings"], settings_command))
telegram_app.add_handler(CommandHandler(["xulosa", "summary"], summary_command))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler))
telegram_app.add_handler(MessageHandler(filters.VOICE | filters.PHOTO, media_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))


@app.get("/")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "smart-community-bot", "features": ["multilingual", "moderation", "anti-spam", "points", "games", "idempotent-updates"]}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is not configured"}
    global telegram_ready
    try:
        if not telegram_ready:
            await telegram_app.initialize()
            telegram_ready = True
        payload = await request.json()
        update = Update.de_json(payload, telegram_app.bot)
        if update is None:
            return {"ok": False, "error": "invalid update"}
        async with update_lock:
            await process_update_once(update, None)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Webhook processing failed: %s", exc)
        return {"ok": False, "error": "update processing failed"}


@app.on_event("startup")
async def startup() -> None:
    global telegram_ready
    if not BOT_TOKEN:
        return
    try:
        await telegram_app.initialize()
        telegram_ready = True
        if PUBLIC_URL:
            await telegram_app.bot.set_webhook(
                url=f"{PUBLIC_URL}{WEBHOOK_PATH}",
                drop_pending_updates=False,
            )
        logger.info("Telegram webhook configured at %s", WEBHOOK_PATH)
    except Exception as exc:
        telegram_ready = False
        logger.exception("Telegram startup configuration failed; health service remains online: %s", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    global telegram_ready
    if BOT_TOKEN and telegram_ready:
        await telegram_app.shutdown()
        telegram_ready = False
