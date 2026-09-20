# Funded next-series research

With spend enforcement enabled, draft-series research uses OpenAI
`gpt-4.1-mini` through the existing server credential. This small, separate
route proposes at most four source-backed topics. Script, critic, media and
narration routes keep their own configuration and funding requirements.
No subscription is assumed to include API cash or automatic overage.

The minute scheduler prices the exact request before it consumes a daily
dispatch. It checks the shared monthly, daily and channel limits, the existing
shorts lineage ceiling, and the actual credential's reconciled funding route.
It writes a permanent task context in the same transaction as the dispatch.
Only request/credential hashes, quote and authority binding are stored there;
no prompt, API key or fabricated video job is added.

The real Celery task carries its own ID into the paid SDK adapter. Current
profile, account, consent epoch, encrypted credential, queue position,
execution claim and frozen request context must still match. The planner
checks capacity again before writing its pending/daily model attempt. A known
budget rejection there leaves those two records unused. An already dispatched
task remains a durable dispatch record and is never automatically resent.
The normal daily scheduler can consider a later new attempt if no uncertain
or ready pending batch exists.

Immediately before the one HTTP request, the adapter reserves the same quoted
upper bound and funding atomically. It rechecks current authority after that
reservation. Timeout, lost acknowledgement and detected changes retain the
hold and cannot grant a refund or retry. Missing context cannot fall back to
a different video job or a new allowance. No initializer runs in these paths.

## Reviewed request and tariff, 20 September 2026

Only stateless plain text with the fixed next-series JSON schema name is
priced: standard tier, `store=false`, at most two non-preview `web_search`
calls and 3,600 output tokens. Prior responses, conversations, other tools,
reasoning options, alternate models and unknown options are rejected. JSON
request size is at most 50,000 encoded bytes; prompt UTF-8 size is at most
30,000 bytes. The SDK makes zero automatic retries.

[OpenAI pricing](https://developers.openai.com/api/docs/pricing) specifies
$0.01 per search and a fixed 8,000 input-token billing block per call for
GPT-4.1 Mini with non-preview search. The
[model tariff](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
is $0.40/M input and $1.60/M output. The quote deliberately reserves $0.50/M
input, all prompt/schema bytes plus 4,096 framing tokens, both search blocks
and the output allowance at every possible round (tool calls plus final
answer). This overcounts repeated context and output instead of assuming
cache discounts. The maximum admitted shape reserves $0.147824 list cost;
actual smaller requests reserve less. Reconciled cash factors, including any
tax/surcharge bound, still apply and all providers share the owner's $10 cap.

The [Responses limits](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
bound total processed tool calls and output. `search_context_size=low` alone
is not treated as a token ceiling. The separate price revision
`openai-series-2026-09-20-v1` requires matching account funding evidence;
old Astra funding cannot authorize it. Quotes expire on 1 October UTC along
with the existing reviewed catalog. No balance is initialized or extended.

The result must contain completed hosted searches and each proposed source
URL must appear in the returned consulted-source list. URL-shaped generated
text by itself cannot create a ready batch. Even a ready batch is an editorial
draft: research/critic, media budget and publication gates remain required.
Synthetic HTTP/Redis tests demonstrate dispatch and accounting behavior;
they do not establish live model quality, account access or publication.
