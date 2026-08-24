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

## Debugging `/start` response

- [ ] Inspect Render startup logs for webhook initialization or Telegram API errors.
- [ ] Check Telegram `getWebhookInfo` without exposing the bot token.
- [ ] Verify that the deployed webhook URL and secret path match the application route.
- [ ] Reproduce a safe local update-processing path without sending test messages.
- [ ] Apply the smallest fix, redeploy, and verify the live endpoint.
- [ ] Ask the user to retry `/start` only after the deployment is confirmed live.

## Automatic group conversation mode

- [ ] Remove the mention/reply-only gate for ordinary group text messages.
- [ ] Keep commands, admin messages, duplicate updates, and anti-spam handling safe.
- [ ] Preserve a concise response policy so every group message does not create an excessively long reply.
- [ ] Document that Telegram Privacy Mode must be disabled or the bot must be an administrator to receive all group messages.
- [ ] Validate and redeploy the automatic participation mode.

## Real conversational replies

- [ ] Audit why `maybe_ai_reply` falls back to generic templates in production.
- [ ] Choose an available server-side AI integration path that does not expose credentials.
- [ ] Add conversational context, language matching, and concise group reply behavior.
- [ ] Preserve a useful non-AI fallback that reflects the user’s actual message instead of repeating the same template.
- [ ] Validate and redeploy the conversational update.

## Question-classification correction

- [ ] Treat “nima haqida suhbatlashamiz?” as an open conversation prompt, not a definition request.
- [ ] Add a natural Uzbek response that proposes several discussion topics and invites a choice.
- [ ] Check equivalent Russian and English open-question patterns.
- [ ] Validate and redeploy the correction.
- [ ] Retest with the exact user sentence from the group.

## Natural direct-answer style

- [ ] Answer the user’s concrete question first, before asking anything back.
- [ ] Use friendly everyday Uzbek instead of formal “fikringizni tushundim” phrasing.
- [ ] For book questions, provide several concrete titles or categories and one-sentence reasons.
- [ ] Keep any follow-up to one short choice question.
- [ ] Apply the same conversational style to Russian and English fallback replies.
