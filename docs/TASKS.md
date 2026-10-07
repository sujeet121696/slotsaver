# Tasks

> Progress tracker. Move items between sections as work happens.

_Last updated: 2026-10-06_

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
- [ ] Deploy with `npx wrangler deploy` once judging is over
- [ ] Rehearse once with `--take`, then one real `--real` check

## Backlog

| Item | Notes |
|---|---|
| Real clinic sheets | Google Sheets / Practo export behind the same `ClinicState` |
| Multilingual calls | CALL-E locales, starting with Hindi |
| WhatsApp morning report | Send the report to the clinic owner |
| Rebooking flow | Handle reschedules instead of handing them back to the clinic |
| Other verticals | Salons, physio, diagnostic labs, tuition, driving schools |

## Done

- [x] Evening loop: confirm → retry → reschedule/cancel → backfill → morning report
- [x] Deterministic brain + Strands agent brain (Groq)
- [x] Real CALL-E calls with allowlist, idempotency and typed-`yes` gate
- [x] Live board + self-playing `?demo=1` replay
- [x] Cloudflare Worker: hosted board, token-gated calls, real evening run, Cron toggle, board push
- [x] Tests for every loop branch + the 3-call demo scenario
- [x] Hackathon submissions: CALL-E and AWS Agents for Humans (2026-09-14)
