# Automatic failure handling

Workers persist a versioned `failure_classification` with the terminal error.
It binds a stable code to the original error hash and pipeline stage. Human
wording no longer decides whether a newly recorded content rejection can move
the schedule forward. Old sealed jobs retain their original strict adapter;
invalid or changed new contracts never fall back to text matching.

| Failure | Behavior |
| --- | --- |
| Exhausted story correction | Save the rejected story and stop this attempt; no whole-pipeline Celery rebuild after the bounded correction loop. |
| Exhausted voice takes or transcript/prosody rejection | Preserve saved audio and its failed review; distinguish an actual rejection from unavailable verification. |
| Exhausted stock/final visual rescue | Preserve candidates and any review copy; never turn a failed review into publication approval. |
| Final duration, frame, breathing-room or motion rejection | Finish this attempt without recreating its already paid inputs. |
| Unknown included reviewer response | Keep its occupied provider receipt and original uncertainty; do not replay or refund it. |
| Credit/budget refusal | Preserve the spending error through voice synthesis; no seed retry, alternate provider or content skip based on that refusal. |
| Unclassified failure, credentials or publication uncertainty | Retain the stop. Neither elapsed time nor an error prefix proves a job is safe to discard or upload again. |

The existing minute tick may archive an eligible unpublished failure and release
that channel's schedule. It still checks the exact profile, connection, retry
lineage, original media, absence of upload execution, current funding, explicit
hold policy and daily allowance. It preserves cadence, consumed topic cursor,
every previous provider receipt and all saved assets. The daily allowance is
reconsidered on the next UTC day; it is not reset by the UI or a deployment.

This is not a claim of successful publication or uninterrupted provider service.
Commissioning requires a real quality-approved video to become public and the
next production to start. Lost worker/broker outcomes and providers that cannot
confirm prior submissions still need evidence-backed reconciliation; blindly
restarting them could duplicate charges or uploads. Expiring account access and
the owner's eventual operating budget remain separate prerequisites.
