# Production spend control: staged dispatch guard, OFF by default

The reservation primitive is now connected to the current paid provider
submission sites. `STUDIO_SPEND_ENFORCEMENT=false` preserves the existing
provider contract without reading or initializing the budget ledger. The new
Abacus editorial path requires enforcement and remains separately disabled. This
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
  SDK funding binds to the effective authorization headers of the actual
  sending client, whose key and headers are frozen in a separate zero-retry
  instance before reservation. Alternate/duplicate auth headers and unbound
  OpenAI organization/project overrides block before spending or submission.
  Unquoted SDK/HTTPX query parameters, alternate HTTP authentication, billing
  headers and request hooks are unsupported. Standard transport configuration
  is checked again after reservation; detected drift stops submission while
  retaining the reservation. This is a guard for the trusted application
  dispatch sites, not isolation against arbitrary concurrent Python mutation:
  SDK copies still share their underlying HTTP transport and callers must not
  mutate that transport during a request.
- Native JSON creates also detach their body, headers and allowed parameters
  before quoting. Bound HTTPX clients must have no alternate authentication,
  query defaults, cookies, redirects, billing headers or request/response hooks;
  configuration is checked before and after reservation. Plain application
  dispatch functions remain trusted reviewed code and must not hide an
  unreviewed client in a new wrapper.
- Planning/render workers persist a budget rejection as a terminal failure on
  the first attempt, consistent with their disabled Celery retry. The server
  tick can release that active capacity without a Studio page visit. The budget
  reason, preserved assets, spending history and failed episode hold remain;
  this does not automatically resume a job when a new budget day starts.
  This depends on the job registry accepting the failure write; registry
  outages and hard worker interruption still need separate reconciliation.
- Scheduled renders and next-series preparation now check existing budget and
  funding capacity before reserving a topic, job, or daily preparation attempt.
  Missing/expired funding, exhausted month/day/channel limits and disabled
  format allowances return `budget_blocked` without changing those records.
  A later ordinary tick may check the same unconsumed topic again, including
  after daily capacity renews. Existing failed jobs and unknown paid requests
  keep their original holds. The preflight uses bounded Redis reads and never
  initializes, refunds or reserves money. Available covered allocations are
  distinct from new cash; unused cash cannot fund a covered-only account.
  This check detects known insufficient capacity, not the full cost of a video
  or availability of every provider route. Each actual request still requires
  its quoted, credential-bound atomic reservation, including after a concurrent
  job consumes capacity between the preflight and worker execution.
- Next-series research additionally prices the exact hosted-search request
  before dispatch, binds it to the real scheduled Celery ID and current account,
  and shares the existing shorts lineage ceiling and global cash/covered
  counters. It creates no fake video ancestry or new allowance. Completed
  hosted search must corroborate proposed source URLs; the resulting batch
  still requires full research, critic and media/publication approval. See
  [the research contract](series-research-budget.md) for bounded pricing,
  current-authority checks and durable failed/unknown attempt behavior.
- Scene creation and repair loops, audio-provider fallbacks, Omni anchor review
  and temporal/recursive visual review preserve a budget rejection as terminal.
  They must not convert it into an ordinary provider failure and continue to
  another paid request. Native Abacus submission failures are also terminal;
  valid usage is recorded even when the generated content subsequently fails QA.
- New video creates require an immutable plan for every scene, prepared from
  the exact storyboard, final measured narration durations and output aspect.
  The first new generation freezes the plan; retained-only recovery performs
  no ledger initialization or current-price lookup. Initial generation, repair
  and child tasks retain the same root/scene allowance. Actual rounded provider
  duration, model, resolution, audio and sample count must match that plan.
  The per-request ceiling is the original Gen-4.5 quote and each scene's total
  is twice that ceiling; the existing family and call-count caps still apply.
  A more expensive fallback cannot exceed the original quote even when the
  family has room. All scene and family counters share one atomic reservation.
- Every enforced paid route also requires explicit reconciled funding evidence
  bound to the actual provider credential, exact endpoint, model and price
  revision. Verified included coverage and additional cash have separate counters.
  New cash across providers is capped at the owner-approved $10/month maximum.
  Covered-only exhaustion blocks without switching to cash; cash-only routes
  require a verified upper-bound factor including applicable taxes/surcharges.
  Funding, scene, family and replay records commit in the same transaction.
- `GET /studio/youtube/production-budget` requires normal owner authentication,
  is non-caching and read-only, and distinguishes disabled/active/blocked state.
  Its counters are reserved upper bounds, NOT settled invoices or an all-in
  household/subscription spending cap. No initialization/refund/resume API is
  exposed by this change. Its separate `funding` summary reports covered-use
  reservations and extra-cash capacity without account/credential hashes. Missing
  or expired funding evidence yields blocked status rather than a false active
  cash-cap claim. A fully reserved cap can report zero remaining capacity.
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
| OpenAI GPT-6 Astra (rate updated 9 September) | Tool-free plain text, explicit standard tier, at most 100,000 encoded JSON bytes, at most 16,384 output tokens | $12.50/M input reserves the implicit cache-write rate; $50/M output. Conservative input bound is encoded JSON bytes + 4,096 framing tokens |
| OpenAI GPT-4.1 Mini series research (20 September revision) | Stateless next-series JSON, standard tier, at most two non-preview web searches, 50,000 encoded bytes and 3,600 output tokens | $0.01/search plus fixed 8,000 input-token blocks; conservative repeated-round input at $0.50/M and output at $1.60/M; maximum admitted shape $0.147824 list cost |
| Gemini 3.1 Pro Preview | One tool-free text-only JSON candidate, optional inline schema, at most 100,000 encoded JSON bytes and 16,384 total output tokens | $2/M input and $12/M output including thinking; encoded bytes + 4,096 framing keeps the input bound below the 200,000-token price threshold |
| Gemini 3.7 Flash text (9 September revision) | One tool-free native text JSON candidate, complete prompt/system/schema at most 100,000 encoded metadata bytes, at most 16,384 output tokens | Full 1,048,576-token input context at $0.75/M plus requested output at $3.75/M including thinking; at most $0.847872 reserved |
| Gemini 3.7 Flash Short audio (separate 9 September revision) | Full native prosody rubric/schema and one original MP3/PCM16 WAV, at most 8 MiB and 30.08 decoded seconds, medium thinking, at most 8,192 output tokens | Same full input context and rates; at most $0.817152 reserved |
| Abacus Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) | Native Messages, one plain-text user message, thinking disabled, at most 100,000 encoded JSON bytes and 8,192 output tokens | $1/M input and $5/M output; encoded bytes + 4,096 framing tokens |
| Abacus Claude Haiku 4.5 native images (9 September revision) | One user message with full interleaved text/JPEG evidence, thinking disabled, standard tier, at most 60 bounded images and 8,192 output tokens | Full 200,000-token input context at $1/M plus the requested output ceiling at $5/M; at most $0.240960 |
| Abacus Claude Sonnet 5 / 4.6 (`claude-sonnet-5`, `claude-sonnet-4-6`) | Same native text contract, explicit global inference | Respectively $2/$10 and $3/$15 per M input/output tokens |
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

OpenAI's default [implicit prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
can charge a cache write at 1.25 times ordinary input cost. The Astra quote
reserves that higher input rate without predicting a cache hit. A separate
`openai-text-2026-09-09-v1` revision invalidates old OpenAI funding-route evidence
without repricing existing video-scene plans. The enforced SDK path sends an
explicit `service_tier=default`, whose standard pricing is documented in the
[Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
and detaches nested options before quoting/reservation. Missing tier is not
allowed to inherit project-level automatic routing at dispatch. These are
conservative reservations, not evidence of any past provider overcharge.

Abacus prices were checked against the authenticated official `/v1/models`
catalog and the [public per-million-token price table](https://routellm-apis.abacus.ai/).
The former returns USD **per token**; do not reuse the differently scaled
`listRouteLLMModels` examples. The adapter uses the
[native Anthropic Messages passthrough](https://abacus.ai/help/developer-platform/route-llm/anthropic-messages),
`service_tier=standard_only`, and `thinking.type=disabled`. Sonnet also requires
`inference_geo=global`; Haiku omits that unsupported field. Tools, cache controls,
media, automatic routing, priority/fast modes, unknown headers and provider-side
structured-output configuration are rejected. A plain prompt carries the JSON
schema, which is validated locally. The authenticated model-list read confirms
key access, not a successful paid Messages call or remaining account balance.

`STUDIO_ABACUS_EDITORIAL_ENABLED=false` is the default. When explicitly enabled
with commissioned spending, only the fresh scheduled director refinement uses
the configured Abacus Claude model (Haiku by default); research, immutable saved
stories and independent media reviewers retain their existing routes unless
the separate visual reviewer setting below is explicitly selected. Every
create has one reservation and no automatic POST retry or provider fallback.
Native model/request identity, token counts and pricing mode must match the
bounded request. An acknowledged `usage:<digest>` observation is attached to
the existing durable reservation before generated-content validation. It stores
no prompt or key and never reduces reserved counters, settles an invoice,
converts credits to cash or authorizes another create. Missing/malformed usage,
uncertain writes, over-quote counts and conflicting observations stop the job.

`STUDIO_VISUAL_QC_PROVIDER` remains empty by default. Explicitly setting it to
`abacus` selects the fixed native `claude-haiku-4-5-20251001` image reviewer;
it requires spending enforcement, the existing Abacus key, and funding admission
for `abacus-vision-2026-09-09-v1`. This setting is independent of the editorial
opt-in. The full rubric, exact scene count/schema, ordered frame labels, source
evidence and cross-scene context are preserved. Abacus receives the original
JPEG bytes, with no frame resizing, splitting or omission to fit its quote.
Up to 60 images are accepted, each at most 180 KiB, 640 pixels wide and 2,000
pixels tall, with at most 12 MiB decoded image bytes and 100,000 encoded metadata
bytes. Larger or malformed evidence blocks before submission.

This separate quote reserves Haiku's full 200,000-token input context at $1/M
plus the requested output ceiling at $5/M (at most 8,192 output tokens): at the
maximum output setting, $0.240960 per request. It does not infer an input bound
from image dimensions. The native image contract and model context/rates are
documented by [Abacus](https://abacus.ai/help/developer-platform/route-llm/anthropic-messages)
and [Anthropic](https://platform.claude.com/docs/en/models/overview).
Local schema validation retains evidence `uniqueItems` and all existing QA
gates, including the 86-point threshold. Submission/usage/schema failures are
terminal; there is no automatic POST retry or fallback to another provider.
The existing explicit same-frame temporal repair and score-consistency review
remain separate, individually reserved calls; an identical repeated request
still stops at the existing replay fence. This opt-in has not been activated
or quality-validated against a live account; it does not commission V6 dispatch,
audio review or the complete production/publication chain.

Other prosody/music and fallback speech review, Gemini grounding/multimodal critic/image/Omni,
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

## Gemini 3.7 Flash text and Short prosody reservations

Two distinct price revisions cover the existing tool-free immutable story critic
and native Short prosody request. The complete prompts, source context, schema,
selected audio and quality criteria remain unchanged. The audio route requires
the complete original canonical base64 MP3 or PCM16 WAV and reuses the same
bounded container and real FFmpeg decode inspection as Whisper; it never prices
audio through the text route or truncates evidence to fit a limit. Unknown
fields, search, images, other models and oversized audio remain blocked.

Both quotes reserve the documented full input context, rather than infer total
input tokens from text bytes or 32 audio tokens per second. The current 8,192
output setting reserves $0.817152 per request. This conservative reservation is
not observed usage, expected cost, a free-tier claim or an invoice. The existing
Gemini transport keeps its acknowledged reservation and replay fence; this
increment does not implement response-usage settlement or refunds.

Sources: [Gemini 3.7 Flash model limits](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash),
[standard pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.7-flash),
[audio input accounting](https://ai.google.dev/gemini-api/docs/audio#technical-details-about-audio),
and [the shared thinking/output pool](https://codelabs.developers.google.com/bigquery-generative-ai-intro#3).
These revisions expire on 1 October 2026. They do not activate account funding,
change the live model/reviewer, reset the ledger or grant a recovery dispatch.

## Bounded Whisper transcription of an existing Short

With enforcement enabled, the existing OpenAI speech review now uses a dedicated
native multipart transport for `whisper-1`. Its immutable request contains the
original MP3 or PCM16 WAV bytes, `verbose_json`, word timestamps, English/Turkish
language and temperature zero. It validates the whole file structure and decodes
the admitted audio to verify at most 30.08 seconds, with an 8 MiB byte limit.
The original bytes are sent; the local decode does not alter the narration.

The reviewed [Whisper rate](https://developers.openai.com/api/docs/models/whisper-1)
is $0.006/minute. Each request conservatively reserves one full minute ($0.006),
under `whisper-short-2026-09-09-v1`, expiring before 1 October. This is not a claim
about provider rounding, prepaid coverage or the account's tax-inclusive cost.
Its exact route and actual credential still need reconciled funding evidence.
Longer/unsupported audio stays blocked; other speech and prosody models gain no
allowance from this quote.

The same atomic family/funding/replay reservation binds the captured bytes and
form fields before one streamed POST. Redis stores their digest, not the voice
or filename. Timeout, rejected HTTP and malformed transcript/timing responses
are terminal through the existing worker failure handler, with no provider or
same-audio retry and no refund. Valid transcript mismatch retains the existing
quality decision. Enforcement OFF preserves the legacy request path. No live
transcription or funding initialization is part of this implementation.

## Existing ElevenLabs voice: evidence-backed quote, not commissioned

`STUDIO_ELEVENLABS_PRICING_EVIDENCE_JSON` defaults to an empty string. The new
pure quote and runtime adapter support only the existing selected voice
`WtOce4YK0dDSxlVlSdBh`, the `/v1/text-to-speech/<voice>/with-timestamps` route,
MP3 44.1 kHz/128 kbps output, and the unchanged continuous Multilingual V2 or
Turkish Flash V2.5 request profiles. Text normalization, voice settings and
timing behavior stay unchanged. Plain-audio, contextual and other voice/model
requests remain unpriced. The actual request shape follows the
[timestamp TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps).

Operator evidence must contain a version, USD currency, account/credential/proof
SHA-256 digests, current-month UTC validity dates, exact voice/route, and one or
both supported profiles. Each profile specifies its model, text length bound,
verified billing-unit bound and positive rational list rate. The billing bound
must explicitly cover normalization, model, custom-voice and all request charges;
it cannot be inferred from `sharing.rate`, a subscription price or a character
balance. Integers calculate `ceil(actual transmitted Unicode codepoints *
unit numerator / denominator) + fixed units`, then round the USD microdollar
cost upward. Validation checks the contract; it does not independently prove
the operator's billing evidence.

The canonical evidence hash forms the price revision. A second read must still
match before reservation. The account digest and the actual sent API-key digest
must match the reconciled funding account in the same transaction as the family
and request counters. The exact body and output parameter form the replay
identity. Covered-only and cash-only funding retain their existing distinct
rules, including the shared $10 extra-cash ceiling and no refund on timeout.
No tariff, credits or production policy was initialized by this change. A
verified tariff, available coverage/cash evidence and opening commitments are
still required before any enforced TTS dispatch can be commissioned.

## Financial scope

- The earlier approximately $260/month API estimate and $500/month combined
  estimate came from the assistant. Neither is a user-approved envelope,
  allowance or spending target. On 8 September 2026 the owner set a maximum
  **$10/month in additional API cash spending**, separate from existing
  subscriptions and included credits. This is a ceiling, not a spending target.
  The earlier estimates must not initialize policy or authorize paid dispatch.
- Persist and reconcile included provider quotas separately from the approved
  $10 additional-cash ceiling. The staged funding ledger now distinguishes
  verified covered allocation from new cash upper bounds, while the original
  list-cost counters remain intact. Real account evidence and live commissioning
  are still missing; simply setting the original monthly number to $10 does not
  establish the funding policy.
  Missing credit coverage must not be treated as free, and exhausted included
  rights must not trigger an automatic topup or a cash fallback over the ceiling.
  Both prior/in-flight extra charges and provider overage settings need evidence
  before live paid dispatch. Existing subscription renewals are outside this
  additional API allowance, not newly authorized purchases.
- In the original USD-list-cost funding mode, a covered allocation must be an explicitly verified USD-list-cost equivalent
  net of prior/in-flight use, with a verified no-overage condition. Abacus credit
  counts, subscription prices or ElevenLabs raw voice-rate metadata do not supply
  this conversion by themselves. Missing evidence blocks both covered and cash
  classification. The policy supports one verified billing account per provider,
  bound to a digest of its actual credential; rotating a key requires reconciled
  evidence, not a fresh allowance.
- `SpendLedger.initialize_funding(policy)` is an explicit one-time operator step
  after reconciliation. It cannot overwrite existing policy/state, repair a
  partially missing pair, or reopen both missing fields when earlier funded
  request receipts remain. Existing cash debt above $10 stays visible. Policy,
  route and account expiry stop new applicable paid requests; there is no automatic
  monthly reset, replenishment, refund or topup. Bounded historical scans refuse
  an uninspected tail, requiring separate reconciliation for larger histories.
- Prefer existing legitimate subscription rights, beginning with the user's
  Abacus AI subscription. Verify its actual plan, remaining credits, commercial
  output rights and supported automation access before treating any generation
  as included. A web subscription does not by itself establish an API allowance.
  Included access does not mean unlimited capacity or authorized cash overage.
- Keep fixed bills, infrastructure, asset/music allowances, taxes and contingency
  visible separately from this API reservation ledger. There is currently no
  approved combined monthly envelope against which to reconcile those costs.
- A valid all-zero USD policy prohibits USD-quoted dispatch while free local editing
  and accepted-operation polling remain possible. Legitimate free quotas or
  prepaid credits require provider-specific quota/cash accounting; their expiry
  must NOT automatically enable a paid route. No repeated fake trial accounts.
- Included subscription credits and actual cash overage need separate
  reconciliation. This ledger counts reserved upper bounds, not invoices.
- Remaining-period initialization must account for existing/in-flight usage.
  It must never silently reset the whole current month to $0 already spent.
- Existing personal ChatGPT Pro/Google subscriptions and their extra credit
  purchases are separate; this application cannot cap purchases in those apps.

## Explicit ElevenLabs native-credit mode (default OFF)

`STUDIO_ELEVENLABS_NATIVE_CREDITS=false` preserves the existing route. The new
opt-in accepts only the selected `WtOce4YK0dDSxlVlSdBh` voice, Multilingual V2,
the original normalized continuous narration settings, MP3 44100/128 output and
the `with-timestamps` endpoint. It requires spending enforcement ON, a real
stored Short task/original lineage, an already reconciled foundation ledger,
and a separately commissioned native-credit policy. No caller initializes any
ledger, modifies a tariff, resets a failed job, or grants publication.

This addresses an accounting mismatch: verified subscription credits have their
own unit, while the original covered mode required a USD-list-cost conversion
and a universal billed-text normalization bound. Native credits are never
presented as dollars or a zero-price quote. An operator policy binds the exact
account/outgoing-key digests, observed net quota, outstanding-use reconciliation,
reset/expiry, and no-extra-cash controls (zero extension, extension unavailable,
and automatic topup OFF with explicit observed/owner-attested provenance).
The policy cannot authorize a purchase or automatic fallback to cash. Existing
subscription renewals remain separate from the owner's additional USD10 limit.

Because no exact future character meter is assumed, each native intent reserves
ALL remaining allocated credits until its actual response is known. This is a
capacity lock, not a claim that the request costs that amount or that the input
character count bounds billing. A complete authenticated HTTPX response with
one positive integer `character-cost` and one `request-id` can settle the exact
observed credits, releasing only unused capacity. The official
[API introduction](https://elevenlabs.io/docs/api-reference/introduction/)
documents those response headers; their presence on the actual timestamp route
is checked rather than assumed. No synthesis is made merely to test the meter.

The internal allocation is not a provider request-price ceiling: an admitted
request can consume more than its remaining share. Verified usage up to the
evidenced account quota is still recorded in full. `overrun_credits` makes that
excess visible, available capacity becomes zero, and the next request is
blocked. Neither the original charge nor its request fence disappears. This
does not authorize provider cash overage; the independent account cash controls
remain required. Charges outside the evidenced account scope remain unverified.

The response's original request must match the immutable outgoing URL, query,
JSON and key, with no redirect or ambiguous headers. Request/response byte
digests and request identity bind the observation. Missing/invalid usage, a
timeout or an uncertain Redis acknowledgement stops the task with `SpendBlocked`
and preserves the held allocation. No automatic resend or new seed follows an
unknown outcome. A metered response may still fail the unchanged audio/JSON QA;
settlement is not quality approval. Application functions and HTTP transports
remain trusted code, and the observer bounds already-loaded responses rather
than claiming a streaming download limit.

`CreditLedger` persists policy/state and an independent complete journal without
TTL in the existing `{production_spend}` Redis hash slot. It detects partial
loss, expiring records, rolled-back state, mismatched receipts and uninspected
history. Production commissioning also records an immutable native-mode marker
in the foundation ledger. Every reservation writes the native state, journal
and the existing-fingerprint request marker in one acknowledged WATCH/EXEC.
Complete correspondence with the foundation markers prevents a simultaneous
state/journal rollback from forgetting an outstanding request. Original root,
channel and source connection bindings stay fixed across children and key IDs.

The native branch rejects an earlier USD request with the same fingerprint.
The USD Eleven branch rejects any native commissioning/receipt evidence, even
if the opt-in flag is later disabled or some native records disappear. Changing
funding modes cannot resend a consumed request. A settled intent retains its
permanent request fence; identical observation replay cannot release capacity
twice. No automatic monthly renewal, topup, missing-state initialization or
coordinated datastore-loss recovery is implemented. Expiry or an uncertain
meter requires explicit reconciliation. An all-zero USD policy can coexist with
separately evidenced credit-only activity; it does not itself commission that
activity or open the USD10 additional allowance.

The private Capital connection-continuity staging service is separate. It
records only the exact audited legacy three-job episode-5 lineage, original
candidate pointers and current OAuth evidence. Its receipt is explicitly
diagnostic, not runnable, not QA-approved and not a funding or publish grant.
Admission/worker/publication/resume consumers remain to be implemented; old
selected-edit proof cannot be fabricated from raw candidate pointers.

Complete legacy journals can instead use the existing V3 retained-media path,
which derives a new package and cut from the six saved candidates and voice.
V3/V4 preparation attaches the original source options before a single immutable
story review. Existing shots do not consume the new-shot authoring allowance;
all non-QA package fields remain fixed, including any explicit V4 repair prompts.
Preparation and private checkpoint publication reject changed scenes, options,
sources or other editorial fields. Fresh story and exact-cut visual approval
remain mandatory. This does not enable a reconnected channel's spending context,
grant a retry, reset historical creates or prove the original selected edit.

## Required commissioning before enabling

Rejected production Shorts can now preserve their exact selected input bytes
without opening a new spend allowance. See
[selected visual recovery](selected-visual-recovery.md) for its private candidate
contract and remaining dispatcher/QA requirements. A checkpoint is not a paid
repair permit; it cannot reset a scene plan, prior cash debt or an uncertain
request reservation.

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
   Per-scene ceilings are implemented for the reviewed Runway/Veo video shapes.
   Unknown Omni/fal/image/audio shapes remain blocked. An older family with paid
   video receipts cannot receive a new zero-use scene plan; preserve/reuse its
   existing files or reconcile it explicitly instead of silently starting over.
   Commission the immutable funding policy with verified account coverage, actual
   credential bindings, current-month opening cash and surcharge/tax bounds before
   any enforced dispatch. Reconcile again when evidence expires; do not invent a
   new period's available money or assume manual account usage was zero.
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

Budget commissioning must also pass the app-independent operation acceptance
checks in [the delivery workflow](longform-delivery-workflow.md#app-independent-operation-acceptance).
Reaching a paid limit must stop new paid dispatch while eligible free monitoring,
accepted-operation polling and reuse can continue; it must not silently select a
paid fallback. These are commissioning conditions, not a claim of proven 24/7
operation.
