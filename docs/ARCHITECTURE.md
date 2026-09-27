# Architecture

> How SlotSaver is built. Update this when modules, routes or the stack change.

## 1. Stack

| Layer | Tech |
|---|---|
| Agent brain | AWS Strands Agents SDK, LLM on Groq (any OpenAI-compatible endpoint) |
| Fallback brain | Plain Python, no dependencies |
| Telephony | CALL-E (`calle-ai`): task + JSON `result_schema` gives structured outcomes |
| Live board | Single static `demo_board.html` |
| Hosting | Cloudflare Worker + static assets + KV + Cron Trigger |
| Tests | stdlib `unittest` |

## 2. Flow

```
demo_data.py ──► ClinicState
                     │
      brain: agent.py (Strands)  or  engine.py (deterministic)
                     │  state changes only through tools
                     ▼
      Caller: MockCaller (default)  or  CalleCaller (real, allowlisted)
                     │
      on_change ──► board.py ──► board.json  (+ optional push to Worker)
                     │
               morning report
```

## 3. Python modules (`slotsaver/`)

| File | Role |
|---|---|
| `models.py` | `SlotStatus`, `Appointment`, `WaitlistEntry`, `ClinicState` (with the `on_change` hook) |
| `demo_data.py` | Fictional clinic sheet (full scenario + 3-call real cast) |
| `config.py` | `.env` loading, mock-by-default switches |
| `caller.py` | `Caller` protocol + scripted `MockCaller` |
| `calle_caller.py` | Real calls: allowlist check, create and poll, per-run idempotency keys |
| `engine.py` | Deterministic evening loop |
| `agent.py` | Strands agent: `get_worklist`, `confirm_call`, `offer_slot`, `get_morning_report` |
| `board.py` | Atomic `board.json` writes, opt-in push (`BOARD_PUSH_URL` + `BOARD_PUSH_TOKEN`) |
| `run_demo.py` | CLI entry: `--brain`, `--slow N`, `--take`, `--real <phone>` |
| `test_call.py` | Places exactly one real call |

`strands` and `calle` are imported lazily, only when that brain or caller is used, so the dry run needs no packages.

## 4. Cloudflare Worker (`worker/index.js`)

- Serves `site/` through the `ASSETS` binding.
- KV `RATE_LIMIT` holds cooldowns, the autorun flag and evening-run state.
- `scheduled()` drives the Cron autorun (window set in `wrangler.jsonc`).

| Route | Purpose |
|---|---|
| `GET /api/numbers` | Masked allowlist (label + opaque id) |
| `POST/GET /api/call` | Place one real call / poll its outcome |
| `POST/GET /api/board` | Receive / serve a live board snapshot |
| `GET /api/evening/preview` | Cast for the evening run, nothing called yet |
| `GET/POST /api/evening/autorun` | Read / toggle the Cron autorun |
| `POST /api/evening/start`, `/step` | Run the evening sequence (supports `dryRun`) |

Every route needs the `CALL_TRIGGER_TOKEN`. Without it, the route returns a flat `403`.

> ⚠️ The evening loop exists twice: in Python (`engine.py`/`agent.py`) and in JavaScript (the Worker). Keep them consistent.

## 5. Config and secrets

| Where | What |
|---|---|
| `.env` (template `.env.example`) | Local keys, allowlist, branding, board push |
| Worker secrets (names in `.dev.vars.example`) | `CALLE_API_KEY`, `ALLOWED_DEMO_PHONES`, `CALL_TRIGGER_TOKEN` |
| Gitignored, generated | `site/`, `board.json` |
