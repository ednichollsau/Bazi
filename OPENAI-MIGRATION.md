# OpenAI reading migration

Prepared 22 October 2026. This change replaces Anthropic with OpenAI for prose in both /reading and the authenticated /generate preview. Existing calculation code, treatment rules, prompt text, email template, recipient handling and reading response fields remain unchanged.

## Configuration and release

- Required server secret: OPENAI_API_KEY, entered directly in Railway service web → Variables. Never commit or paste it into chat.
- Optional OPENAI_READING_MODEL, default gpt-6-astra. Model access has NOT been verified for this account. Confirm it using a fictional preview before rollout.
- Responses API, low reasoning effort, 4,000 output-token cap, 60-second timeout, no automatic SDK retries, store=false. This disables API response storage; it is not a promise of zero provider retention.
- Missing credentials produce a generic 503. Failed, empty or incomplete output produces a generic 502 before any email or patient write. There is no fallback to Claude.
- The existing prompt receives name and calculated pillars/elemental analysis, not email or raw birth-date fields. The requested provider change means that prompt will be sent to OpenAI when activated.

Railway currently has ANTHROPIC_API_KEY but no OPENAI_API_KEY (only variable names inspected). It still reports deployments paused due to an upstream issue. Keep this migration unmerged until the new secret is configured and deployment is available. Do not remove the old key as part of this code-only preparation.

After configuration: test a fictional reading without email, review its prose/section format and latency, then merge and deploy. Verify the deployed commit and authenticated preview; check the public reading-to-Resend path with a separately authorized test recipient. Live OpenAI output quality, account access and cost are not yet verified. Mock tests cannot establish those.

## Validation

22 automated tests pass. New checks cover OpenAI prompt/model/storage parameters, missing credentials, incomplete output, empty output, provider failures with sanitized errors, and prevention of email delivery after generation failure. Existing generator, public reading delivery, authentication and calendar regression tests also pass. No real OpenAI call or email was made.

Official documentation: https://developers.openai.com/api/docs/guides/text and https://developers.openai.com/api/docs/models/gpt-6-astra.
