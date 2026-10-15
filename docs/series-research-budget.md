# Funded next-series research

An explicitly commissioned included RouteLLM subscription can fund draft-series
research over actually retrieved primary pages. The separately priced OpenAI
`gpt-4.1-mini` search route requires its own reconciled funding. Each planning
request proposes at most four source-backed topics. Script, critic, media and
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
The normal scheduler can consider a later, distinct attempt if no active or
ready pending batch exists. Each channel has at most three scheduled planning
attempts per UTC day, at least thirty minutes between starts, through the
archival and execution checks below. These limits never replace provider,
channel or financial capacity checks.

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
tax/surcharge bound, still apply under the configured shared cash ceilings.
The owner prioritized commissioning on September 21 and will select a final
operating budget later; explicit setup grants remain separately recorded.

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


## Bounded recovery after terminal planning failures

A completed planner can retain an uncertain provider outcome without blocking
new planning for the rest of the day. The ordinary scheduler checks the exact
pending/daily equality, permanent execution claim, and terminal `finished`
dispatch whose outcome matches the failed or uncertain pending record. It
archives the exact pending and dispatch bytes, original claim and digest before
deleting only the active pending pointer in one watched transaction. The original
daily, execution, funding and provider records stay occupied and byte-for-byte
intact. Unknown costs remain unknown; a later independent request consumes its
own reservation even if the provider may have charged for an earlier failure.

The first attempt retains the existing v1 identities. Attempts two and three
use a v2 dispatch binding with `preparation_slot`, distinct task IDs, and
`:attempt:2` or `:attempt:3` daily/dispatch keys. They are never new deliveries
of the previous task. All preceding same-day attempts must be verifiably
finished, with no gaps in their permanent history. A ready pending batch still
blocks further planning until consumed or legitimately retired. An unresolved
worker/broker outcome, missing claim, changed record or expiring receipt stays
fenced. After the third failure, a new UTC day is required. Current channel,
credential, consent, funding and capacity are checked again for every new task.

Promotion reads the matching attempt's daily receipt and preserves all previous
ones. Archiving does not prepare a video, approve content, change cadence or
authorize publication. A successful plan enters the existing promotion and
ordinary render pipeline; its script, audio, visuals and publication must still
pass the full independent checks. The dashboard shows the current attempt count.

The separate failed-episode daily cap reports the exact paused root, profile
revision and next UTC midnight to the completed-tick observer. This is status
information only: it clears no pause and changes no counter, reservation or
cadence. Ordinary maintenance reevaluates the same failed episode on a new UTC
day, against all current eligibility and funding checks, retaining the previous
day's receipts and unknown provider outcomes. Studio uses only a fresh matching
observation to distinguish this automatic wait from an owner-action pause.

Rollout is explicit: `STUDIO_SERIES_MULTIPLE_ATTEMPTS_ENABLED` defaults to false.
Deploy protocol support to every worker and remove old deployments before enabling
it on the production worker. This flag controls admission of additional attempts;
all instances of the new release can consume already reserved v2 tasks, including
during the flag rollout. No active or uncertain task needs to be cancelled.

Included-provider settlement failures also retain a bounded encrypted copy of
an already received response when possible. This is diagnostic evidence only;
it never releases a reservation or authorizes another request. A settlement
that succeeded before its acknowledgement was lost remains authoritative and
is never overwritten by failure capture.

## Stable completion of rejected episodes

A quality hold seals the actual failed task chain and retained media. The
dashboard's legacy repair-state synchronization could rewrite those jobs after
sealing, changing their raw hashes and blocking later series promotion. New
holds atomically retain permanent job fences; ordinary progress, completion,
retry and repair-checkpoint writers respect them. Polling returns no repair
action without rewriting a held job. The bounded recent-history index may
drop old entries while the sealed job remains available by its ID.

For an older unfenced hold, the ordinary minute tick can append one separate
revalidation receipt. It first rechecks the original policy, daily history,
current channel/profile, funding, exact task chain, failed terminal state,
same failure class, unchanged specification and identical retained candidates.
Any active work, new child, changed media, owner hold or possible publication
blocks the append. The original hold, job bytes, schedule, daily count and
provider receipts remain unchanged. The new receipt seals the current job
hashes and installs permanent fences in the same watched transaction. Future
changes still fail; the receipt cannot be overwritten or issued a second time.
Promotion requires this exact later proof when the original hashes differ.
It remains an unpublished disposition and never grants quality or upload
approval. A lost acknowledgement is resolved by reading the existing receipt.

## Source evidence and the bounded story correction

The included sentence audit keeps every actual model row and exact source
identity. In the director, an invented, foreign, duplicated or noncontiguous
quotation becomes a negative finding, even when the model's editorial verdict
is positive. It cannot approve a sentence. Fresh, unlocked stories can send
those findings through the existing single whole-story correction and full
independent review. An immutable saved story cannot be rewritten by this path;
a failed second review stops before media. Missing rows, changed narration and
malformed response structure still fail closed. Other audit callers retain
strict quotation validation by default.

The current audit also rejects a claimed prohibition when its cited text only
describes cost or inconvenience. The narrow negative guard cannot grant
approval or override a negative semantic verdict. Previous audit versions
cannot confer a current story approval. No request cap, reservation, daily
counter or publication gate changes with this correction.

Audit version 5 binds each factual assessment by the exact complete narration,
unchanged array order and full row count. A repeated numeric position is optional;
when supplied it must still be the correct integer. Missing, duplicated,
reordered or changed narration remains invalid. The retained actual response
does not gain invented index fields; negative findings use the authored scene's
position only after the exact identity check. This avoids rejecting an otherwise
complete critique solely because the provider omitted redundant indexes. It
does not recover or refund an earlier unknown request, authorize a replay, or
relax any factual, quotation, editorial or publication check.

A terminal `included_factual_audit_invalid` at `director_qc` is an unpublished
story rejection eligible for the same fully guarded, daily-bounded quality
hold. This prevents an unusable independent critique from permanently blocking
later topics. Other stages or unrecognized errors remain ineligible. This
classification cannot retry the critique, alter accounting or approve media.

The same explicit stock-only research decoder accepts the provider's redundant
`visual_queries_count` scene metadata only when it is an integer equal to the
actual two or three queries. It omits that derived counter from parsed content,
preserving every query, narration, source and the exact raw response proof.
Wrong counts, duplicate counters, other paths, unrelated extra fields and
legacy schemas remain invalid. This does not settle or replay a previously
unknown request, and the entire original research schema and independent QA
still apply to the actual content.
