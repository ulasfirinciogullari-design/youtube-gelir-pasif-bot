# Production spend control: staged dispatch guard, OFF by default

The reservation primitive is now connected to the current paid provider
submission sites. `STUDIO_SPEND_ENFORCEMENT=false` preserves the existing
provider contract without reading or initializing the budget ledger. This
change does NOT enable production enforcement, initialize Redis, purchase
credits, generate media, resume failed jobs or publish anything.

When enabled, unpriced requests are BLOCKED, not treated as free. The reviewed
catalog below is intentionally small: do not enable a complete paid pipeline
until its required routes have bounded, tested quotes and verified job context.

## Implemented contract

- Actual Celery IDs establish task context, propagated separately into every
  narration/critic worker thread. Stored parent jobs determine the original
  channel, connected account and cost family. A derived Short retains its long
  parent's allowance; repair task IDs do not create new money.
- The channel must still be registered with the same connected account.
  Immutable bindings in the non-expiring ledger detect later ancestry or
  channel/kind changes. Missing/cyclic/expired job ancestry fails closed.
- A deterministic digest of provider, operation and payload identifies one
  submission across the family. Only an acknowledged atomic reservation may
  reach the create transport. The ledger stores no prompt, media or credential.
  A timeout does not refund or authorize replay; existing accepted-operation
  polling and artifact checkpoints remain unchanged.
- OpenAI and Runway SDK automatic create retries are disabled when enforcement
  is on. OpenAI text requests and Gemini JSON requests receive output bounds.
  Existing paid HTTP creates pass through the same pre-dispatch check.
- `GET /studio/youtube/production-budget` requires normal owner authentication,
  is non-caching and read-only, and distinguishes disabled/active/blocked state.
  Its counters are reserved upper bounds, NOT settled invoices or an all-in
  household/subscription spending cap. No initialization/refund/resume API is
  exposed by this change.
- Explicit operator-side `SpendLedger.initialize_reconciled` can seed a new
  ledger with audited current-month paid intents. It atomically imports the
  full reserved upper bounds into month/day/channel/lineage counters and the
  same request replay fences, with an audit digest. It neither calls providers
  nor grants a submission permit. Existing or conflicting ledgers cannot be
  overwritten, even after a lost initialization reply. Over-limit prior usage
  stays recorded and blocks additional spending.

## Reviewed initial catalog (8 September 2026)

Quotes expire before 1 October 2026 UTC. Unknown fields, models, unsupported
endpoints and unbounded requests block. Prepaid balances are not cash discounts.

| Route | Bounded request | USD list-rate upper bound |
| --- | --- | --- |
| OpenAI GPT-6 Astra | Tool-free plain text, standard tier, at most 100,000 encoded JSON bytes, at most 16,384 output tokens | $10/M input and $50/M output; conservative input bound is encoded JSON bytes + 4,096 framing tokens |
| Gemini 3.1 Pro Preview | One tool-free text-only JSON candidate, optional inline schema, at most 100,000 encoded JSON bytes and 16,384 total output tokens | $2/M input and $12/M output including thinking; encoded bytes + 4,096 framing keeps the input bound below the 200,000-token price threshold |
| Runway Gen-4.5 | Text-to-video, supported 720p ratios, 2–10 s, no generated audio | $0.12/s |
| Runway Seedance 2 Fast | Same bounded request family | $0.29/s, including when selected as a fallback |
| Veo 3.1 Lite | One text-to-video sample, 4/6/8 s | $0.05/s at 720p; $0.08/s at 1080p |
| Veo 3.1 Fast | Same bounded request family | $0.10/s at 720p; $0.12/s at 1080p |
| Veo 3.1 | Same bounded request family | $0.40/s at 720p or 1080p |

Sources: [OpenAI pricing](https://developers.openai.com/api/docs/pricing),
[Responses limits](https://developers.openai.com/api/reference/python/resources/responses/methods/create),
[Runway pricing](https://docs.dev.runwayml.com/guides/pricing/),
[Gemini/Veo pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini generation limits](https://ai.google.dev/api/generate-content),
[Google's combined thinking/output token guidance](https://codelabs.developers.google.com/bigquery-generative-ai-intro#3).
The OpenAI output ceiling includes reasoning tokens. Provider-supported model
duration restrictions still apply; a cost quote does not imply model acceptance.

The current audio/TTS/music, Gemini grounding/multimodal critic/image/Omni,
OpenAI vision/search and fal calls are guarded but have NO commissioned quote
yet. They therefore block with enforcement on. The Gemini JSON route permits
only the named model and bounded text contract, including schema/settings;
media, tools, custom tier headers, unknown fields and other models still block.
This covers the existing tool-free director and text story-critic request shapes;
it does not permit a multimodal/audio review through a text-only price quote.
Next-series planning also needs a persisted planning cost family and a grounded
request quote before it can operate in that mode. ElevenLabs base list rates
alone do not bound the selected voice: custom voice multipliers require separate
verified rate evidence ([ElevenLabs custom-rate documentation](https://elevenlabs.io/docs/help-center/product/voices/voice-library/what-are-custom-rates-and-credit-multipliers)).
A shared-voice bookmark and
Gemini file-upload transport are not model-generation charges; they are the
only explicit free-POST exclusions in the source coverage test.

## Financial scope

- Proposed combined-channel cash envelope: $500/month, not a spending target.
- Proposed variable API allowance: approximately $260/month. Fixed bills,
  infrastructure, asset/music allowances, taxes and contingency stay outside
  that API ledger but INSIDE the $500 envelope.
- A valid all-zero policy prohibits paid dispatch while free local editing
  and accepted-operation polling remain possible. Legitimate free quotas or
  prepaid credits require provider-specific quota/cash accounting; their expiry
  must NOT automatically enable a paid route. No repeated fake trial accounts.
- Included subscription credits and actual cash overage need separate
  reconciliation. This ledger counts reserved upper bounds, not invoices.
- Remaining-period initialization must account for existing/in-flight usage.
  It must never silently reset the whole current month to $0 already spent.
- Existing personal ChatGPT Pro/Google subscriptions and their extra credit
  purchases are separate; this application cannot cap purchases in those apps.

## Required commissioning before enabling

1. Load one operator-approved policy. Reconcile billed and in-flight current-month
   API intents first, retaining their real production request/lineage identities
   and full upper bounds; missing history is not zero usage. Use
   `initialize_reconciled(month=..., reservations=[OpeningReservation(...)],
   reconciliation_sha256=...)` on the durable, no-eviction Redis ledger explicitly
   once. Its import only supports the current UTC month; unresolved earlier-month
   operations require separate reconciliation before commissioning. An empty
   `initialize()` is appropriate only with no pre-existing usage. Neither method
   runs when a provider request finds missing state. Every replica uses a synchronized
   clock and the same ledger/policy; legacy expiring job writes cannot touch it.
2. Verify channel, format and root cost lineage using real stored job shapes.
   Repairs use the same root lineage, not a fresh queue task's allowance.
   Derived Shorts are charged to a verified long-form family with its `long`
   budget kind, not reclassified into a fresh budget. Independently budgeted
   originals have independent lineages. Caller-provided IDs cannot mint money.
3. Complete reviewed quotes with hard bounds on input, output tokens, tool
   calls, duration, resolution and attempt count. Unknown models/prices block
   paid dispatch. `SpendQuote` holds this upper bound; it does not calculate
   prices or validate a provider's request shape by itself.
4. Retain the existing durable provider intent alongside the deterministic
   spending identity. The guard reserves immediately before ONE create call.
   Only an acknowledged new reservation permits that POST. Duplicate keys,
   lost Redis replies or uncertain provider outcomes never permit blind replay.
   Read/poll a previously accepted provider operation without another create.
5. Commission ALL required paid paths: OpenAI planning/research/next-series/visual critic,
   Gemini JSON/critic/audio review/image/video, ElevenLabs TTS/design/effects,
   Runway and fal creates, future MiniMax/HeyGen and every fallback/SDK retry.
   Every worker thread must preserve the verified spending context. No paid
   route may fall through to an unguarded client if its context/price is absent.
6. Keep the existing request-count guard and artifact checkpoints. A budget
   failure allows free polling, existing-asset editing and other independently
   eligible work. It never approves rejected media or resets paid history.
7. Present the safe budget endpoint in Studio and reconcile provider usage. Do not
   release reserved money on a timeout/error unless a later audited process
   proves non-billing; the foundation intentionally does not implement refunds.
8. Test first with stub transports, then one <=$5 reference acceptance clip.
   This tests integration/style, not a statistically proven 83.3% usable-clip
   yield or monthly throughput. An eight-minute long + three derived Shorts
   and post-publication continuation are later separate acceptance gates.

No new paid package or trial-account cycling is required by this module.
