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
import mimetypes
import os
import re
import sqlite3
import tempfile
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
AI_PROVIDER = os.environ.get("AI_PROVIDER", "").strip().lower()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
AI_API_URL = os.environ.get("AI_API_URL", "").strip() or ("https://api.groq.com/openai/v1/chat/completions" if GROQ_API_KEY else "")
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip() or GROQ_API_KEY
AI_MODEL = os.environ.get("AI_MODEL", "").strip() or os.environ.get("GROQ_CHAT_MODEL", "").strip() or ("openai/gpt-oss-120b" if GROQ_API_KEY else "")
TRANSCRIBE_API_URL = os.environ.get("TRANSCRIBE_API_URL", "").strip() or ("https://api.groq.com/openai/v1/audio/transcriptions" if GROQ_API_KEY else "")
TRANSCRIBE_API_KEY = os.environ.get("TRANSCRIBE_API_KEY", "").strip() or GROQ_API_KEY
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "").strip() or "whisper-large-v3-turbo"
VISION_API_URL = os.environ.get("VISION_API_URL", "").strip()
VISION_API_KEY = os.environ.get("VISION_API_KEY", "").strip()
def read_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        logger.warning("Invalid %s value; using default.", name)
        return default


MEDIA_MAX_BYTES = read_positive_int("MEDIA_MAX_BYTES", 12 * 1024 * 1024)
REQUIRED_CHANNEL_ID = os.environ.get("REQUIRED_CHANNEL_ID", "").strip()
REQUIRED_CHANNEL_URL = os.environ.get("REQUIRED_CHANNEL_URL", "").strip()
BOT_DB_PATH_RAW = os.environ.get("BOT_DB_PATH", "").strip()
DB_PATH = Path(BOT_DB_PATH_RAW or "/tmp/smart-community-bot.sqlite3")
DB_STORAGE_MODE = "configured" if BOT_DB_PATH_RAW else "ephemeral"
WEBHOOK_PATH = f"/telegram-webhook/{hashlib.sha256(WEBHOOK_SECRET.encode('utf-8')).hexdigest()[:24]}"

app = FastAPI(title="Smart Community Bot")
update_lock = asyncio.Lock()
telegram_ready = False
schedule_task: asyncio.Task[None] | None = None

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
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS moderation_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL,
                    admin_name TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    target_id INTEGER NOT NULL DEFAULT 0,
                    target_name TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    admin_id INTEGER NOT NULL,
                    post_time TEXT NOT NULL,
                    content TEXT NOT NULL,
                    last_sent_date TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS required_chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    invite_url TEXT NOT NULL DEFAULT '',
                    added_by INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expiry_at REAL
                );
                CREATE TABLE IF NOT EXISTS subscription_checks (
                    scope_chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    required_chat_id TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    checked_at REAL NOT NULL,
                    PRIMARY KEY (scope_chat_id, user_id, required_chat_id)
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(required_chats)").fetchall()}
            if "expiry_at" not in columns:
                conn.execute("ALTER TABLE required_chats ADD COLUMN expiry_at REAL")
            subscription_columns = {row["name"] for row in conn.execute("PRAGMA table_info(subscription_checks)").fetchall()}
            if "display_name" not in subscription_columns:
                conn.execute("ALTER TABLE subscription_checks ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
            if "username" not in subscription_columns:
                conn.execute("ALTER TABLE subscription_checks ADD COLUMN username TEXT NOT NULL DEFAULT ''")

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

    def log_action(self, chat_id: int, admin_id: int, admin_name: str, action: str, target_id: int = 0, target_name: str = "", details: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO moderation_actions(chat_id,admin_id,admin_name,action,target_id,target_name,details,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (chat_id, admin_id, admin_name or "", action, target_id, target_name or "", details[:500], time.time()),
            )

    def recent_actions(self, chat_id: int, limit: int = 5) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT admin_name,action,target_name,details,created_at FROM moderation_actions WHERE chat_id=? ORDER BY id DESC LIMIT ?", (chat_id, limit)).fetchall())

    def add_schedule(self, chat_id: int, admin_id: int, post_time: str, content: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO scheduled_posts(chat_id,admin_id,post_time,content,created_at) VALUES(?,?,?,?,?)", (chat_id, admin_id, post_time, content[:3900], time.time()))

    def schedules(self, chat_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT id,post_time,content,enabled FROM scheduled_posts WHERE chat_id=? ORDER BY post_time,id", (chat_id,)).fetchall())

    def due_schedules(self, post_time: str, date_key: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT id,chat_id,content FROM scheduled_posts WHERE enabled=1 AND post_time=? AND last_sent_date<>?", (post_time, date_key)).fetchall())

    def mark_schedule_sent(self, schedule_id: int, date_key: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE scheduled_posts SET last_sent_date=? WHERE id=?", (date_key, schedule_id))

    def add_required_chat(self, chat_id: int, title: str, invite_url: str, added_by: int, expiry_at: float | None = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO required_chats(chat_id,title,invite_url,added_by,created_at,expiry_at) VALUES(?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, invite_url=excluded.invite_url, added_by=excluded.added_by, expiry_at=excluded.expiry_at", (chat_id, title or "", invite_url or "", added_by, time.time(), expiry_at))

    def remove_required_chat(self, chat_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM required_chats WHERE chat_id=?", (chat_id,))

    def purge_expired_required_chats(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM required_chats WHERE expiry_at IS NOT NULL AND expiry_at <= ?", (time.time(),))

    def required_chats(self) -> list[sqlite3.Row]:
        self.purge_expired_required_chats()
        with self.connect() as conn:
            return list(conn.execute("SELECT chat_id,title,invite_url,expiry_at FROM required_chats ORDER BY title,chat_id").fetchall())

    def record_subscription_pass(self, scope_chat_id: int, user_id: int, required_chat_ids: list[str], display_name: str = "", username: str = "") -> None:
        now = time.time()
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO subscription_checks(scope_chat_id,user_id,required_chat_id,display_name,username,checked_at) VALUES(?,?,?,?,?,?) ON CONFLICT(scope_chat_id,user_id,required_chat_id) DO UPDATE SET display_name=excluded.display_name, username=excluded.username, checked_at=excluded.checked_at",
                [(scope_chat_id, user_id, required_chat_id, display_name or "", username or "", now) for required_chat_id in required_chat_ids],
            )

    def subscription_roster_count(self, scope_chat_id: int, required_chat_id: str = "") -> int:
        with self.connect() as conn:
            if required_chat_id:
                row = conn.execute("SELECT COUNT(DISTINCT user_id) AS total FROM subscription_checks WHERE scope_chat_id=? AND required_chat_id=?", (scope_chat_id, required_chat_id)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(DISTINCT user_id) AS total FROM subscription_checks WHERE scope_chat_id=?", (scope_chat_id,)).fetchone()
            return int(row["total"] if row else 0)

    def subscription_roster(self, scope_chat_id: int, required_chat_id: str = "", limit: int = 40, offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as conn:
            if required_chat_id:
                return list(conn.execute("SELECT display_name,username,required_chat_id,checked_at FROM subscription_checks WHERE scope_chat_id=? AND required_chat_id=? GROUP BY user_id ORDER BY checked_at DESC LIMIT ? OFFSET ?", (scope_chat_id, required_chat_id, limit, offset)).fetchall())
            return list(conn.execute("SELECT display_name,username,required_chat_id,checked_at FROM subscription_checks WHERE scope_chat_id=? GROUP BY user_id ORDER BY checked_at DESC LIMIT ? OFFSET ?", (scope_chat_id, limit, offset)).fetchall())

    def subscription_stats(self, scope_chat_id: int) -> dict[str, Any]:
        now = time.time()
        day_start = now - 86400
        week_start = now - 7 * 86400
        with self.connect() as conn:
            totals = conn.execute(
                "SELECT COUNT(DISTINCT user_id) AS all_time, COUNT(DISTINCT CASE WHEN checked_at>=? THEN user_id END) AS today, COUNT(DISTINCT CASE WHEN checked_at>=? THEN user_id END) AS week FROM subscription_checks WHERE scope_chat_id=?",
                (day_start, week_start, scope_chat_id),
            ).fetchone()
            per_chat = list(conn.execute(
                "SELECT required_chat_id, COUNT(DISTINCT user_id) AS users FROM subscription_checks WHERE scope_chat_id=? GROUP BY required_chat_id ORDER BY required_chat_id",
                (scope_chat_id,),
            ).fetchall())
        return {"all_time": int(totals["all_time"]), "today": int(totals["today"]), "week": int(totals["week"]), "per_chat": per_chat}

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

    def add_conversation_message(self, chat_id: int, user_name: str, role: str, content: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO conversation_messages(chat_id,user_name,role,content,created_at) VALUES(?,?,?,?,?)",
                (chat_id, user_name or "", role, content[:2000], time.time()),
            )
            conn.execute(
                "DELETE FROM conversation_messages WHERE chat_id=? AND id NOT IN (SELECT id FROM conversation_messages WHERE chat_id=? ORDER BY id DESC LIMIT 12)",
                (chat_id, chat_id),
            )

    def conversation(self, chat_id: int, limit: int = 8) -> list[dict[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversation_messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"])} for row in reversed(rows)]


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
    open_topic = any(marker in normalized for marker in (
        "nima haqida suhbat", "nima haqida gap", "qaysi mavzuda", "что обсудим", "о чём поговорим",
        "what should we discuss", "what can we talk about",
    ))
    book_question = any(marker in normalized for marker in (
        "kitob", "o'qishga arzi", "o‘qishga arzi", "книг", "что почитать", "book", "read",
    ))
    identity_question = any(marker in normalized for marker in ("kimsan", "kim bo‘lasan", "kim bo'lasan", "кто ты", "who are you"))
    thanks = any(marker in normalized for marker in ("rahmat", "raxmat", "спасибо", "thanks", "thank you"))
    capability_question = any(marker in normalized for marker in ("nima qila olasan", "nimalar qila olasan", "что ты умеешь", "what can you do"))

    if thanks:
        if language == "russian":
            return "Пожалуйста. Если понадобится, разберём вопрос по шагам."
        if language == "english":
            return "You’re welcome. If needed, I can break the question down step by step."
        if language == "uz_cyrillic":
            return "Арзимайди. Керак бўлса, саволни босқичма-босқич тушунтираман."
        return "Arzimaydi. Kerak bo‘lsa, savolni bosqichma-bosqich tushuntiraman."

    if identity_question:
        if language == "russian":
            return "Я Dadasi — группа помощник: отвечаю на вопросы, помогаю администраторам и слежу за порядком."
        if language == "english":
            return "I’m Dadasi, a community assistant for questions, moderation, scheduled posts, and group activity."
        if language == "uz_cyrillic":
            return "Мен — Dadasi ботман: саволларга жавоб бераман, админларга ёрдамлашаман ва гуруҳ тартибини кузатаман."
        return "Men Dadasi botman: savollarga javob beraman, adminlarga yordam beraman va guruh tartibini kuzataman."

    if capability_question:
        if language == "russian":
            return "Я умею отвечать на вопросы, помогать с модерацией, фильтровать спам, проводить игры, считать баллы, планировать посты и проверять обязательную подписку."
        if language == "english":
            return "I can answer questions, help with moderation, filter spam, run games, track points, schedule posts, and check required subscriptions."
        if language == "uz_cyrillic":
            return "Мен саволларга жавоб бераман, модерацияга ёрдамлашаман, спамни филтрлайман, ўйин ўтказаман, балл санайман, пост режалайман ва мажбурий обунани текшираман."
        return "Savollarga javob beraman, moderatsiyaga yordam beraman, spamni filtrlayman, o‘yin o‘tkazaman, ball sanayman, post rejalayman va majburiy obunani tekshiraman."

    if book_question:
        if language == "russian":
            return "Если хочется начать с сильных книг: «Атомные привычки» — для практических изменений, «Sapiens» — для широкого взгляда на историю, а «Маленькая жизнь» — для глубокого чтения. Я бы начал с первой."
        if language == "english":
            return "For a good start, try *Atomic Habits* for practical change, *Sapiens* for a broad view of history, or *The Midnight Library* for reflective fiction. I’d start with the first one."
        if language == "uz_cyrillic":
            return "Китобдан бошлаш учун: «Атом одатлар» — амалий ўзгаришлар учун, «Sapiens» — тарихга кенгроқ қараш учун, «Кеча ярим тун кутубхонаси» — таъсирли бадиий асар сифатида яхши танлов. Мен биринчи китобдан бошлардим."
        return "Kitobdan boshlash uchun uchta yaxshi yo‘nalish: «Atom odatlar» — amaliy o‘zgarishlar uchun, «Sapiens» — tarixga kengroq qarash uchun, «Yarim tun kutubxonasi» — ta’sirli badiiy asar sifatida. Men birinchi kitobdan boshlardim."

    if language == "russian":
        if is_greeting:
            return f"Здравствуйте, {name}! Я вас слышу. О какой теме поговорим?"
        if open_topic:
            return "Давайте выберем тему: полезные технологии, новости сообщества, книги, фильмы или жизненные советы. Что интереснее?"
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
        if open_topic:
            return "We can discuss useful technology, community news, books, films, or practical life questions. Which topic should we start with?"
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
        if open_topic:
            return "Технология, жамоа янгиликлари, китоблар, фильмлар ёки ҳаётий маслаҳатлар ҳақида суҳбатлашишимиз мумкин. Қайси мавзуни танлаймиз?"
        if asks_how:
            return f"Саволингиз усул ҳақида: «{question}». Мақсадингиз ёки вазиятни ёзинг, жавобни босқичма-босқич тушунтираман."
        if asks_what:
            return f"Сиз «{question}» нималигини сўраяпсиз. Термин ёки мавзуни аниқроқ ёзсангиз, содда мисол билан тушунтираман."
        if asks_why:
            return f"«{question}» саволига жавоб бериш учун вазият керак. Нима содир бўлди ёки қайси натижани тушунмоқчисиз?"
        return f"Фикрингизни тушундим: «{question}». Қайси жавоб керак: тушунтириш, амалий ечим ёки холис фикр?"
    if is_greeting:
        return f"Assalomu alaykum, {name}! Sizni eshityapman. Qaysi mavzuda suhbatlashamiz?"
    if open_topic:
        return "Texnologiya, guruh yangiliklari, kitoblar, filmlar yoki hayotiy maslahatlar haqida suhbatlashishimiz mumkin. Qaysi mavzuni tanlaymiz?"
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
        f"Salom, {user.first_name if user else 'do‘st'}! Men Dadasi botman.\n\n"
        "Savolingizni yozing yoki /yordam buyrug‘ini bosing. Guruhda oddiy xabar yozsangiz ham suhbatga qo‘shilaman."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Smart Community Bot buyruqlari:\n\n"
        "/start — ishga tushirish\n/yordam — imkoniyatlar\n/til — til rejimi\n/holat — server holati\n"
        "/oyin — tezkor savol-o‘yin\n/ball — ballar reytingi\n\n"
        "Admin buyruqlari:\n/ogohlantir, /jim, /jimdanchiqar, /blok, /blokdanchiqar, /hayda\n"
        "/statistika, /filtr, /xulosa, /sozlamalar\n"
        "/rejalashtir, /obuna, /obuna_statistika, /obuna_kimlar\n"
        "/majburiy_qosh, /majburiy_royxat, /majburiy_ochir\n\n"
        "Guruhdagi oddiy xabarlar avtomatik ko‘rib chiqiladi. Admin buyruqlarida kerak bo‘lsa nishon foydalanuvchining xabariga reply qiling."
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


def audit(update: Update, action: str, target: Any = None, details: str = "") -> None:
    chat = update.effective_chat
    admin = update.effective_user
    if chat and admin:
        store.log_action(chat.id, admin.id, admin.full_name, action, getattr(target, "id", 0), getattr(target, "full_name", ""), details)


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
    audit(update, "ogohlantir", target, f"{count}/3")
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
        audit(update, "jim", target, f"{minutes} daqiqa")
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
        audit(update, "jimdanchiqar", target)
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
        audit(update, "blok", target)
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
        audit(update, "blokdanchiqar", target)
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
        audit(update, "hayda", target)
        await update.effective_message.reply_text(f"{target.full_name} guruhdan chiqarildi.")
    except Exception:
        await update.effective_message.reply_text("Foydalanuvchini chiqarish amalga oshmadi.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    members, points, warnings = store.stats(chat.id)
    recent = store.recent_actions(chat.id)
    audit_text = "\n".join(f"{row['admin_name'] or 'admin'} → {row['action']} → {row['target_name'] or '-'}" for row in recent) or "Hali admin amallari yo‘q."
    subscription = store.subscription_stats(chat.id)
    await update.effective_message.reply_text(f"📊 Guruh statistikasi:\nKuzatilgan a’zolar: {members}\nJami ball: {points}\nOgohlantirishlar: {warnings}\nMajburiy obunadan o‘tganlar: {subscription['all_time']} ta\n\nSo‘nggi admin amallari:\n{audit_text}")


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
        audit(update, "filtr-qo‘shish", details=phrase[1:].strip())
        await update.effective_message.reply_text("Filtr qo‘shildi.")
    elif phrase.startswith("-"):
        store.remove_filter(chat.id, phrase[1:].strip())
        audit(update, "filtr-o‘chirish", details=phrase[1:].strip())
        await update.effective_message.reply_text("Filtr o‘chirildi.")
    else:
        await update.effective_message.reply_text("Foydalanish: /filtr + so‘z yoki /filtr - so‘z")


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    admin = update.effective_user
    if not chat or not admin:
        return
    raw = " ".join(context.args).strip()
    if not raw:
        rows = store.schedules(chat.id)
        if not rows:
            await update.effective_message.reply_text("Hali rejalashtirilgan postlar yo‘q. Misol: /rejalashtir 18:30 | Bugun muhim yangilik bor.")
            return
        lines = ["Rejalashtirilgan postlar:"] + [f"{row['id']}. {row['post_time']} — {row['content']}" for row in rows]
        await update.effective_message.reply_text("\n".join(lines))
        return
    if "|" not in raw:
        await update.effective_message.reply_text("Foydalanish: /rejalashtir 18:30 | Post matni")
        return
    post_time, content = (part.strip() for part in raw.split("|", 1))
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", post_time) or not content:
        await update.effective_message.reply_text("Vaqt HH:MM ko‘rinishida bo‘lsin. Misol: /rejalashtir 09:00 | Xayrli tong!")
        return
    store.add_schedule(chat.id, admin.id, post_time, content)
    audit(update, "rejalashtir", details=f"{post_time} | {content}")
    await update.effective_message.reply_text(f"Post {post_time} UTC vaqtiga rejalashtirildi.")


async def resolve_required_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = update.effective_chat
    if not current:
        return None
    reference = context.args[0].strip() if context.args else ""
    if not reference:
        target = current
    else:
        if reference.startswith("https://t.me/"):
            reference = "@" + reference.removeprefix("https://t.me/").strip("/").split("/")[0]
        try:
            lookup = int(reference) if re.fullmatch(r"-?\d+", reference) else reference
            target = await context.bot.get_chat(lookup)
        except Exception:
            await update.effective_message.reply_text("Bu guruh topilmadi. @username yoki -100... ko‘rinishidagi ID ni tekshiring.")
            return None
    if target.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Majburiy obunaga shaxsiy akkauntni qo‘shib bo‘lmaydi.")
        return None
    try:
        me = await context.bot.get_me()
        bot_member = await context.bot.get_chat_member(target.id, me.id)
        if bot_member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}:
            await update.effective_message.reply_text("Botni avval shu maqsadli guruh yoki kanalda administrator qiling.")
            return None
    except Exception:
        await update.effective_message.reply_text("Bot bu guruhni tekshira olmayapti. Botni guruhga qo‘shib administrator qiling.")
        return None
    return target


def parse_required_expiry(value: str) -> tuple[float | None, str]:
    now = datetime.now(timezone.utc)
    match = re.fullmatch(r"(6|12|24)(?:soat|s|h)?", value.lower())
    if match:
        return time.time() + int(match.group(1)) * 3600, f"{match.group(1)} soat"
    if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        expiry = datetime.strptime(value, "%H:%M").replace(tzinfo=timezone.utc)
        expiry = expiry.replace(year=now.year, month=now.month, day=now.day)
        if expiry <= now:
            expiry += timedelta(days=1)
        return expiry.timestamp(), f"UTC {value}"
    return None, "doimiy"


def expiry_label(expiry_at: float | None) -> str:
    if not expiry_at:
        return "doimiy"
    return datetime.fromtimestamp(float(expiry_at), timezone.utc).strftime("%Y-%m-%d %H:%M UTC gacha")


async def required_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    admin = update.effective_user
    if not admin:
        return
    target = await resolve_required_chat(update, context)
    if not target:
        return
    expiry_at = None
    expiry_text = "doimiy"
    if len(context.args) >= 2:
        expiry_at, expiry_text = parse_required_expiry(context.args[1].strip())
        if expiry_at is None and expiry_text == "doimiy":
            await update.effective_message.reply_text("Vaqt 6soat, 12soat, 24soat yoki HH:MM ko‘rinishida bo‘lsin.")
            return
    invite_url = f"https://t.me/{target.username}" if target.username else ""
    if not invite_url:
        try:
            invite_url = await context.bot.export_chat_invite_link(target.id)
        except Exception:
            invite_url = ""
    store.add_required_chat(target.id, target.title or str(target.id), invite_url, admin.id, expiry_at)
    audit(update, "majburiy-obuna-qo‘shish", details=f"{target.title or target.id} ({target.id}) | {expiry_text}")
    await update.effective_message.reply_text(f"{target.title or target.id} majburiy obunaga qo‘shildi. Amal qilish muddati: {expiry_text}.")


async def required_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    rows = store.required_chats()
    if not rows and not REQUIRED_CHANNEL_ID:
        await update.effective_message.reply_text("Majburiy obuna ro‘yxati bo‘sh.")
        return
    lines = ["Majburiy obuna guruhlari:"]
    for row in rows:
        lines.append(f"- {row['title']} ({row['chat_id']}) — {expiry_label(row['expiry_at'])}")
    if REQUIRED_CHANNEL_ID:
        lines.append(f"- Sozlamadagi kanal: {REQUIRED_CHANNEL_ID}")
    await update.effective_message.reply_text("\n".join(lines))


async def required_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    current = update.effective_chat
    if not current or current.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Bu buyruqni guruh ichida yuboring.")
        return
    if context.args:
        reference = context.args[0].strip()
        if reference.startswith("https://t.me/"):
            reference = "@" + reference.removeprefix("https://t.me/").strip("/").split("/")[0]
        try:
            target = await context.bot.get_chat(int(reference) if re.fullmatch(r"-?\d+", reference) else reference)
        except Exception:
            await update.effective_message.reply_text("Bu guruh topilmadi. @username yoki -100... ko‘rinishidagi ID ni tekshiring.")
            return
    else:
        target = current
    store.remove_required_chat(target.id)
    audit(update, "majburiy-obuna-o‘chirish", details=f"{target.title or target.id} ({target.id})")
    await update.effective_message.reply_text(f"{target.title or target.id} majburiy obuna ro‘yxatidan olib tashlandi.")


async def subscription_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    required = [(str(row["chat_id"]), str(row["title"]), str(row["invite_url"]), row["expiry_at"]) for row in store.required_chats()]
    if REQUIRED_CHANNEL_ID and not any(chat_id == REQUIRED_CHANNEL_ID for chat_id, _, _, _ in required):
        required.append((REQUIRED_CHANNEL_ID, "Kerakli kanal", REQUIRED_CHANNEL_URL, None))
    if not required:
        await update.effective_message.reply_text("Majburiy obuna ro‘yxati bo‘sh. Admin guruh ichida /majburiy_qosh buyrug‘ini yuborsin.")
        return
    missing: list[str] = []
    for chat_id, title, invite_url, _expiry_at in required:
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            active = member.status not in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}
        except Exception:
            active = False
        if not active:
            missing.append(f"{title}: {invite_url}" if invite_url else title)
    if not missing:
        scope_chat = update.effective_chat
        if scope_chat:
            store.record_subscription_pass(scope_chat.id, user.id, [chat_id for chat_id, _, _, _ in required], user.full_name, user.username or "")
        await update.effective_message.reply_text("Barcha majburiy obunalar tasdiqlandi.")
    else:
        await update.effective_message.reply_text("Avval quyidagi guruh yoki kanallarga kiring:\n" + "\n".join(missing) + "\n\nKeyin /obuna buyrug‘ini qayta yuboring.")


async def subscription_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Bu buyruqni guruh ichida yuboring.")
        return
    summary = store.subscription_stats(chat.id)
    lines = [
        "📈 Majburiy obuna statistikasi:",
        f"Bugun o‘tganlar: {summary['today']} ta",
        f"Oxirgi 7 kunda: {summary['week']} ta",
        f"Umumiy noyob odamlar: {summary['all_time']} ta",
    ]
    per_chat = summary["per_chat"]
    if per_chat:
        lines.append("\nGuruhlar bo‘yicha:")
        titles = {str(row["chat_id"]): str(row["title"]) for row in store.required_chats()}
        for row in per_chat:
            lines.append(f"- {titles.get(str(row['required_chat_id']), row['required_chat_id'])}: {int(row['users'])} ta")
    else:
        lines.append("\nHali hech kim /obuna orqali tasdiqlanmagan.")
    await update.effective_message.reply_text("\n".join(lines))


async def subscription_roster_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    if not chat or chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text("Bu buyruqni guruh ichida yuboring.")
        return
    required_chat_id = ""
    reference = context.args[0].strip() if context.args else ""
    if reference:
        if reference.startswith("https://t.me/"):
            reference = "@" + reference.removeprefix("https://t.me/").strip("/").split("/")[0]
        try:
            target = await context.bot.get_chat(int(reference) if re.fullmatch(r"-?\d+", reference) else reference)
            required_chat_id = str(target.id)
        except Exception:
            await update.effective_message.reply_text("Guruh topilmadi. @username yoki -100... ko‘rinishidagi ID ni tekshiring.")
            return
    total = store.subscription_roster_count(chat.id, required_chat_id)
    if not total:
        await update.effective_message.reply_text("Hali /obuna orqali muvaffaqiyatli o‘tganlar yo‘q.")
        return
    rows = store.subscription_roster(chat.id, required_chat_id, total, 0)
    messages: list[str] = []
    current = [f"Jami muvaffaqiyatli o‘tganlar: {total} ta", "Majburiy obunadan o‘tganlar:"]
    for index, row in enumerate(rows, 1):
        name = str(row["display_name"] or "Noma’lum ism")
        username = f"@{row['username']}" if row["username"] else "username yo‘q"
        checked = datetime.fromtimestamp(float(row["checked_at"]), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"{index}. {name} — {username} — {checked}"
        if sum(len(line) + 1 for line in current) + len(entry) > 3800:
            messages.append("\n".join(current))
            current = []
        current.append(entry)
    if current:
        messages.append("\n".join(current))
    for report in messages:
        await update.effective_message.reply_text(report)


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


async def maybe_ai_reply(question: str, language: str, chat_id: int | None = None) -> Optional[str]:
    if not (AI_API_URL and AI_API_KEY):
        return None
    language_name = {
        "uzbek": "Uzbek Latin",
        "uz_cyrillic": "Uzbek Cyrillic",
        "russian": "Russian",
        "english": "English",
    }.get(language, "the user's language")
    system_prompt = (
        f"You are Dadasi, a warm and intelligent Telegram community participant. Answer in {language_name}. "
        "Reply directly to the latest message, using the recent conversation for context. "
        "Do not describe the user's question or ask generic meta-questions such as whether they want an explanation. "
        "Give a concrete, useful answer first, keep group replies reasonably concise, and ask at most one natural follow-up when it helps. "
        "Never claim to have current information unless it is present in the conversation."
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if chat_id is not None:
        messages.extend(store.conversation(chat_id))
    messages.append({"role": "user", "content": question})
    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {"messages": messages}
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
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:240].replace("\n", " ") if exc.response is not None else ""
        logger.warning("Optional AI provider failed status=%s detail=%s", exc.response.status_code if exc.response is not None else "unknown", detail)
    except Exception as exc:
        logger.warning("Optional AI provider failed type=%s", type(exc).__name__)
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
    ai_reply = await maybe_ai_reply(clean, language, chat.id)
    final_reply = ai_reply or reply_text(language, user.first_name, clean)
    store.add_conversation_message(chat.id, user.full_name, "user", clean)
    store.add_conversation_message(chat.id, "Dadasi", "assistant", final_reply)
    await message.reply_text(final_reply)


def provider_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("text", "transcript", "description", "result"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message_data = choices[0].get("message") or {}
        content = message_data.get("content") or choices[0].get("text")
        if isinstance(content, str):
            return content.strip()
    return ""


async def call_transcription_provider(url: str, key: str, file_path: Path, filename: str, language: str) -> str:
    if not (url and key):
        return ""
    headers = {"Authorization": f"Bearer {key}"}
    language_code = {"russian": "ru", "english": "en", "uzbek": "uz", "uz_cyrillic": "uz"}.get(language, "")
    data: dict[str, str] = {"model": TRANSCRIBE_MODEL, "response_format": "json", "temperature": "0"}
    if language_code:
        data["language"] = language_code
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with file_path.open("rb") as media_file:
                response = await client.post(
                    url,
                    headers=headers,
                    data=data,
                    files={"file": (filename, media_file, "audio/ogg")},
                )
            response.raise_for_status()
            return provider_text(response.json())
    except Exception as exc:
        logger.warning("Transcription provider failed: %s", exc)
        return ""


async def call_media_provider(url: str, key: str, file_path: Path, filename: str, prompt: str) -> str:
    if not (url and key):
        return ""
    headers = {"Authorization": f"Bearer {key}"}
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            with file_path.open("rb") as media_file:
                response = await client.post(
                    url,
                    headers=headers,
                    data={"prompt": prompt},
                    files={"file": (filename, media_file, mime)},
                )
            response.raise_for_status()
            return provider_text(response.json())
    except Exception as exc:
        logger.warning("Media provider failed: %s", exc)
        return ""


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    language = detect_language(message.caption or "")
    is_voice = bool(message.voice)
    provider_url = TRANSCRIBE_API_URL if is_voice else VISION_API_URL
    provider_key = TRANSCRIBE_API_KEY if is_voice else VISION_API_KEY
    if not provider_url or not provider_key:
        if is_voice:
            text = {
                "russian": "Голосовое сообщение получено. Для расшифровки голосовых сообщений нужно подключить сервис транскрибации.",
                "english": "I received the voice message. A transcription provider still needs to be connected to convert it to text.",
                "uz_cyrillic": "Овозли хабар қабул қилинди. Уни матнга айлантириш учун транскрипция хизмати ҳали уланиши керак.",
            }.get(language, "Ovozli xabarni oldim. Uni matnga aylantirish uchun transkripsiya xizmati hali ulanmagan.")
        else:
            text = {
                "russian": "Изображение получено. Для распознавания текста и анализа изображения нужно подключить vision/OCR-сервис.",
                "english": "I received the image. A vision/OCR provider still needs to be connected for text extraction and image analysis.",
                "uz_cyrillic": "Расм қабул қилинди. Матнни таниш ва расмни таҳлил қилиш учун vision/OCR хизмати ҳали уланиши керак.",
            }.get(language, "Rasmni oldim. Undagi matnni o‘qish va mazmunini tahlil qilish uchun vision/OCR xizmati hali ulanmagan.")
        await message.reply_text(text)
        return
    try:
        telegram_file = await context.bot.get_file(message.voice.file_id if is_voice else message.photo[-1].file_id)
        suffix = ".oga" if is_voice else ".jpg"
        with tempfile.NamedTemporaryFile(prefix="smart-community-media-", suffix=suffix, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            await telegram_file.download_to_drive(custom_path=str(temp_path))
            if temp_path.stat().st_size > MEDIA_MAX_BYTES:
                await message.reply_text("Fayl hajmi juda katta. Iltimos, kichikroq fayl yuboring.")
                return
            prompt = (
                "Transcribe this voice message accurately. Return only the transcription."
                if is_voice
                else "Read all visible text and briefly describe the image. Answer in the user's language."
            )
            result = (
                await call_transcription_provider(provider_url, provider_key, temp_path, temp_path.name, language)
                if is_voice
                else await call_media_provider(provider_url, provider_key, temp_path, temp_path.name, prompt)
            )
            if result:
                prefix = "Ovozli xabaringiz matni:\n" if is_voice else "Rasm tahlili:\n"
                await message.reply_text(prefix + result[:3900])
            else:
                await message.reply_text("Faylni tahlil qilish vaqtida xizmat javob bermadi. Keyinroq qayta urinib ko‘ring.")
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Telegram media handling failed: %s", exc)
        await message.reply_text("Faylni qabul qilishda vaqtinchalik xatolik yuz berdi.")


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
telegram_app.add_handler(CommandHandler(["obuna_statistika", "subscription_stats"], subscription_stats_command))
telegram_app.add_handler(CommandHandler(["obuna_kimlar", "subscription_users"], subscription_roster_command))
telegram_app.add_handler(CommandHandler(["filtr", "filter"], filter_command))
telegram_app.add_handler(CommandHandler(["sozlamalar", "settings"], settings_command))
telegram_app.add_handler(CommandHandler(["xulosa", "summary"], summary_command))
telegram_app.add_handler(CommandHandler(["rejalashtir", "schedule"], schedule_command))
telegram_app.add_handler(CommandHandler(["obuna", "subscribe"], subscription_command))
telegram_app.add_handler(CommandHandler(["majburiy_qosh", "obunagaqosh"], required_add_command))
telegram_app.add_handler(CommandHandler(["majburiy_royxat", "obunalar"], required_list_command))
telegram_app.add_handler(CommandHandler(["majburiy_ochir", "obunadanol"], required_remove_command))
telegram_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler))
telegram_app.add_handler(MessageHandler(filters.VOICE | filters.PHOTO, media_handler))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))


@app.get("/")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "smart-community-bot", "storage": DB_STORAGE_MODE, "features": ["multilingual", "moderation", "anti-spam", "points", "games", "conversation-memory", "voice-provider-ready", "vision-ocr-provider-ready", "self-service-subscription", "scheduled-posts", "idempotent-updates"]}


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


async def schedule_worker() -> None:
    while True:
        now = datetime.now(timezone.utc)
        post_time = now.strftime("%H:%M")
        date_key = now.strftime("%Y-%m-%d")
        for row in store.due_schedules(post_time, date_key):
            try:
                await telegram_app.bot.send_message(chat_id=int(row["chat_id"]), text=str(row["content"]))
                store.mark_schedule_sent(int(row["id"]), date_key)
            except Exception as exc:
                logger.warning("Scheduled post %s failed: %s", row["id"], exc)
        await asyncio.sleep(30)


@app.on_event("startup")
async def startup() -> None:
    global telegram_ready, schedule_task
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
        schedule_task = asyncio.create_task(schedule_worker())
        logger.info("Telegram webhook configured at %s", WEBHOOK_PATH)
    except Exception as exc:
        telegram_ready = False
        logger.exception("Telegram startup configuration failed; health service remains online: %s", exc)


@app.on_event("shutdown")
async def shutdown() -> None:
    global telegram_ready, schedule_task
    if schedule_task:
        schedule_task.cancel()
        schedule_task = None
    if BOT_TOKEN and telegram_ready:
        await telegram_app.shutdown()
        telegram_ready = False
