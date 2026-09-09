# Existing-subscription RouteLLM review candidate

The existing Capital episode has six retained clips and its original voice.
Reviewing those assets should not require purchasing replacement media. The
new adapter and journal prepare a separate subscription-funded review path.
They are currently unused: no production flag, sender, director integration,
entitlement commissioning, provider call, quality approval or publication is
enabled by importing these modules.

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

## Remaining integration

The runtime must bind the existing subscription evidence and actual outgoing
key, enforce zero new cash, use a reviewed transport with one send only, and
connect the two purposes to complete immutable story and visual review. The
journal's purpose label alone does not prove rubric or frame completeness.
There is currently no sender, scheduled commissioning or automatic renewal.
Commissioning is intentionally deferred until that runtime is tested.

Keep the existing story/visual thresholds, narration, original financial root
and new-connection authority checks. Router quality or model independence is
not established by a successful API response. Audio QA, final assembly QA,
recovery child admission, public upload verification and scheduler resume all
remain separate requirements. This path grants no publication approval and
does not supply grounded trend research, new voices or new generated video.
If this exact route later becomes available through any other funding adapter,
that adapter must honor these included-subscription replay records as well.

Offline tests cover actual HTTPX mutation/protocol/schema/image bounds and real
Redis WATCH races, cross-purpose replay, cross-mode receipts, unknown cash,
partial loss/rollback, expiry, cancellation and lost transaction replies.
