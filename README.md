# Time & Place — MVP v0.1

A single-user conversational scheduling pilot. Google Calendar remains the schedule view. Intentions become proposals; only explicit approval writes an event.

## Installing the downloaded ZIP

Install Python 3.12 or newer from https://www.python.org/downloads/ first. On Windows, enable **Add Python to PATH** if the installer offers it. Extract the entire ZIP to a normal folder; do not run scripts inside the compressed ZIP.

- **Windows:** double-click `Install-Windows.bat` once. When it finishes successfully, double-click `Start-Windows.bat`.
- **Mac:** open `Install-Mac.command` once, then `Start-Mac.command`. If macOS blocks a downloaded script, you can use Terminal and the commands below instead; do not disable system security settings.

Keep the server window open and open `http://localhost:8000` in your browser. Choose **Explore the local demo**. To stop the app, press Ctrl+C in the server window. Later, use only the Start script; reinstall after dependency updates. Dependencies download from PyPI during installation. Your demo data stays on your computer under `.local`.

The included `tzdata` package supplies timezone rules on Windows, where a system timezone database may be unavailable. The launcher scripts are provided for convenience; automated validation has been run on Linux, not on Windows or macOS.

## Run the local demo

Python 3.12+ is required. From the existing checkout (no worktree needed):

```sh
cd /workspace/Timeandplace
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m app.server
```

On your own machine, open `http://localhost:8000` and choose **Explore the local demo**. This uses isolated example events stored in SQLite; it never accesses Google. Complete the five-message interview. Try:

```text
מחר אני צריך להתקשר לאחי
לשאול על הדוגמה
```

Choose **Find a better fit** and reply `הוא בעבודה עד 16:00`. Review the revised proposal, then approve. **Close the day** lets you confirm completed achievements or propose moving an existing event. Every move requires a fresh approval and retains the same event ID and notes. When no capacity exists, nothing moves or accumulates.

The server listens on loopback by default. Set `HOST=0.0.0.0` only for a trusted container/reverse proxy. `APP_URL` must equal the browser origin, including port; its default is `http://localhost:8000`. For HTTPS deployment, set `APP_URL` to the HTTPS origin and terminate TLS at a reverse proxy. The development server is not a hardened public deployment server. Health: `GET /health`.

## Enable real Google Calendar later

The real OAuth and Calendar transport is already implemented, separate from demo storage. No application rewrite is needed to enable it.

1. Create a Google Cloud project and enable **Google Calendar API**.
2. Configure the Google Auth Platform consent screen. Keep publishing status **Testing**, choose an External audience if appropriate, and add your pilot Google account as a test user.
3. Create an OAuth client with application type **Web application**.
4. Register the exact redirect URI `http://localhost:8000/auth/callback` for local use, or `<APP_URL>/auth/callback` for your HTTPS deployment. Changing port or hostname requires a matching registered URI. No JavaScript origins are required for this server-side flow.
5. Supply `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` through secure environment configuration, not source control or chat. Set `APP_URL` as above. Restart the server and choose **Connect Google Calendar**. Approve calendar-event permissions.
6. The first verified Google account becomes the single pilot owner. Later accounts are rejected. Existing demo data is isolated and is not imported into your real calendar.

Scopes: `openid`, `email`, `https://www.googleapis.com/auth/calendar.events`. Outbound HTTPS destinations: `accounts.google.com` (browser), `oauth2.googleapis.com` (token exchange/refresh), `openidconnect.googleapis.com` (identity), and `www.googleapis.com` (Calendar). Refresh tokens in Google testing mode can expire after seven days; reconnect when requested. The integration reads the **primary calendar only**, expands recurring instances and paginates events. Use the sync button for immediate refresh; the browser also polls every minute and when focused. External moves, edits and cancellations reconcile into stored achievements. Approved writes update Google. No calendar grid is built here.

Live Google sign-in/read/write has **not** been validated without a real OAuth client. Tests exercise the transport contract with controlled provider responses; they do not prove Google consent or network access in your account.

## Optional AI interpretation

The local demo works without an LLM. Basic mode recognizes English/Hebrew today/tomorrow, ISO dates, minutes, morning/evening and `after 16:00` style constraints. It is deliberately limited and labeled in the interface. Use 24-hour times. It stores interview context but does not automatically turn narrative constraints into recurring commitments. Put fixed commitments in Google Calendar and include exact activity windows in requests.

To enable richer extraction, set `AI_API_KEY` securely and optionally `AI_MODEL` (default `gpt-4o-mini`). The server calls `https://api.openai.com/v1/chat/completions` with a structured-output schema. This sends the current request and profile context to OpenAI; calendar event contents are not sent. AI output still passes deterministic constraints and explicit approval. No live AI call has been validated without a key.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
```

Tests include actual HTTP requests with cookie sessions, onboarding, user isolation, CSRF, proposals, approval conflict rechecks, idempotent approval, notes, same-event rescheduling, pure scheduling rules and controlled Google transport responses. All tests use a temporary database.

An optional Chromium/Playwright smoke test exercises the actual interface, Hebrew correction, approval, daily close, rescheduling and a mobile viewport:

```sh
# Keep browser tooling outside the project; Chromium must be installed.
npm install --prefix /tmp/timeplace-browser --cache /tmp/timeplace-npm-cache playwright@1.58.2 --no-audit --no-fund
mkdir -p .local
NODE_PATH=/tmp/timeplace-browser/node_modules node tests/browser.cjs
```

Start the app first. Set `CHROMIUM_PATH` or `TEST_URL` if needed. Screenshots go into ignored `.local/`.

## Data and limits

Architecture decisions are recorded in [docs/decisions.md](docs/decisions.md), before the corresponding implementation.

`.local/app.db` persists profiles, conversation, achievements, proposals and sessions. Google tokens are encrypted using `.local/token.key`; protect and back up both together. `.local` is ignored by Git. `DATA_DIR` can relocate runtime state. Never commit tokens, keys, database files or `.env`. For local data reset, stop the server and remove only your own runtime directory; this also clears the pilot-owner binding. Signing out terminates the browser session but does not revoke Google authorization. Revoke access through Google Account security settings if desired.

The first slice protects sleep/work windows, calendar conflicts (including all-day events), 15-minute transitions, a two-hour unscheduled daily reserve and a maximum of eight commitments. Personal activities can use waking hours outside work hours. Scheduling searches the next seven days; unsupported constraints should be clarified rather than assumed. It will refuse a move when no slot fits. Automated displacement chains, proactive daily notifications, travel routing, automatic recurring achievement creation, multiple calendars, and multi-user administration are deferred. Resource compatibility is a tested engine capability; external events remain blocking because the pilot has no explicit UI for resource annotations yet.
