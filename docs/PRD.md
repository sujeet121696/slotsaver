# Product Requirements

> What SlotSaver does and why. Update this when scope changes.

## 1. Problem

- Clinics lose **15–30%** of booked slots to no-shows and late cancellations.
- Reminder SMS is a commodity. The real loss is a slot that frees up in the evening and never gets refilled.
- Nobody calls the waitlist at 9 PM, so the slot goes unused.

## 2. Users

| User | Gets |
|---|---|
| Small-clinic owner (primary) | Evening calls handled without extra staff, plus ₹ recovered |
| Waitlisted patient | Earlier slots they would never have heard about |

## 3. Core flow: the evening run

1. Read tomorrow's appointments and the waitlist.
2. Call each patient to confirm:

   | Outcome | Result |
   |---|---|
   | Confirms | `confirmed` |
   | No answer | Retry once, then `needs_attention` |
   | Reschedules | `rescheduled`, slot freed |
   | Cancels | `cancelled`, slot freed |

3. For each freed slot, call the waitlist in order until someone accepts (`backfilled`).
4. Morning report: confirmed · cancelled · backfilled · ₹ recovered.

**Backfill is the product. Reminders are just the way in.**

## 4. Requirements

- **Zero-key mode:** runs end to end with a mock caller and the deterministic brain.
- **Safe real calls:** allowlisted numbers only, a typed `yes` before each run, one call at a time.
- **Tool-gated state:** the LLM chooses the order of calls, but only tools change state.
- **Live board:** slots change status as calls land. The `?demo=1` replay needs no backend.
- **Hosted controls:** token-gated real call and real evening run. Cron autorun is **off by default**.

## 5. Out of scope

- Real patient data or clinic system integrations (the data is fictional)
- Rebooking patients who reschedule (the clinic calls them back)
- Multi-day scheduling, payments, EHR/PMS
- Languages other than English

## 6. Success

The full loop runs on real phone calls: a real confirmation, a real cancellation and a real backfill, with ₹ recovered shown live on the board.
