import os
import re
import logging
from typing import Optional

from fastapi import FastAPI, Request
from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smart-community-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me").strip()
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
WEBHOOK_PATH = f"/{WEBHOOK_SECRET}"

app = FastAPI(title="Smart Community Bot")

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set. Add it in Render Environment Variables.")

telegram_app = Application.builder().token(BOT_TOKEN or "000000:invalid").updater(None).build()

UZBEK_CYRILLIC = re.compile(r"[а-яёқғҳўА-ЯЁҚҒҲЎ]")
UZBEK_LATIN = re.compile(r"[a-zA-Zʻ’'`]")
RUSSIAN_MARKERS = set("ыэъёыэ" )


def detect_language(text: str) -> str:
    """Lightweight language detection for the first test deployment."""
    lower = text.lower()
    if any(ch in lower for ch in "қғҳў") or " салом" in f" {lower}":
        return "uz_cyrillic" if UZBEK_CYRILLIC.search(text) else "uzbek"
    if any(ch in lower for ch in "ыэъё"):
        return "russian"
    if any(word in lower.split() for word in ("the", "what", "how", "hello", "please", "can")):
        return "english"
    if UZBEK_CYRILLIC.search(text):
        return "russian"
    return "uzbek"


def build_reply(text: str, language: str, first_name: str) -> str:
    """Safe deterministic test reply; AI provider will be added after deployment test."""
    if language == "russian":
        return (
            f"Здравствуйте, {first_name}! Я Smart Community Bot.\n\n"
            "Я получил ваш вопрос и готов помочь. Сейчас это тестовая версия. "
            "Скоро я смогу глубоко анализировать вопросы, работать с голосом, "
            "изображениями и управлять группой."
        )
    if language == "english":
        return (
            f"Hello, {first_name}! I am Smart Community Bot.\n\n"
            "I received your question and I am ready to help. This is currently "
            "a test version; AI analysis, voice, image, moderation and games will "
            "be connected in the next stage."
        )
    if language == "uz_cyrillic":
        return (
            f"Ассалому алайкум, {first_name}! Мен Smart Community Botман.\n\n"
            "Саволингизни қабул қилдим. Ҳозир бу тест версияси. Кейинчалик саволни "
            "чуқур таҳлил қилиш, овоз, расм, модерация ва ўйин функциялари қўшилади."
        )
    return (
        f"Assalomu alaykum, {first_name}! Men Smart Community Botman.\n\n"
        "Savolingizni qabul qildim. Hozir bu test versiyasi. Keyingi bosqichda "
        "savolni chuqur tahlil qilish, ovoz, rasm, moderatsiya va o‘yin funksiyalari "
        "ulanadi. Savolingizni yozishda davom eting."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "do‘st"
    await update.effective_message.reply_text(
        f"Salom, {name}! Men Smart Community Bot test versiyasiman.\n\n"
        "Savolingizni yozing. /yordam buyrug‘i orqali imkoniyatlarni ko‘ring."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Test buyruqlari:\n"
        "/start — botni ishga tushirish\n"
        "/yordam — yordam\n"
        "/til — til haqida\n"
        "/holat — bot holati\n\n"
        "Guruhda botni @mention qilib yoki xabariga reply qilib savol bering."
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Bot savol qaysi tilda berilganini aniqlab, javobni o‘sha tilda qaytarish uchun sozlanmoqda. "
        "O‘zbekcha lotin va kiril yozuvlari qo‘llab-quvvatlanadi."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Bot ishlayapti. Test server: Render Free.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return

    text = message.text.strip()
    is_private = update.effective_chat and update.effective_chat.type == ChatType.PRIVATE
    mentioned = f"@{context.bot.username}".lower() in text.lower() if context.bot.username else False
    is_reply_to_bot = bool(message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == context.bot.id)

    # In groups, only answer direct mentions or replies during the test phase.
    if not is_private and not mentioned and not is_reply_to_bot:
        return

    clean_text = re.sub(r"@\w+", "", text).strip()
    language = detect_language(clean_text)
    reply = build_reply(clean_text, language, user.first_name or "do‘st")
    await message.reply_text(reply)


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("yordam", help_command))
telegram_app.add_handler(CommandHandler("til", language_command))
telegram_app.add_handler(CommandHandler("holat", status_command))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))


@app.get("/")
async def health() -> dict:
    return {"status": "ok", "service": "smart-community-bot"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> dict:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN is not configured"}
    payload = await request.json()
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.on_event("startup")
async def startup() -> None:
    if BOT_TOKEN:
        await telegram_app.initialize()
        if PUBLIC_URL:
            await telegram_app.bot.set_webhook(url=f"{PUBLIC_URL}{WEBHOOK_PATH}")
        logger.info("Telegram webhook configured.")


@app.on_event("shutdown")
async def shutdown() -> None:
    if BOT_TOKEN:
        await telegram_app.shutdown()
