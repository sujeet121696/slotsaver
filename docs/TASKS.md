# Tasks

> Progress tracker. Move items between sections as work happens.

_Last updated: 2026-10-13_

## In progress: `NextVersion`

Cleanup pass, all local. Not deployed: the live site stays on the judged
build (tag `hackathon-submission-2026-09-14`) until judging is over.

- [x] Remove a real phone number from a Worker comment (it stays in git history; the tag can't be rewritten)
- [x] Worker: per-run idempotency keys (a second same-day run used to get cached calls)
- [x] `wrong_person_or_unclear` outcome in Python and the Worker; failed or invalid results are flagged, never read as `no_answer`
- [x] One reschedule rule everywhere: no backfill (the agent prompt used to backfill)
- [x] `tools.py`: rule guards shared by both brains
- [x] Worker: evening runs count against the rate limit; no second real run while one is running
- [x] Tests for all of the above (Python: `tests/test_tools.py`)
- [x] Worker loop moved to `worker/evening.mjs` with `node --test` tests; first no-answer now flags the slot like Python, and the ≤2 calls per person guard is enforced
- [x] Cron autorun charged against the rate limit; retries and repeat offers charged one by one; a run stops after 5 failed steps in a row
- [x] Token sent in an `X-SlotSaver-Token` header on GET routes instead of the query string
- [x] Python: CALL-E SDK errors become `wrong_person_or_unclear` (a bad API key still stops the run); board push runs in the background; `--real` prints the worst-case call count
- [x] `npm run local`: test, clean, build (site/ + Worker dry run + login check), then the board with a mock run
- [x] `npm run local:tunnel`: same plus a public quick-tunnel link; the board server serves only `.serve/`, never the repo root
- [ ] Deploy with `npx wrangler deploy` once judging is over (rebuild `site/` first: the board and Worker changed together)
- [ ] Rehearse once with `--take`, then one real `--real` check
- [x] `VobizCaller`: Vobiz number via a Bolna agent behind `Caller`, chosen with `CALLER=calle|vobiz`; call wording shared in `providers/scripts.py`; tests in `tests/test_vobiz.py`
- [x] Real providers moved to `slotsaver/providers/` (`calle.py`, `vobiz.py`, shared `scripts.py`, `real_caller` factory)
- [x] `StopRun` (bad key, low balance) now stops the agent brain too: `EveningTools` refuses every later call
- [x] Bolna shapes checked against its docs (nested `extracted_data`, guardrail reschedules); local end-to-end test over HTTP against a fake Bolna server
- [ ] Vobiz: set up the Bolna agent (TELEPHONY.md §6), then one real `test_call` with `CALLER=vobiz`

## Backlog

| Item | Notes |
|---|---|
| Real clinic sheets | Google Sheets / Practo export behind the same `ClinicState` |
| Global-ready core | Business config (country, time zone, currency, language, type), consent guard in `tools.py`, voice enabled per country, per-time-zone scheduling, opt-out, approval mode with an audit log. See [PRD.md §8](PRD.md#8-next-version-global-ready-planned-not-built) |
| Vobiz in the Worker + router | Bolna `fetch` calls in the hosted Worker; then a router for both providers. See [TELEPHONY.md](TELEPHONY.md) |
| Multilingual calls | CALL-E locales, starting with Hindi |
| WhatsApp morning report | Send the report to the clinic owner |
| Rebooking flow | Handle reschedules instead of handing them back to the clinic |
| Second business type | Labs (home collection) or salons, chosen after the clinic pilots. Must work by config only, with no engine changes. Order in [PRD.md §7](PRD.md#7-business-types-planned-not-built) |

## Done

- [x] Evening loop: confirm → retry → reschedule/cancel → backfill → morning report
- [x] Deterministic brain + Strands agent brain (Groq)
- [x] Real CALL-E calls with allowlist, idempotency and typed-`yes` gate
- [x] Live board + self-playing `?demo=1` replay
- [x] Cloudflare Worker: hosted board, token-gated calls, real evening run, Cron toggle, board push
- [x] Tests for every loop branch + the 3-call demo scenario
- [x] Hackathon submissions: CALL-E and AWS Agents for Humans (2026-09-14)
- [x] SlotSaver skill merged into CALLE-AI/awesome-phone-call-agents (PR #542, 2026-10-08)
