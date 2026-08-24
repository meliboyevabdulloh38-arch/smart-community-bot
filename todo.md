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

## Ordered completion roadmap

- [ ] Phase 1: connect a real server-side AI conversation provider with Uzbek, Russian, and English language matching.
- [ ] Phase 2: add voice-to-text and image/OCR processing through optional server-side providers.
- [ ] Phase 3: move SQLite state from ephemeral `/tmp` to durable storage and expand moderation safeguards.
- [ ] Phase 4: add scheduled posts, forced-subscription checks, and richer admin analytics.
- [ ] Phase 5: prepare the separate MTProto/userbot voice-chat assistant integration.
- [ ] Phase 6: validate and deploy each phase without exposing secrets.

## Provider-ready media intake

- [ ] Add server-side transcription endpoint configuration for Telegram voice messages.
- [ ] Add server-side vision/OCR endpoint configuration for Telegram photos and screenshots.
- [ ] Download Telegram media only during request handling and enforce size/time limits.
- [ ] Return a clear Uzbek/Russian/English response when providers are not configured.
- [ ] Keep credentials in Render environment variables only and never log media or tokens.

## Scope decision

Voice-chat userbot/MTProto integration is paused by user request. Continue with non-voice features and validation.

## Non-voice hardening

- [ ] Verify Render environment-variable documentation against the deployed service.
- [ ] Validate scheduling and subscription checks with safe disabled-default behavior.
- [ ] Confirm the live health endpoint after the latest deployment.
- [ ] Keep voice-chat credentials and account setup out of the current deployment.

## Mandatory subscription fix

- [ ] Confirm the exact channel username or numeric channel ID and public invite link.
- [ ] Confirm that Dadasi is an administrator in the channel or group being checked.
- [ ] Add the channel settings to Render Environment Variables without exposing secrets.
- [ ] Redeploy and test `/obuna` with a subscribed and unsubscribed account.
- [ ] Support multiple required groups or channels instead of only one configured chat.
- [ ] Keep a per-chat invite link so the user sees exactly which subscriptions are missing.
- [ ] Let group admins add the current group with an Uzbek bot command.
- [ ] Let group admins list and remove required groups with Uzbek bot commands.
- [ ] Make `/obuna` check every administrator-configured required group.
- [ ] Accept a target group or channel username/ID in `/majburiy_qosh` from the current admin group.
- [ ] Accept a target group or channel username/ID in `/majburiy_ochir` from the current admin group.
- [ ] Confirm the bot can access the target chat and return a clear permission error if it cannot.

## Timed mandatory subscriptions

- [ ] Accept 6-hour, 12-hour, and 24-hour expiry values when adding a target group.
- [ ] Accept a specified clock time as an expiry value.
- [ ] Remove expired required chats automatically before `/obuna` checks.
- [ ] Show expiry times in `/majburiy_royxat` and document the commands.

## Mandatory-subscription analytics

- [ ] Record successful `/obuna` checks with chat, user, and timestamp.
- [ ] Aggregate unique users for today, seven days, and all time.
- [ ] Aggregate per-required-chat pass counts without exposing user identities.
- [ ] Add Uzbek admin-only `/obuna_statistika` output and include a short summary in `/statistika`.
- [ ] Store display name, username, and timestamp for users who successfully run `/obuna`.
- [ ] Add admin-only `/obuna_kimlar` roster output with pagination or a safe limit.
- [ ] Add optional per-group roster filtering without exposing lists to ordinary members.
- [ ] Remove the 100-user roster truncation from storage and reporting.
- [ ] Add 40-user pagination for `/obuna_kimlar` and optional group/page arguments.
- [ ] Show the current page and total pages to admins.
- [ ] Make `/obuna_kimlar` automatically send every roster entry from one command.
- [ ] Split long reports into Telegram-safe messages without dropping entries.
- [ ] Preserve optional target-group filtering for the one-command report.

## Next hardening pass

- [ ] Review the SQLite path and startup behavior for Render restarts.
- [ ] Add safe database-directory creation and clear health metadata for storage mode.
- [ ] Review admin command error messages and avoid repeated responses on webhook retries.
- [ ] Validate the live deployment after the hardening pass.
