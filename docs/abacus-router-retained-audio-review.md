# Explicit retained-audio subscription review

This default-inert path stages two separate reviews of the existing Capital
narration: blind transcription with word timings, then full-rubric prosody.
It neither commissions itself nor runs from the scheduler. It does not create
speech/video, initialize historical cash, claim a retry, approve a final render
or publish to YouTube.

Both requests explicitly use `route-llm` at the fixed self-serve Chat Completions
endpoint with the complete original MP3 and text output only. Existing ChatLLM
subscription coverage is an explicit operator assertion bound to the real key
and a window of at most 24 hours. Past additional cash remains unknown and new
cash allowance is zero. Token usage is observed protocol data, not an invoice,
cash conversion or proof of an independently chosen upstream model. Actual
`route-llm` audio/strict-schema compatibility remains untested.

## Source and one-send admission

`abacus_router_audio_review_journal.py` owns a separate original-root namespace
and two permanent purposes: `retained_audio_blind_asr` and
`retained_audio_prosody`. It never modifies the story/visual review journal.
Commissioning requires the actual bounded metadata bytes whose hash matches
the original leaf checkpoint. The original voice contract, complete saved
spoken text, scene durations, audio descriptor and source/connection identities
are bound before admission. Exact whole-list historical Turkish numeric spacing
remains accepted; mixed or changed speech is rejected.

Three non-expiring records, full schema checks, independent commitments and
WATCH/EXEC acknowledgements retain reservation history. Missing, partial,
expiring or contradictory records block; complete external loss/rollback
requires external recovery. There is no automatic reset or reconfirmation.

A first acknowledged reservation permits one send. An uncertain reserve reply,
HTTP/protocol failure or settlement reply leaves the purpose occupied. There is
no cached permit, retry or provider fallback. Late settlement can record the
same actual HTTPX response and cannot authorize another send.

Prosody admission requires the same journal's acknowledged ASR observation,
matching parsed-result hash and original spoken-contract hash, exact existing
transcript comparison, valid word timing, identical audio, and the complete
unchanged prosody rubric/schema. A caller's pass flag or fabricated typed object
is insufficient. Existing cash/native request fingerprints are checked without
creating or relabeling financial history.

## Explicit runtime and diagnostic quality reports

`studio_abacus_router_retained_audio_review_enabled` defaults to `False`.
The explicit synchronous runtime also requires spending enforcement, all six
cash limits as exact integer zero, the audited leaf and a frozen server key.
Thread ownership, terminal failure/closure and per-purpose attempts prevent
copied scopes, changed credentials or replayed artifacts from sending again.
The private transport disables retries, redirects and proxy inheritance and
bounds streamed responses to 2 MiB.

Actual HTTP status and a limited error diagnosis are retained on failure.
Only fixed allowed fields or reason indicators can survive a bounded error-body
inspection; free-form messages, raw bodies, headers and credentials are omitted.
Those indicators are diagnostic, never a verified cause or a reason to resend.

`retained_router_audio_qa.py` re-observes actual supplied HTTPX artifacts, binds
ASR to the supplied original descriptor and expected narration, then binds
prosody to that exact ASR and full original rubric. The first request receives
no expected text or story context. Existing transcript/timing and prosody
thresholds are preserved; `audio_qc` labels router results honestly instead of
calling them Gemini reviews.

Purely constructed HTTPX data is not proof of network receipt, and supplied
source values are not authoritative by themselves. Every bridge report remains
`diagnostic_only=true`, `qa_approved=false`, `publish_eligible=false` and
`full_qa_complete=false`, even when a component passes. Decoded duration and word
intervals do not replace the frozen edit-duration or final rendered-video gates;
`edit_duration_qa_complete` remains false.

## Remaining integration

An explicit operations coordinator still needs to load and authenticate original
Storage blobs, preserve acknowledged ASR evidence in an immutable record with a
durable anchor before prosody, and preserve the final diagnostic record. General
recovery execution, final audio/render QA, OAuth delivery authority and ordered
public release require separate verified consumers. This path alone does not
complete unattended production or clear either channel's existing quality hold.
