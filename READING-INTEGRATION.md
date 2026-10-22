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

## Outstanding integration work

1. Verify Railway deploys the current branch after its deployment pause ends; verify the subscriber endpoint rejects anonymous requests and supports authenticated admin import.
2. Remove historical dashboard credentials from PWA URLs and rotate the exposed dashboard token. Changing source alone cannot revoke an already exposed credential.
3. Repair and test the legacy calendar sync before consolidating calendars. Current code imports untagged events, contrary to the export brief. Its datetime conversion is defective; missing appointments can also cause event deletion. Do not enable a new shared-calendar integration based on the brief's safety claims.
4. The old standalone /generate UI needs an explicit supported workflow before activation. Use the live Squarespace reading form meanwhile.
5. New form submissions currently enforce consent in the browser but do not send a consent field to the backend; record consent server-side before relying on automated ongoing subscriber synchronisation.

## Validation

Inline JavaScript syntax checked with Node; Python source parsed successfully. The main Squarespace reading block matches the captured live page exactly. Homepage script recovered from the live site rather than overwriting it with the older export. No readings generated, appointments changed, calendar sync invoked or emails sent during this source integration.
