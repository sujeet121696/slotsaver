# Product Requirements

> What SlotSaver does and why. Update this when scope changes.

## 1. Problem

- Published studies put appointment no-show rates at roughly **10–42%**, depending on specialty and setting ([PMC review](https://pmc.ncbi.nlm.nih.gov/articles/PMC11231932/)). There's no reliable India-wide figure yet, so pilots must measure it.
- Reminder SMS is a commodity. The real loss is a slot that frees up in the evening and never gets refilled.
- Nobody calls the waitlist at 9 PM, so the slot goes unused.

## 2. Users

| User | Gets |
|---|---|
| Small-clinic owner (primary) | Evening calls handled without extra staff, plus ₹ recovered |
| Waitlisted patient | Earlier slots they would never have heard about |
| Other appointment businesses (later) | The same thing for their slots: labs, salons, service centres and more (§7) |

## 3. Core flow: the evening run

1. Read tomorrow's appointments and the waitlist.
2. Call each patient to confirm:

   | Outcome | Result |
   |---|---|
   | Confirms | `confirmed` |
   | No answer | Retry once, then `needs_attention` |
   | Wrong person, or no usable result | `needs_attention` for a human. No retry, slot not freed |
   | Reschedules | `rescheduled`, slot freed (the patient keeps a future booking, so no backfill) |
   | Cancels | `cancelled`, slot freed |

3. For each cancelled slot, call the waitlist in order until someone accepts (`backfilled`). An unclear offer call pauses that slot's backfill for a human.
4. Morning report: confirmed · cancelled · backfilled · ₹ recovered.

**Backfill is the product. Reminders are just the way in.**

## 4. Requirements

- **Zero-key mode:** runs end to end with a mock caller and the deterministic brain.
- **Safe real calls:** allowlisted numbers only, a typed `yes` before each run, one call at a time.
- **Tool-gated state:** the LLM chooses the order of calls, but only tools change state, and the tools enforce the rules (waitlist order, cancelled slots only, at most 2 calls per person).
- **Outcomes are proposals:** on a real clinic schedule, a change needs CALL-E's call record plus a human's approval. The demo applies them only to fictional data.
- **Live board:** slots change status as calls land. The `?demo=1` replay needs no backend.
- **Hosted controls:** token-gated real call and real evening run. Cron autorun is **off by default**.

## 5. Out of scope

- Real patient data or clinic system integrations (the data is fictional)
- Rebooking patients who reschedule (the clinic calls them back)
- Multi-day scheduling, payments, EHR/PMS
- Languages other than English

## 6. Success

The full loop runs on real phone calls: a real confirmation, a real cancellation and a real backfill, with ₹ recovered shown live on the board.

## 7. Business types (planned, not built)

Clinics come first, but they aren't the only market. The core fits any business that sells time slots and loses money when one goes empty. Each new type should need **config only** (scripts, rules, channel), never engine changes. The second type is the test of that.

| Order | Type | Value | Channel |
|---|---|---|---|
| 1. Now | Private clinics (dental, dermatology, physio, eye) | High-value slots; informal waitlists already exist | WhatsApp → voice |
| 2. Next | Labs with home sample collection | A missed visit wastes a technician's trip; confirming has value even with no waitlist | WhatsApp → voice |
| 2. Next (alternative) | Salons and spas without booking software | Many businesses, quick decisions | WhatsApp only |
| 3 | Car service and test drives; field service (installs, repairs, pest control) | High value per booking, or a wasted trip | Voice / WhatsApp → voice |
| 4 | Pay-at-property homestays and hotels; classes, coaching, courts and turf | Confirm or release the booking; waitlists are common | WhatsApp |
| Later | Interview scheduling, retail holds, restaurants | Unusual cases, tested only once the core is stable | Varies |

Not a fit: cheap slots with no replacement demand (contact costs eat the value), and anything promotional (discounts, upsells). Calls must stay service calls (TRAI in India, the TCPA in the US).

## 8. Next version: global-ready (planned, not built)

Anyone, anywhere can sign up. Voice calling is turned on one country at a time, only once it's compliant there.

| Requirement | What it means |
|---|---|
| **Business config** | Each business sets its country, time zone, currency, languages and type (clinic, lab, salon, …), and gets that type's call scripts and rules. The engine and guards stay the same for every type |
| **Consent guard** | The engine refuses to contact a number with no recorded consent for that channel. This replaces the demo allowlist and is enforced in the tools, like the other guards |
| **Channel by country** | WhatsApp is available wherever the business has opted-in contacts. Voice is on only where a compliance pack exists (consent, calling hours, caller ID, opt-out, number series) |
| **Provider by country** | A call provider per country behind `Caller` (CALL-E, Vobiz, …; see [TELEPHONY.md](TELEPHONY.md)) |
| **Per-time-zone scheduling** | Each business's evening run happens in its own local time, not on one global Cron |
| **Opt-out** | "Stop" on any channel is recorded at once and respected by every run |
| **Approval mode** | New businesses approve each change; trusted ones can turn on auto-apply. Every change is recorded in an audit log |

Why consent comes first:
- US: under an FCC ruling (Feb 2024), AI-voice calls need prior express consent under the TCPA ([Hunton](https://huntonak.com/privacy-and-information-security-law/fcc-issues-declaratory-ruling-that-tcpa-applies-to-ai-generated-voice-calls)).
- India: TRAI number-series rules and the DPDP Rules apply.
- Health data adds HIPAA (US) and GDPR special-category rules (EU).
- **Abroad, start with non-health businesses.**
