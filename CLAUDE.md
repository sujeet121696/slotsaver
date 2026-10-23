# SlotSaver

> AI front-desk agent ("Asha") that confirms tomorrow's clinic appointments by phone and calls the waitlist to refill cancelled slots.

## Docs

| File | Read when |
|---|---|
| [docs/PRD.md](docs/PRD.md) | You need to know what it does, who it's for, and what's out of scope |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | You're changing code: stack, flow, modules, Worker routes |
| [docs/RULES.md](docs/RULES.md) | **Always**, before touching calls, secrets or git |
| [docs/TELEPHONY.md](docs/TELEPHONY.md) | You're adding or changing a call provider (CALL-E, Vobiz, a router) |
| [docs/DESIGN.md](docs/DESIGN.md) | You're changing the board UI or call wording |
| [docs/TASKS.md](docs/TASKS.md) | You're picking up work or recording progress |

`README.md` is the user-facing setup guide.

## Commands

```
npm run local                                                    # test → clean → build → board + mock run (mock only)
npm run local:tunnel                                             # same, plus a public trycloudflare.com link
npm test                                                         # both test suites
.venv\Scripts\python.exe -m unittest discover tests              # tests (no keys, no calls)
node --test tests/worker_evening.test.mjs                        # Worker loop tests (no keys, no calls)
.venv\Scripts\python.exe -m slotsaver.run_demo                   # dry run, deterministic brain
.venv\Scripts\python.exe -m slotsaver.run_demo --brain strands   # dry run, agent brain (needs GROQ_API_KEY)
npx wrangler deploy                                              # owner only: hosted board (build site/ first, see README)
```

## Top rules

- Never commit, push or open a PR. The owner commits manually.
- Never place real calls or deploy. Suggest the command instead.
- Never copy anything from `docs/SUBMISSION.md` or `backup/` into tracked files. They are private, gitignored notes.
