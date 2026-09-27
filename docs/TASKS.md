# Tasks

> Progress tracker. Move items between sections as work happens.

_Last updated: 2026-09-27_

## In progress: `NextVrsion`

- [ ] _Define the next version's scope here_

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
