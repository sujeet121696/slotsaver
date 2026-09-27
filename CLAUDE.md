# SlotSaver

> AI front-desk agent ("Asha") that confirms tomorrow's clinic appointments by phone and calls the waitlist to refill cancelled slots.

## Docs

| File | Read when |
|---|---|
| [docs/PRD.md](docs/PRD.md) | You need to know what it does, who it's for, and what's out of scope |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | You're changing code: stack, flow, modules, Worker routes |
| [docs/RULES.md](docs/RULES.md) | **Always**, before touching calls, secrets or git |
| [docs/DESIGN.md](docs/DESIGN.md) | You're changing the board UI or call wording |
| [docs/TASKS.md](docs/TASKS.md) | You're picking up work or recording progress |

`README.md` is the user-facing setup guide.

## Commands

```
.venv\Scripts\python.exe -m unittest discover tests              # tests (no keys, no calls)
.venv\Scripts\python.exe -m slotsaver.run_demo                   # dry run, deterministic brain
.venv\Scripts\python.exe -m slotsaver.run_demo --brain strands   # dry run, agent brain (needs GROQ_API_KEY)
npx wrangler deploy                                              # hosted board (build site/ first, see README)
```

## Top rules

- Never commit, push or open a PR. The owner commits manually.
- Never place real calls. Suggest the command instead.
- Never copy anything from `docs/SUBMISSION.md` or `backup/` into tracked files. They are private, gitignored notes.
