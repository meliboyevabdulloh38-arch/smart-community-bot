# Smart Community Bot — Render test version

This is the first Render-compatible test version of the Smart Community Bot. It uses FastAPI as a health-check/webhook service and python-telegram-bot for Telegram updates.

## Render settings

Use a Render Web Service with:

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Environment variable `BOT_TOKEN`: the token from BotFather
- Environment variable `WEBHOOK_SECRET`: a long random secret; Render can generate this automatically

Do not commit the Telegram token to GitHub or place it in source code. Add it only in Render Environment Variables.

## Current test commands

- `/start` — start the bot
- `/yordam` — show help
- `/til` — language behavior
- `/holat` — service status

In a group, mention the bot or reply to one of its messages. During this test stage, the bot does not answer every group message.

## Next modules

The production roadmap includes the AI answer engine, automatic language detection, Latin/Cyrillic Uzbek support, voice transcription, image and screenshot analysis, Uzbek admin commands, moderation, games, scheduled posts, analytics, idempotent message delivery, and the separate voice-chat assistant account.
