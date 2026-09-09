# Existing-subscription RouteLLM review candidate

The existing Capital episode has six retained clips and its original voice.
Reviewing those assets should not require purchasing replacement media. The
new adapter and journal prepare a separate subscription-funded review path.
The explicit retained-review runtime defaults OFF. Importing these modules does
not commission an entitlement, send a request, grant quality approval or publish.

## Documented scope

The [RouteLLM API credit system](https://abacus.ai/help/developer-platform/route-llm/#credit-system)
states that ChatLLM subscribers can continue using RouteLLM after their monthly
credits run out. The narrow interpretation here is existing-subscription use
of `model: route-llm` at `https://routellm.abacus.ai/v1/chat/completions`.
This is not an entitlement for every fixed premium model. The official
[Chat Completions](https://abacus.ai/help/developer-platform/route-llm/chat-completions)
and [image-analysis examples](https://abacus.ai/help/developer-platform/route-llm/chat-completions/image-analysis)
also document JSON schema and image input with that router. Sources reviewed
2026-09-09. No undocumented account-balance endpoint or auto-topup setting is
assumed.

The response may identify only the router alias. The adapter records the
returned model honestly and never asserts a verified underlying model or
independence between two router calls. Input/output token usage is protocol
evidence, not an Abacus credit balance or a cash invoice. Actual review quality
and compatibility still require a live calibration with the existing rubric.

## Adapter and persistence

`abacus_router_adapter.py` freezes the complete bounded text, JSON schema and
ordered original JPEG data URLs. It permits only the exact endpoint and model,
text output, at most 60 images and 8,192 output tokens. There are no tools,
external image URLs, audio/video generation, streaming, retry or fallback.
It verifies the actual HTTPX request and a complete terminal response before
returning schema-validated JSON and hashed observation evidence. It does not
send requests or grant spending authority.

`abacus_router_review_journal.py` is an explicit, synchronous operator API for
the already audited Capital root and retained leaf. A one-time policy binds the
existing subscription assertion, actual key hash and complete current
continuity observation. This assertion is not presented as a provider account
lookup. Historical additional cash remains `null`; new cash allowance stays
zero. No USD foundation, credit conversion or historical-debt reconciliation
is created.

There are two permanent one-use purposes: immutable story review and retained
visual review. A root/request fingerprint also prevents the same request from
using the other purpose; existing USD/native request markers are checked under
WATCH without altering them. Source, profile, current OAuth, original lineage,
owner fences and idle queues are checked in the same reservation transaction.
The old OAuth and original **6/6** generation records remain intact. These idle
requirements deliberately mean this is not yet a generic Celery admission API.

State, independent journal and commissioning commitment are stored atomically
in three non-expiring Redis keys. A lost reservation acknowledgement, timeout,
invalid response or partial key loss never authorizes another send. Rolling
back state and journal alone conflicts with the surviving commitment. External
loss/rollback of all three requires external recovery; no request path may
automatically reinitialize. A late valid response may be recorded after expiry
or cancellation, without opening new work. Exact settlement readback is
idempotent and preserves original timestamps; different response evidence is
a conflict. No provider credentials, prompts or image bytes are persisted.

## Explicit runtime and remaining release integration

`prepare_subscription_router_recovery` is the explicit synchronous entry. It
requires the exact retained Capital source, a precommissioned journal,
`STUDIO_SPEND_ENFORCEMENT=true`, all six cash limits at zero and
`STUDIO_ABACUS_ROUTER_RETAINED_REVIEW_ENABLED=true`. That separate flag defaults
false and does not reroute any ordinary production request. The scope derives
the actual Abacus key from server settings; neither key, sender nor a funding
identity is accepted from a request body. A fresh HTTPX transport has zero
retries, no environment proxy or redirects, and a bounded streamed response.
Reservation ACK precedes one send; settlement ACK precedes result delivery.
Any uncertain result leaves the permanent slot occupied and the scope terminal.
There is no scheduled commissioning or automatic renewal.

The actual immutable story critic supplies its complete existing rubric and
schema; the visual critic supplies its original rubric, all sampled JPEGs in
order and the same complete schema. No writer, new shot or new voice is used.
Missing images/overrides reject before sending. Temporal, missing-review and
semantic retries are disabled in this one-use scope; contradictory score/reason
results keep the existing hard rejection. Private recovery audit records keep
honest response evidence and bounded rejected-story diagnostics.

An explicit private typed story proof permits this route without calling it a
Gemini review. It is created only after the complete immutable director checks,
binds every non-QA package field, topic and acknowledged response proof, and is
valid only while the exact runtime scope still contains that observation.
Ordinary Gemini critic requirements remain unchanged. A copied marker in a
package, a changed package, a new empty scope or an ended scope cannot supply
this proof. This deliberately does **not** make the resulting saved package
eligible for a later generic worker: a persistent recovery consumer must first
rederive the server-owned journal/package/authority binding.

Keep the existing story/visual thresholds, narration, original financial root
and new-connection authority checks. Router quality or model independence is
not established by a successful API response. Audio QA, final assembly QA,
recovery child admission, public upload verification and scheduler resume all
remain separate requirements. A real calibration remains necessary. This path grants no publication approval and
does not supply grounded trend research, new voices or new generated video.
If this exact route later becomes available through any other funding adapter,
that adapter must honor these included-subscription replay records as well.

Offline tests cover actual HTTPX mutation/protocol/schema/image bounds and real
Redis WATCH races, cross-purpose replay, cross-mode receipts, unknown cash,
partial loss/rollback, expiry, cancellation and lost transaction replies.
