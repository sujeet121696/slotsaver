# Rules

> Rules for anyone (human or AI) changing this repo. Update this when a new rule is agreed.

## 1. Git

- **Never commit, push or open a PR.** The owner commits manually.
- Leave changes in the working tree and summarise what changed.

## 2. Real calls

Real calls cost money and ring real phones.

- Mock is the default. A real call needs `CALLE_API_KEY` **and** a number in `ALLOWED_DEMO_PHONES`.
- Never run `test_call` or `--real` yourself. Suggest the command instead.
- Keep the typed-`yes` gate. Calls go one at a time, and nothing is queued.
- Idempotency keys must be unique per run (CALL-E dedupes them forever).
- Call tasks never tell the bot to press keys or wait on hold. Calls stay under about 45 seconds.

## 3. Hosted Worker

- Real numbers never reach the browser. The browser gets a masked label and an opaque id only.
- Compare tokens in constant time. Without a token, the page shows no hint of the controls.
- Keep the rate limits: 60s cooldown, 5 calls a day per token.
- Cron autorun stays **off by default**.

## 4. Data and secrets

- Fictional data only. No real names, phone numbers or emails in tracked files, since the repo is public.
- Keys live only in `.env` or Worker secrets. Never in code, `wrangler.jsonc` or logs.
- Never copy anything from `docs/SUBMISSION.md` or `backup/` into tracked files.

## 5. Code

- State changes only inside tools or engine functions. The LLM only chooses the order of calls.
- A change to the evening loop must be made in both Python and the Worker's JavaScript.
- Keep `strands`/`calle` imports lazy so the dry run works without packages.
- Match the existing style: small modules, type hints, docstrings that explain *why*.
- `demo_board.html` stays one self-contained file with no build step.

## 6. Workflow

- Run the tests after any loop change, and add a test for each new branch.
- Update `README.md` when setup changes, and `docs/` when scope or architecture changes.
- Windows: use `.venv\Scripts\python.exe`. If `--brain strands` fails with a `pydantic_core` ABI error, recreate `.venv`.
