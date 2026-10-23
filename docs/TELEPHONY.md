# Telephony providers

> Which phone provider places Asha's calls, and how to support more than one.
> Status today: **CALL-E is the tested real provider.** Option B (Vobiz via Bolna)
> is built as `VobizCaller` (`slotsaver/providers/vobiz.py`, `CALLER=vobiz`) with
> mocked-API tests, but has **not placed a real call yet**. Options C and D are
> still a plan.
>
> Facts are from the linked sources, checked 2026-10-14. Prices and limits change,
> so re-check them before you budget. Anything marked **to verify** wasn't confirmed.

## 1. The two providers are different kinds of product

| | CALL-E | Vobiz |
|---|---|---|
| What it is | A whole calling agent: you send a task in plain language plus a JSON `result_schema`, and it makes the call and returns a structured result ([heycall-e.com](https://heycall-e.com)) | **Telephony only**: phone numbers, SIP trunks, a Voice API and WebSocket audio streaming. It has no AI of its own; the conversation runs on a separate agent platform ([vobiz.ai](https://vobiz.ai), [AI voice agent guide](https://vobiz.ai/docs/guides/ai-voice-agent)) |
| Who does speech-to-text, the LLM and text-to-speech | CALL-E | You or an agent platform (Bolna, Vapi, Retell, ElevenLabs, Ultravox, Pipecat, LiveKit) ([Vobiz integrations](https://vobiz.ai/docs/integrations)) |
| Structured outcome | Built in (`result_schema`) | You build it, e.g. as an LLM tool call or by extracting from the transcript after the call |
| India | Calls India through a shared international number pool; carrier spam filters can block them (README troubleshooting). Hindi support **to verify** | Indian provider; claims "TRAI + DLT compliant", INR billing and GST invoices ([vobiz.ai](https://vobiz.ai), [docs](https://vobiz.ai/docs/)) |
| Concurrency | 1 call on the shared pool, up to 10 on a purchased number ([heycall-e.com](https://heycall-e.com)) | "High-concurrency channels", no number given ([vobiz.ai](https://vobiz.ai)) |
| Pricing | Billed in 10-second increments, plus a success fee charged only when the call's goal is met. The per-minute rate isn't published ([heycall-e.com](https://heycall-e.com)) | Pay per minute. The public page lists only US rates, e.g. $0.0115/min outbound local and $0.0034/min per WebSocket stream. **India rates: to verify** ([vobiz.ai/pricing](https://vobiz.ai/pricing)) |
| KYC | Needed for outbound calls ([heycall-e.com](https://heycall-e.com)) | eKYC when you provision numbers ([vobiz.ai](https://vobiz.ai)); Bolna requires KYC before selling a Vobiz number ([Bolna × Vobiz](https://www.bolna.ai/integrations/vobiz)) |
| API | `calle-ai` Python SDK (already used in `slotsaver/providers/calle.py`) | REST: `POST /api/v1/Account/{auth_id}/Call/` with `from`, `to` and `answer_url`; headers `X-Auth-ID` and `X-Auth-Token` ([docs](https://vobiz.ai/docs/)) |

**What follows:** "use Vobiz instead of CALL-E" really means "Vobiz plus a voice agent". Replacing CALL-E means replacing its AI half too.

## 2. Why Vobiz matters for India

- Calls to Indian numbers have to enter and leave the phone network through DoT-licensed operators, and domestic VoIP dial-out to Indian phones is restricted ([Edesy guide](https://edesy.in/blog/sip-trunk-providers-india-ai-calling-2026)). **To verify with Vobiz:** which licensed carrier your calls terminate on.
- TRAI separates number series: **140** for promotional calls and **160/1600** for service and transactional calls. Mixing promotional content into a call makes the whole call promotional ([Securiti](https://securiti.ai/india-spam-rules-trai-latest-amendment/), [Rootle](https://rootle.ai/blog/the-february-2025-trai-amendment-voice-ai-compliance/)). Bolna's page says 140-series Vobiz numbers need DLT registration first ([Bolna × Vobiz](https://www.bolna.ai/integrations/vobiz)).
- A local, compliant number is also the practical fix for the "phone never rings" problem: calls from an international pool get screened as spam.

**Open legal question:** is a waitlist offer ("a slot opened, do you want it?") a service call or a promotional one? Get advice before scaling. Until then, keep every script free of upsells.

## 3. Four ways to wire it

| # | Option | How it works | Good | Watch out |
|---|---|---|---|---|
| A | **CALL-E only** (default) | `CalleCaller` | Built, tested, simplest | Calls India from an international pool (spam screening), 1 concurrent call on the shared pool, no Indian number series |
| B | **Vobiz + a hosted agent platform** (e.g. Bolna). **Built:** `VobizCaller`, see §6 | A caller that starts a call on the platform, which dials through a Vobiz number | Fastest route to India. Both sides document the integration ([Bolna × Vobiz](https://www.bolna.ai/integrations/vobiz), [Vobiz docs](https://vobiz.ai/docs/integrations/bolna)) | Two bills (minutes plus platform fee). Whether the platform returns a structured outcome is **to verify**. Keypad (DTMF) input isn't supported on Vobiz in Bolna yet |
| C | **Vobiz + our own pipeline** (Pipecat or LiveKit Agents) | Vobiz SIP or WebSocket → our server running STT + LLM (Groq, as today) + TTS. The LLM ends the call with a `record_outcome` tool call ([Pipecat](https://vobiz.ai/docs/integrations/pipecat), [LiveKit](https://vobiz.ai/docs/integrations/livekit)) | Full control: Indian-language speech models, our own guards and outcome schema, lowest cost per minute at scale | The most engineering: latency, barge-in, hosting a long-running server (it can't run on a Worker) |
| D | **Several providers behind a router** | A `RoutingCaller` implements the same `Caller` protocol and chooses a provider for each call | Pick per country and cost, fail over when a provider is down, A/B test quality | Fail over **only if the call was never placed**. Once a call has connected, a retry on another provider would ring the person twice |

## 4. Rules every provider adapter must follow

These match the existing guards (`docs/RULES.md`), so the engine stays unaware of which provider it's using:

1. Implement the `Caller` protocol in `slotsaver/caller.py` (`confirm_call` and `offer_call` return a `CallOutcome`).
2. Return only the existing outcomes. A failed call or a result with no valid outcome becomes `wrong_person_or_unclear`, never `no_answer` or `cancelled`. A rejected API key or an empty balance raises `StopRun` (`slotsaver/caller.py`), which stops the run.
3. Check `ALLOWED_DEMO_PHONES` before contacting the provider.
4. Idempotency: CALL-E takes an idempotency key. Bolna's `POST /call` has none, so `VobizCaller` counts the call before dialing and never re-sends a start that failed or timed out. Whether Vobiz itself has one is **to verify**.
5. Every dial counts against the same rate limit and daily cap, whatever the provider.
6. Keys go only in `.env` or Worker secrets, e.g. `VOBIZ_AUTH_ID` and `VOBIZ_AUTH_TOKEN` (proposed names).
7. Keep the call wording (`docs/DESIGN.md`) the same on every provider: the same persona, under 45 seconds, no keypad presses.

## 5. Recommended path

1. **Now:** keep CALL-E for the demo. It works and is what the hackathon judged.
2. **India pilot:** option **B** (Vobiz + a hosted agent platform) behind a new `Caller`, chosen with a `CALLER=calle|vobiz` setting. Measure answer rate, outcome accuracy and cost per call against CALL-E on the same scripts.
3. **At scale:** move to option **C** if per-minute costs or language quality call for it.
4. **Both together:** option **D**, routing by destination country and failing over only on "call not placed".
5. **Worker:** the Worker reaches CALL-E with `fetch`. For B it would call the platform's REST API the same way. C needs its own server, with the Worker only starting the run.

Before any of this: get Vobiz's India rate card, its terminating carrier and its idempotency support, and a legal view on 140 vs 160/1600 for backfill offers.

## 6. Vobiz via Bolna: setup

`VobizCaller` (`slotsaver/providers/vobiz.py`) talks only to Bolna's REST API with stdlib `urllib`: `POST /call` starts the call, then it polls `GET /executions/{id}` until an end status. Auth is the `Authorization` header with the Bearer scheme. Bolna dials through the Vobiz number linked in its dashboard. Facts below checked against Bolna's docs on 2026-10-23.

One-time setup in the Bolna dashboard:

1. Link or buy a Vobiz number in Bolna. Bolna requires KYC before selling one ([Bolna × Vobiz](https://www.bolna.ai/integrations/vobiz)).
2. Create an agent whose prompt is only the `task` variable. SlotSaver sends Asha's full wording (`slotsaver/providers/scripts.py`, shared with CALL-E) in `user_data.task`, so both providers say the same thing. Bolna's docs show both `{task}` and `{{task}}`; use whichever the prompt editor detects (detected variables show up as inputs in its test panel) ([make a call](https://www.bolna.ai/docs/api-reference/calls/make), [prompting guide](https://www.bolna.ai/docs/prompting-guide)).
3. Add an extraction: category **`SlotSaver`**, extraction **`Outcome`**, with the predefined values `confirmed`, `cancelled`, `reschedule`, `accepted`, `declined`, `no_answer`, `wrong_person_or_unclear`, and free text on for notes. Bolna returns it as `extracted_data.SlotSaver.Outcome.objective` (the picked value) and `.subjective` (the free text) ([extraction output](https://www.bolna.ai/docs/prompting/extraction-output.md)). The names must match exactly; anything else reads as `wrong_person_or_unclear`.
4. Keep **calling guardrails off** (Bolna's default). With them on, a call outside the window is rescheduled for later, which would ring a patient after the run has moved on. SlotSaver flags such a call as `wrong_person_or_unclear` straight away ([calling guardrails](https://www.bolna.ai/docs/calling-guardrails)).
5. SlotSaver never sends `retry_config` (Bolna's auto-retry): the engine decides retries.
6. Put `CALLER=vobiz`, `BOLNA_API_KEY`, `BOLNA_AGENT_ID` and, optionally, `VOBIZ_FROM_NUMBER` in `.env`. Without `VOBIZ_FROM_NUMBER`, Bolna uses the account's default number.

How a Bolna execution becomes an outcome ([get execution](https://www.bolna.ai/docs/api-reference/executions/get_execution)):

| Bolna says | SlotSaver outcome |
|---|---|
| `completed`, with a valid `SlotSaver > Outcome` value | That outcome |
| `no-answer`, `busy`, or `answered_by_voice_mail` | `no_answer` (the phone rang and no person spoke) |
| `scheduled` or `rescheduled` | `wrong_person_or_unclear` at once (may ring later) |
| `failed`, `error`, `stopped`, `canceled`, no valid outcome, no end status before the timeout, or a failed start | `wrong_person_or_unclear` |
| HTTP 401/403, or `balance-low` | Stops the run (`StopRun`: bad API key, or top up Bolna) |

`call-disconnected` is not an end status: Bolna is still filling in `extracted_data`, so polling continues.

**Tested locally:** `tests/test_vobiz.py` runs a fake Bolna server on 127.0.0.1 and plays the 3-call `--real` cast through the real HTTP code. The fake follows the docs, so it can't catch where the docs and the live API differ.

**To verify on the first real call:** that a real execution matches the documented shapes above (status names, the nested `extracted_data`), and that the agent prompt picks up the `task` variable.

Not done yet: the hosted Worker still calls only CALL-E. Adding Bolna there is a `fetch` to the same two endpoints; worth doing once a real call has confirmed the shapes.

## Sources

- CALL-E: [heycall-e.com](https://heycall-e.com)
- Vobiz: [site](https://vobiz.ai), [docs](https://vobiz.ai/docs/), [AI voice agent guide](https://vobiz.ai/docs/guides/ai-voice-agent), [integrations](https://vobiz.ai/docs/integrations), [pricing](https://vobiz.ai/pricing), [Capterra listing](https://www.capterra.in/software/1110211/Vobiz)
- Bolna: [Vobiz integration](https://www.bolna.ai/integrations/vobiz), [make a call](https://www.bolna.ai/docs/api-reference/calls/make), [get execution](https://www.bolna.ai/docs/api-reference/executions/get_execution), [extraction output](https://www.bolna.ai/docs/prompting/extraction-output.md), [calling guardrails](https://www.bolna.ai/docs/calling-guardrails), [API auth](https://www.bolna.ai/docs/api-reference/introduction)
- India calling rules: [Edesy: SIP trunk providers for AI calling](https://edesy.in/blog/sip-trunk-providers-india-ai-calling-2026), [Securiti: TRAI amendment](https://securiti.ai/india-spam-rules-trai-latest-amendment/), [Rootle: TRAI Feb 2025 and voice AI](https://rootle.ai/blog/the-february-2025-trai-amendment-voice-ai-compliance/)
- Frameworks: [Pipecat](https://github.com/pipecat-ai/pipecat), [LiveKit Agents](https://github.com/livekit/agents)
