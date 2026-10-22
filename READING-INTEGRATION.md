# Reading system source integration — 22 October 2026

## Source of truth

The root Python files are the current Bazi application source. The Claude export's backend matches these files, except api_server.py: retain the newer authenticated reading-subscriber export endpoint in this repository. Do not replace the root server with the older exported server.

- squarespace/squarespace_earseed_code_block.html: exported reading form; exact match within the captured live /your-cosmology page.
- squarespace/homepage_popup.html: current live homepage script, captured 22 October. The live version builds its styles and form on demand and is newer than the export.
- reference/homepage-popup-export.html: older popup, for comparison only.
- reference/standalone-generator.html: old standalone generator. It calls POST /generate, which the current server does not provide. It also lacks the email/consent flow used by /reading. It is intentionally not routed or published as a working page.
- Root dashboard, icons, calculator, database, protocol and prompts already match the export and were retained.

The full 31-file export, including older v3 code, original brief and diagnostic logs, is preserved privately in the local project workspace, with a SHA-256 inventory. Logs and historical credentials must not be published to this public repository.

## Connected services

Squarespace reading form and homepage popup call Railway /reading. The server generates the reading, saves it in Railway PostgreSQL and already sends reading emails through Resend. Supabase and the Booking-Platform repository own current bookings, package credits and the opted-in subscriber list. Railway's older appointments table is a separate legacy system, not the authoritative booking calendar.

Fifteen unique existing reading subscribers were imported into the booking subscriber list on 22 October, preserving existing unsubscribe decisions. The authenticated bulk-import endpoint is prepared in this repository but was not live at the last check: Railway explicitly paused deployments due to an upstream incident. No campaign was sent.

## Repairs prepared after integration

The standalone generator is now /generator, linked from the practitioner console. POST /generate requires practitioner authentication and shares the existing calculation/model code. It deliberately does not email, save patients or add subscribers. Public /reading retains its existing response and Resend delivery flow. HTML rendering in the new generator escapes names and generated text.

Calendar reconciliation now compares both sides with the last agreed times, handles timezone offsets using Europe/London, rejects ambiguous/nonexistent naive times, paginates events, uses stable event IDs and database locks, and reports conflicting changes instead of guessing. It does not import unrelated events or delete orphan events. Google deletions and clinic cancellations are not propagated automatically; resolve these in the authoritative booking admin. The implementation still targets the legacy Railway appointment table. It must NOT be enabled as a substitute for the current Supabase booking calendar.

Automatic and manual legacy sync require GCAL_SYNC_ENABLED=true; the default is disabled. Leave it disabled until a current-booking adapter with availability validation, cancellation/refund semantics, patient update emails and an end-to-end live calendar test is implemented. No calendar writes were made during this repair.

Hardcoded dashboard-token URLs were removed from both manifests and the service worker. Old ena-* browser caches are purged when the worker updates; authenticated data and pages are no longer cached. Dashboard API calls now use Bearer headers, and URL tokens are no longer interpolated into executable HTML. Existing bookmark-token import remains for compatibility.

## Deployment and remaining requirements

Railway still explicitly reports "Deploys have been paused due to an upstream issue" on 22 October. The old deployment remains active. These changes are prepared source, not verified production behaviour. Once deployments resume:

1. Deploy current main and verify /health, /dashboard and authenticated /generator. Check the subscriber endpoint gives 401 anonymously and succeeds for the booking administrator.
2. Rotate the previously exposed DASHBOARD_TOKEN in Railway and sign in again. Source removal cannot revoke a previously exposed credential. The user must enter and submit the replacement credential when using browser UI.
3. Import reading-form opt-ins through the booking admin, confirming deduplication preserves unsubscribes; the 15 historical addresses are already imported.
4. Record new form consent server-side before enabling ongoing automated subscription sync. Current form enforcement is browser-only.

## Validation

19 automated tests pass, covering both clock changes, different UTC offsets for the same appointment, ambiguous/nonexistent local times, edits in each direction, simultaneous-edit conflicts, missing/deleted calendar events, pagination, disabled sync, practitioner authentication, valid generation with real calculations and mocked prose, invalid dates, anonymous subscriber rejection, and the public reading delivery flow with mocked email/database writes.

JavaScript syntax checks pass for dashboard, generator and worker. An isolated browser check signed in, opened the generator and rendered a fictional result with the real calculation engine and mocked prose. No patient records, real appointments, calendar events or external emails were created. This verifies software wiring; it is not clinical validation of the inherited protocol rules.

Calendar reconciliation uses Google's documented private extended properties and conditional event updates: https://developers.google.com/workspace/calendar/api/guides/extended-properties and https://developers.google.com/workspace/calendar/api/v3/reference/events/patch.

## Prepared OpenAI migration

The separate OpenAI migration replaces prose generation for both endpoints; see OPENAI-MIGRATION.md for activation requirements. Railway still needs OPENAI_API_KEY and a successful deployment. Do not describe the production service as switched until verified.
