# SlotSaver

**An AI front-desk agent that rescues cancelled appointment slots.**

Every evening, "Asha" calls tomorrow's patients to confirm. When someone
cancels, she immediately calls the waitlist and fills the empty slot — the
call no human receptionist ever makes. Reminders are the commodity;
**backfill is the product.**

Clinic no-show rates run 15–30%. Every empty slot is paid-for chair time,
staff time and rent producing zero revenue — and nobody calls a waitlist at
9 PM to refill a slot that just opened for 11 AM tomorrow. The slot simply
dies. SlotSaver makes that call.

```
EVENING RUN
1. Read tomorrow's appointments + waitlist
2. Confirm-call every patient
   ├─ confirms         → CONFIRMED
   ├─ no answer        → retry once, then flag NEEDS-ATTENTION
   ├─ reschedules      → slot freed
   └─ cancels          → slot freed
3. Freed slot → call the waitlist in order until someone accepts
4. Morning report: X confirmed · Y cancelled · Z backfilled · ₹ recovered
```

## Architecture

```
Demo appointment sheet (fictional data — never real patient data)
        │
Strands Agents SDK  — the agent brain deciding which call to place next
        │  tool calls (all state changes live inside tools)
        │        └──→ board.json → live browser board (demo_board.html)
CALL-E  — real telephony: one API call per phone call, structured outcomes
        │
Morning report — slots recovered, rupees saved
```

- **Brain:** [AWS Strands Agents SDK](https://strandsagents.com) agent
  (`slotsaver/agent.py`), LLM served by Groq. A deterministic fallback brain
  (`slotsaver/engine.py`) runs the same loop with zero dependencies.
- **Telephony:** [CALL-E](https://heycall-e.com) via the official `calle-ai`
  SDK (`slotsaver/calle_caller.py`). Each call sends a natural-language task
  plus a JSON `result_schema`, so CALL-E returns the outcome
  (`confirmed` / `cancelled` / `reschedule` / `no_answer`) already structured —
  no transcript parsing.
- **Dry-run by default:** with no API keys set, everything runs on a scripted
  `MockCaller` — no call is ever placed, no account needed.

## What the calls sound like (persona: "Asha")

**Confirm call**

> "Hi, this is Asha calling from [clinic name]. Am I speaking with [patient]?
> I'm just confirming your appointment tomorrow at [time] with [doctor].
> Will you be able to make it?"

- Yes → "Great, we'll see you at [time]. Have a good evening!"
- Reschedule → "No problem — I'll free up that slot and the clinic will call
  you to rebook."
- Cancel → "Thanks for letting us know — I'll free up that slot."

**Backfill call (to the waitlist)**

> "Hi, this is Asha from [clinic name]. You asked us for an earlier
> appointment — a slot just opened tomorrow at [time] with [doctor].
> Would you like it?"

- Yes → "Done, you're booked for [time] tomorrow. See you then!"
- No → "No problem, we'll keep you on the list. Have a good evening!"

Asha introduces herself as the clinic's assistant up front, keeps every call
under ~45 seconds, never presses phone keys, and never waits on hold. The
outcome of one call decides whether the next happens — a cancellation is what
triggers the backfill call — so these are chained real calls, not one
scripted call.

## Setup — end to end

### 0. Prerequisites

- Python **3.11+**
- macOS / Linux shell (Windows: use WSL)

### 1. Accounts & keys (skip any you don't need)

| Account | Where | What you get | Needed for |
|---|---|---|---|
| CALL-E | [heycall-e.com](https://heycall-e.com) → sign up → Dashboard → Account → **API Keys** | `CALLE_API_KEY` (`iams_…`) + free trial calls | Real phone calls |
| Groq | [console.groq.com](https://console.groq.com) → **API Keys** | `GROQ_API_KEY` (`gsk_…`), free tier | The Strands agent brain |

No keys at all? Everything still runs in dry-run mode (step 4).

### 2. Install

```bash
git clone <this repo> && cd slotsaver
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Then fill in `.env`:

| Variable | Required? | Meaning |
|---|---|---|
| `CALLE_API_KEY` | For real calls | CALL-E dashboard API key. **Empty = mock caller, no calls placed.** |
| `ALLOWED_DEMO_PHONES` | For real calls | Comma-separated E.164 numbers real calls may dial — **only phones you own**. Empty = every real call refused. |
| `GROQ_API_KEY` | For agent brain | Groq key powering the Strands agent. Empty = deterministic engine only. |
| `GROQ_MODEL` | No | Default `openai/gpt-oss-120b`. |
| `GROQ_BASE_URL` | No | Any OpenAI-compatible endpoint (default: Groq). |
| `CLINIC_NAME` / `AGENT_PERSONA` / `DOCTOR_NAME` | No | Demo clinic branding (defaults: Dr. Meera's Dental Clinic / Asha / Dr. Meera). |
| `CALLE_REGION` / `CALLE_LOCALE` | No | Recipient region + call locale (defaults `IN` / `en-US`). |
| `CALLE_TIMEOUT_SECONDS` | No | Max wait per call (default 300). |
| `TEST_PATIENT_NAME` | No | The name `test_call` asks for (default: Rohit Sharma). |

`.env` is gitignored; keys never leave your machine.

### 4. Dry run — no calls, no keys, ₹0

```bash
# Deterministic engine brain
.venv/bin/python -m slotsaver.run_demo

# Strands agent brain (needs GROQ_API_KEY)
.venv/bin/python -m slotsaver.run_demo --brain strands
```

Both print the evening's call log, tomorrow's board, and the morning report
for a scripted scenario (a confirmation, a cancellation + backfill, a retry
after no-answer, a reschedule).

**Live demo board** — watch slots flip in the browser as calls land:

```bash
python3 -m http.server 8787          # terminal 1, from the repo root
open http://localhost:8787/demo_board.html
.venv/bin/python -m slotsaver.run_demo --slow 3   # terminal 2, paced run
```

Every run writes `board.json` after each state change; the board polls it
once a second. `--slow 3` paces mock calls ~3s apart so the board tells the
story at real-call speed.

**Self-playing demo** — `demo_board.html?demo=1` needs no backend at all: it
replays a real agent evening run on a loop (confirmations, a cancellation,
waitlist backfills, ₹1,600 recovered). That's what the hosted demo serves —
see [Deploying the hosted demo](#deploying-the-hosted-demo) below.

### 5. One real test call

```bash
# 1. Put YOUR OWN number in .env:  ALLOWED_DEMO_PHONES=+91XXXXXXXXXX
# 2. Place exactly one call (answer as the patient):
.venv/bin/python -m slotsaver.test_call +91XXXXXXXXXX
```

Asha calls you, confirms a fictional appointment, and the script prints the
structured outcome CALL-E returned.

### 6. The full loop with REAL calls

```bash
# rehearse the exact take first — same cast and order, ₹0, no calls:
.venv/bin/python -m slotsaver.run_demo --brain strands --take --slow 3

# then the real thing:
.venv/bin/python -m slotsaver.run_demo --brain strands --real +91XXXXXXXXXX
```

A trimmed 3-call cast where your allowlisted phone plays every patient —
answer as the first patient (confirm), then Priya (cancel), then Arjun
(accept the freed slot). It asks for a typed `yes` before dialing, spends
~3 calls of credit, and the live board updates as each call lands.

## Deploying the hosted demo

The self-playing board (`demo_board.html?demo=1`) is a static page — no
backend, no API keys needed at runtime — so it deploys as static assets on
[Cloudflare Workers](https://developers.cloudflare.com/workers/static-assets/).
`wrangler.jsonc` already points a Workers project (`slotsaver-board`) at a
`site/` assets directory and an optional custom domain.

```bash
# 1. One-time: authenticate wrangler with your Cloudflare account
npx wrangler login

# 2. Build the static site/ folder (gitignored, regenerated each deploy)
mkdir -p site
cp demo_board.html site/demo_board.html
cat > site/index.html <<'EOF'
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url=demo_board.html?demo=1">
<link rel="canonical" href="demo_board.html?demo=1"><title>SlotSaver</title>
</head><body><p>Redirecting to the
<a href="demo_board.html?demo=1">live demo board</a>…</p></body></html>
EOF

# 3. Deploy (uses wrangler.jsonc — project name, assets dir, custom domain)
npx wrangler deploy
```

`wrangler deploy` prints both live URLs on success: the `*.workers.dev`
subdomain and, if the custom domain's zone lives in your Cloudflare account,
the domain configured under `routes` in `wrangler.jsonc`. Re-run steps 2–3
any time `demo_board.html` changes — there's nothing to redeploy otherwise
(no server, no database, no scheduled build).

### Optional: a private "trigger a real call" button on the hosted page

`worker/index.js` turns the deploy from pure static hosting into a small
Worker (still serving `site/` via the `ASSETS` binding for everything else)
that adds two API routes so the hosted board can place one real CALL-E test
call without anyone running Python locally:

```
POST /api/call    { id, token } -> { call_id }          places the call
GET  /api/call     ?id&token    -> { status, outcome }  polled by the page
GET  /api/numbers  ?token       -> [{ id, label }]       masked number list
```

**Security model** — this endpoint can spend real call credit, so it's
designed so the only way to know it exists is to already hold the secret:

- The real phone numbers in `ALLOWED_DEMO_PHONES` **never reach the
  browser**. `/api/numbers` returns a masked label (`+9182••••404`) and a
  small integer `id`; the Worker maps that id back to the real number
  itself, server-side. A public page's HTML/JS source is always viewable by
  anyone regardless of what's hidden in the UI, so nothing sensitive is
  ever embedded in it — only referenced by an opaque id.
- Every route requires `token` to equal the `CALL_TRIGGER_TOKEN` secret,
  compared in constant time (hash-then-compare, so a wrong guess can't be
  timed to find out how much of it matched). No token = a flat `403` from
  every route, and the page renders identically to any other visitor —
  no calling UI, no hint the feature exists.
- `CALLE_API_KEY` lives only as a Worker secret; it's attached to the
  CALL-E request inside the Worker and never sent to or readable by the
  browser.
- A KV-backed cooldown (60s between calls — Cloudflare KV's own TTL floor) and daily cap (5/day) per token
  limit how much a mistake — or a leaked token — could spend.

**Setup** (after the deploy steps above):

```bash
# One-time: a KV namespace for the rate limiter
npx wrangler kv namespace create RATE_LIMIT
# → paste the printed { "binding": "RATE_LIMIT", "id": "..." } into
#   wrangler.jsonc under "kv_namespaces" (already done in this repo)

# Generate a private access token — treat it like a password
node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))"

# Push secrets (never written to wrangler.jsonc or git)
printf '%s' '<your CALL-E API key>'        | npx wrangler secret put CALLE_API_KEY
printf '%s' '+91XXXXXXXXXX,+91YYYYYYYYYY'  | npx wrangler secret put ALLOWED_DEMO_PHONES
printf '%s' '<the generated token>'        | npx wrangler secret put CALL_TRIGGER_TOKEN

npx wrangler deploy
```

Then visit the page **once** with `?key=<the generated token>` appended,
e.g. `https://slotsaver.kharidwise.com/demo_board.html?key=...` — the page
saves it to that browser's `localStorage` and immediately rewrites the URL
to drop the query param, so it isn't left sitting in the address bar,
browser history, or link previews. A "Trigger a real call" section then
appears with a button per allowlisted number. Bookmark the **plain** URL
(without `?key=`) afterwards; that browser already remembers the token.
Anyone you hand the `?key=...` link to can trigger real calls on your
credit (rate-limited, not unlimited) — share it the way you'd share a
password, or not at all.

### Real evening run — manual button, or fully automatic

The same unlocked page also has a **"Real evening run"** section: a
▶ **Start real evening run** button that places the whole confirm →
cancel → backfill sequence for real (the trimmed cast the local `--real`
flow uses), updating the board live as each call resolves. A 🧪 **Test the
flow** button next to it runs the identical UI/flow simulated, for
rehearsing safely first.

The same sequence can also run **fully unattended, on a schedule** — a
Cloudflare [Cron Trigger](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
(configured in `wrangler.jsonc`, ticking every evening) — but it's **off by
default**, gated behind a small ON/OFF toggle right on the page
("Automatic daily run (Cron): OFF/ON · turn on/turn off"). Flipping it
writes a flag to KV and takes effect immediately, no redeploy needed.
Turn it on when you actually want SlotSaver dialing your phone(s) on its
own every evening; leave it off for manual-only control.

## Safety & side effects

- **Real calls only ever reach allowlisted phones.** The demo data's numbers
  are fictional; `CalleCaller` refuses anything not in `ALLOWED_DEMO_PHONES`
  before touching the API.
- All schedule/waitlist mutations happen inside tools — the LLM sequences
  calls but cannot edit state directly.
- Idempotency keys (unique per run) are sent on every call-create, so a
  retry inside a run cannot double-dial the same person.
- No real patient data anywhere; the appointment sheet is fictional and a
  real deployment would read the clinic's own sheet.
- Calls cost trial credits: the test runner places exactly one call per
  invocation, and the demo scenario is rehearsable end-to-end on the mock.
- **Cancellation:** every real run asks for a typed `yes` before dialing and
  aborts placing zero calls otherwise. Ctrl-C between calls stops the run —
  calls are placed strictly one at a time, so at most the call currently in
  progress completes; nothing is queued or scheduled for later.

## Tests

```bash
.venv/bin/python -m unittest discover tests
```

Pure stdlib, no keys, no calls — exercises every branch of the evening loop
(confirm, cancel → backfill after a decline, no-answer → retry, reschedule)
on the MockCaller, including the exact 3-call demo scenario.

## Beyond clinics

The same evening loop fits any appointment business with a waitlist: salons,
physio courses ("session 6 of 10" nudges), diagnostic labs (fasting
reminders), tuition and driving schools. The demo shows a dental clinic;
swapping the sheet is all it takes.

## Project structure

```
slotsaver/
├── slotsaver/
│   ├── models.py        # Appointment, WaitlistEntry, ClinicState
│   ├── demo_data.py     # fictional clinic sheet
│   ├── config.py        # .env loading; mock-by-default switches
│   ├── caller.py        # Caller protocol + MockCaller (dry-run)
│   ├── calle_caller.py  # real calls via CALL-E (calle-ai SDK)
│   ├── engine.py        # deterministic fallback brain
│   ├── agent.py         # Strands agent brain (tools + policy prompt)
│   ├── board.py         # snapshots state to board.json for the live board
│   ├── run_demo.py      # the evening run: --brain engine|strands [--slow N]
│   └── test_call.py     # place exactly ONE real budgeted call
├── tests/test_engine.py # stdlib unittest suite for the evening loop
├── demo_board.html      # live browser board (serve via python -m http.server)
├── worker/index.js      # Cloudflare Worker: serves site/ + the token-gated /api/call routes
├── wrangler.jsonc       # Worker config: project name, assets dir, KV binding, custom domain
├── requirements.txt
├── .env.example
└── .dev.vars.example    # names of the Worker secrets (see "trigger a real call" above)
```

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `refusing to dial …: not in ALLOWED_DEMO_PHONES` | Add the number (E.164, `+91…`) to `ALLOWED_DEMO_PHONES` in `.env`. Working as designed. |
| Phone never rings, script waits then reports `no_answer` | CALL-E dials India via a shared international pool — carrier spam screening (Jio/Truecaller/"silence unknown callers") may eat the call. Whitelist unknown callers for the test, or try a phone on another carrier. Check the call's status in the CALL-E dashboard. |
| `--brain strands needs GROQ_API_KEY` | Set `GROQ_API_KEY` in `.env`, or use the default engine brain. |
| `CALLE_API_KEY is not set` from `test_call` | Real calls need the CALL-E key in `.env`. |
