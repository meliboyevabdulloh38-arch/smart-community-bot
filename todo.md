# Smart Community Bot — Upgrade Checklist

- [ ] Replace the starter-only reply flow with a maintainable multilingual command and message architecture.
- [ ] Support Uzbek Latin, Uzbek Cyrillic, Russian, and English responses with language detection.
- [ ] Add Uzbek admin commands for warn, mute, ban, unban, kick, stats, filter, summary, settings, and scheduled-post management.
- [ ] Add group moderation filters, welcome messages, basic anti-spam protection, and admin permission checks.
- [ ] Add lightweight gamification and group-safe games without requiring a database for the first deployment.
- [ ] Add idempotent update handling so webhook retries and restarts do not duplicate responses.
- [ ] Add optional AI provider integration only through server-side environment variables; do not hard-code credentials.
- [ ] Add safe extension points for voice transcription, image/OCR analysis, scheduled posts, analytics, and the separate voice-chat assistant.
- [ ] Preserve Render Free compatibility and document which advanced features need extra services or secrets.
- [ ] Run syntax, import, health, and webhook-path validation locally.
- [ ] Commit and upload the upgraded source to the private GitHub repository.
- [ ] Redeploy on Render and verify the health endpoint and startup logs.
- [ ] Provide the user with only a short Telegram test checklist.
