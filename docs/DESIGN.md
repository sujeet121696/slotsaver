# Design

> How the board looks and how Asha sounds. Update this when UI or call wording changes.

## 1. Board principles

- **Dark ops console:** it should read like a live control room.
- **Status is the story:** each slot's colour change is the main visual event.
- **₹ recovered is the headline:** always the most prominent number.
- **One static file:** no framework and no build step, so it deploys anywhere.

## 2. Colour tokens

| Token | Value | Use |
|---|---|---|
| `--bg` | `#0e1116` | Page |
| `--card` | `#171c24` | Cards |
| `--line` | `#262d38` | Borders |
| `--text` | `#e6e9ee` | Body text |
| `--dim` | `#8b94a3` | Secondary text |

| Status | Colour |
|---|---|
| scheduled | `#8b94a3` grey |
| confirmed | `#34c47c` green |
| cancelled | `#e5534b` red |
| backfilled | `#b78bfa` purple |
| rescheduled | `#58a6ff` blue |
| needs_attention | `#e3a008` amber |

## 3. Layout

- System font stack. The ops log is monospace and shows the last 12 lines.
- Breakpoints at 1000px and 480px. Below 480px the slot grid becomes one column.

## 4. Board modes

| Mode | Trigger | Data source |
|---|---|---|
| Local live | Default | Polls `board.json` every second |
| Hosted live | On the Worker | `/api/board` |
| Replay | `?demo=1` | Built-in recorded run, no backend |
| Private controls | `?key=<token>` once | Token saved to localStorage and removed from the URL. Hidden otherwise |

## 5. Call persona: Asha

- Introduces herself as the clinic's assistant. Warm and brief, under about 45 seconds.
- **Confirm:** "Hi, this is Asha calling from [clinic]… confirming your appointment tomorrow at [time] with [doctor]. Will you be able to make it?"
- **Backfill:** "…a slot just opened tomorrow at [time] with [doctor]. Would you like it?"
- A decline is always polite: "We'll keep you on the list."
- Never presses keys, never waits on hold.
- Clinic, persona and doctor names come from env, so the demo can be re-skinned for other businesses without code changes.
