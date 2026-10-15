# Kie voice connection

The signed-in owner can save their funded Kie account key at
`/studio/providers/kie`. It uses the existing Studio cookie, same-origin POST,
bounded single-field password form and response privacy headers. The immutable
credential record is encrypted with `APP_ENCRYPTION_KEY`, a Kie-specific domain
and its exact storage namespace. It is available to every server replica without
copying secrets through chat, git, logs or the user's computer.

Saving a key does not activate production or make a paid request. The next
commissioning checks are authenticated balance access, supported narrator IDs,
actual audio/timing output and independent speech quality. No existing
ElevenLabs allocation, replay receipt, historical period or channel is changed.

Verified public Kie contracts on 2026-09-24: account balance is read through
`GET https://api.kie.ai/api/v1/chat/credit`; generation uses
`POST /api/v1/jobs/createTask`; accepted work is observed through
`GET /api/v1/jobs/recordInfo?taskId=...`. Turbo 2.5 lists six Kie credits per
1,000 characters; Multilingual v2 lists twelve. One Kie credit lists $0.005.
The price comparison is not a provider invoice or evidence of measured quality.

The separate prepaid ledger reserves an upper bound before each create request.
Accepted task IDs and encrypted terminal responses are reused; an unknown POST
outcome cannot trigger a replacement purchase. A provider-observed refund is
recorded as zero usage, while a missing usage observation keeps the reservation.
The ElevenLabs connection check has a total cap of eight distinct probes. Production
activation remains absent until real audio, alignment and speech quality pass.
Existing native voice allocations and all their historical receipts remain intact.

An operator can separately authorize two Gemini 3.1 Flash TTS checks within the
same original Kie allocation. This adds no credit and cannot reopen the eight
ElevenLabs checks. Kie's reviewed rates are 140 credits per million input tokens
and 2,800 per million audio tokens. The upstream limits of 8,192 input and
16,384 output tokens imply a 47.02208-credit full-request bound; each request
reserves 48 credits until the provider reports actual usage. The model extension
is immutable, bound to the original funding policy, and has no production
activation side effect. It accepts only the reviewed single-speaker request.
Gemini output does not include source character timing: audio generation alone
does not qualify this route for production. Independent speech recognition and
verified scene/subtitle timing are still required.

The live first Gemini checks returned an explicit 422 `style` validation error,
with no task ID. Kie's `accent`, `style`, and `pace` fields are enumerations;
pronunciation instructions belong in `audio_profile`. A separate, immutable
operator record can permit one corrected submission for each of those two exact
rejected checks. It binds the original encrypted receipts and the unchanged
text/voice/language. Accepted requests, ambiguous responses, different text,
third attempts and further probes remain blocked. Original receipts and their
conservative 48-credit reservations are preserved; no refund is inferred.

Production uses a separate explicit activation only after the retained Turkish
and English probe responses pass blind Whisper recognition, actual word timing,
and the existing Gemini listening review. The operator re-evaluates the raw
encrypted responses, binds all three proof records and the original Kie result,
and changes no allocation. The successful 2026-09-25 checks used Fenrir in
Turkish (11.8 seconds, 1.67 Kie credits) and Kore in English (14.08 seconds,
1.99 credits); both transcripts matched exactly and both listening reviews
passed. These are short connection checks, not proof of a published long film.

Only new Capital/Margin roots use that activation. A permanent root record
binds the provider, model, language and narrator. Any existing native intent or
narrator assignment retains its original route; a matching native reservation
also watches the Kie root key and cannot race a second provider purchase.
Unrelated native reservations are retained. The old native policies, period
archives, cash ledger and receipts are never rewritten to fund this route.

Every accepted WAV is saved before conversion, and its complete MP3 derivative
is content addressed and verified after storage. A retry restores those bytes
and the original Kie task; it does not buy another voice. Blind recognition
supplies real word boundaries for visual scene changes. This path preserves
the entire audio performance and never invents character timestamps or treats
forced alignment as independent recognition. The usual final transcription,
prosody, visual quality and publication gates still apply. Daily admission can
use the verified remaining Kie allocation for the waiting documentary while
keeping the owner's channel limits and existing queue order.

Sources reviewed 2026-09-24: [Kie Gemini contract](https://docs.kie.ai/market/google/gemini-3-1-flash-tts),
[Kie prices](https://kie.ai/pricing), and
[upstream model limits](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview).

The owner settings hub is `/studio/settings`. An authenticated, same-origin
request can save a reusable Studio password as a salted scrypt digest. Normal
login at `/studio/login` is rate limited and uses a Secure, HttpOnly session
cookie for 90 days. No password or API key is exposed in HTML or URLs. Existing
one-time access grants remain usable for initial access and recovery.
