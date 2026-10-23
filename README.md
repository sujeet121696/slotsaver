<p align="center"><img src="assets/logo.svg" alt="SlotSaver" width="280"></p>

# SlotSaver

**An AI front-desk agent that fills cancelled appointment slots.**

Every evening, "Asha" phones tomorrow's patients to confirm. When someone cancels, she calls the waitlist straight away and fills the empty slot. Reminders are everywhere; **refilling the slot is the product.**

> Demo data is fictional. No real patient data is used anywhere.

## How it works

```
EVENING RUN
1. Read tomorrow's appointments and the waitlist
2. Call each patient to confirm
   ├─ confirms                → CONFIRMED
   ├─ no answer               → retry once, then NEEDS ATTENTION
   ├─ wrong person / unclear  → NEEDS ATTENTION (no retry, slot kept)
   ├─ reschedules             → slot freed (the clinic rebooks them)
   └─ cancels                 → slot freed
3. Freed slot → call the waitlist in order until someone accepts
4. Morning report: confirmed · cancelled · refilled · ₹ recovered
```

| Part | What it does | File |
|---|---|---|
| Brain | [Strands Agents](https://strandsagents.com) on [Groq](https://console.groq.com), or a deterministic engine with no dependencies | `slotsaver/agent.py`, `slotsaver/engine.py` |
| Guards | The only code that changes state: cancelled slots only, waitlist order, at most 2 calls per person | `slotsaver/tools.py` |
| Calls | `MockCaller` by default; real calls through [CALL-E](https://heycall-e.com), allowlisted numbers only | `slotsaver/caller.py`, `slotsaver/providers/calle.py` |
| Live board | Browser board that updates as each call finishes | `demo_board.html` |
| Hosting | Cloudflare Worker: board, token-protected call routes, Cron run | `worker/` |

More detail: [Architecture](docs/ARCHITECTURE.md) · [Product](docs/PRD.md) · [Call wording](docs/DESIGN.md) · [Rules](docs/RULES.md) · [Telephony options](docs/TELEPHONY.md)

## Quick start (no keys, no calls, ₹0)

Needs Python 3.11+ and Node 20+.

```bash
git clone https://github.com/<you>/slotsaver && cd slotsaver
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
npm install
npm run local
```

`npm run local` runs the tests, cleans, builds, then opens a board at http://127.0.0.1:8790/demo_board.html and plays a mock evening run.

| Command | What it does |
|---|---|
| `npm run local` | test → clean → build → board + mock run |
| `npm run local:tunnel` | Same, plus a public `*.trycloudflare.com` link (needs [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)) |
| `npm run local -- --brain strands` | Same, using the agent brain (needs `GROQ_API_KEY`) |
| `npm test` | Python and Worker tests (no keys, no calls) |
| `npm run build` | Rebuild `site/`, check the Worker bundle (dry run), check Cloudflare login |
| `npm run clean` | Remove generated files |
| `python -m slotsaver.run_demo` | Terminal-only mock run |

Notes:
- `npm run local` only does mock runs. It refuses `--real` and never deploys.
- The board server serves only `.serve/`, never the repo root, so a tunnel link can't expose `.env`.
- If port 8790 is in use, run `PORT=8791 npm run local`.

## Configure

```bash
cp .env.example .env
```

| Variable | Needed for | Notes |
|---|---|---|
| `CALLER` | Real calls | `calle` (default) or `vobiz` |
| `CALLE_API_KEY` | Real calls on CALL-E | Empty = mock caller, no calls placed |
| `BOLNA_API_KEY`, `BOLNA_AGENT_ID`, `VOBIZ_FROM_NUMBER` | Real calls on Vobiz | Only with `CALLER=vobiz`. Set up the Bolna agent first ([docs/TELEPHONY.md §6](docs/TELEPHONY.md#6-vobiz-via-bolna-setup)) |
| `ALLOWED_DEMO_PHONES` | Real calls | Comma-separated E.164 numbers you own. Empty = every real call is refused |
| `GROQ_API_KEY` | Agent brain | Empty = deterministic engine only |
| `CLINIC_NAME`, `AGENT_PERSONA`, `DOCTOR_NAME` | Branding | Optional |
| `CALLE_REGION`, `CALLE_LOCALE`, `CALLE_TIMEOUT_SECONDS` | Call settings | Optional, defaults `IN` / `en-US` / `300` |

`.env` is gitignored. Every option is listed in [.env.example](.env.example).

## Real calls (costs credit)

Put your own number in `ALLOWED_DEMO_PHONES` first. Run these with `.venv` activated.

```bash
python -m slotsaver.test_call +91XXXXXXXXXX                              # exactly one call
python -m slotsaver.run_demo --brain strands --take --slow 3              # rehearse the take, no calls
python -m slotsaver.run_demo --brain strands --real +91XXXXXXXXXX         # 3-call real run
```

In the real run, your phone plays each patient in turn: confirm, then cancel, then accept the freed slot. It asks you to type `yes` before dialing and prints the worst-case call count (3 if everyone answers, up to 6 with retries).

## Telephony providers

| Provider | Status | Notes |
|---|---|---|
| Mock | ✅ Built | Default; scripted outcomes |
| [CALL-E](https://heycall-e.com) | ✅ Built | A complete calling agent: task + JSON schema in, structured outcome out |
| [Vobiz](https://vobiz.ai) | 🧪 Built, not yet tried on a real call | Indian telephony (numbers, SIP, Voice API) with a [Bolna](https://www.bolna.ai/integrations/vobiz) voice agent on top. `CALLER=vobiz`; setup in [docs/TELEPHONY.md §6](docs/TELEPHONY.md#6-vobiz-via-bolna-setup) |
| Several at once | 📝 Planned | A router behind the same `Caller` interface that picks a provider per call |

Every provider sits behind the `Caller` interface (`slotsaver/caller.py`), so the brain and guards never change. The comparison, India compliance notes and the recommended path are in [docs/TELEPHONY.md](docs/TELEPHONY.md).

## Deploy the hosted board (owner only)

```bash
npx wrangler login
npm run build
npx wrangler deploy
```

`?demo=1` replays a recorded evening run and needs no backend. The Worker also has token-protected routes for one real call, a full real evening run and a Cron autorun (off by default):
- The token goes in the JSON body for POST and in the `X-SlotSaver-Token` header for GET.
- Phone numbers never reach the browser.
- Calls are limited by a 60-second cooldown and 5 a day.

Secrets are set with `npx wrangler secret put` (names in [.dev.vars.example](.dev.vars.example)). See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#4-cloudflare-worker-worker) for the routes.

## Safety

- Real calls reach only allowlisted numbers, and real runs need a typed `yes`.
- The LLM decides the order of calls but can't edit state; only the guarded tools can.
- A failed or unclear call is flagged for a human, never guessed.
- Per-run idempotency keys stop a retry from double-dialing.
- Keys live only in `.env` or Worker secrets.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `refusing to dial …: not in ALLOWED_DEMO_PHONES` | Add the number (E.164) to `ALLOWED_DEMO_PHONES`. This is working as designed. |
| Phone never rings, outcome `no_answer` | CALL-E calls India from a shared international pool, which spam filters may block. Allow unknown callers, try another carrier, or see the Vobiz option in [docs/TELEPHONY.md](docs/TELEPHONY.md). |
| `--brain strands needs GROQ_API_KEY` | Set `GROQ_API_KEY`, or use the default engine. |
| `port 8790 is already in use` | `PORT=8791 npm run local` |

## Beyond clinics

Clinics come first, not only. The same loop fits any business that sells time slots: diagnostic labs (home collection), salons, car service, field service, homestays, classes and courts. The plan is one engine with a config per business type; see [PRD §7](docs/PRD.md#7-business-types-planned-not-built). Today the demo is a dental clinic.

## References

- Agent brain: [Strands Agents](https://strandsagents.com), [Groq](https://console.groq.com)
- Telephony: [CALL-E](https://heycall-e.com), [Vobiz docs](https://vobiz.ai/docs/), [Bolna × Vobiz](https://www.bolna.ai/integrations/vobiz), [Pipecat](https://github.com/pipecat-ai/pipecat), [LiveKit Agents](https://github.com/livekit/agents)
- Hosting: [Workers static assets](https://developers.cloudflare.com/workers/static-assets/), [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/), [Quick tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
- India calling rules: [TRAI amendment (Securiti)](https://securiti.ai/india-spam-rules-trai-latest-amendment/), [SIP providers for AI calling (Edesy)](https://edesy.in/blog/sip-trunk-providers-india-ai-calling-2026)
