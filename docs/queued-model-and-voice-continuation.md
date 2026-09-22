# Queued long-form and retained-speech continuation

The first queued three-minute documentary reached research but Gemini explicitly
rejected the larger editorial JSON grammar with HTTP 400 `INVALID_ARGUMENT`.
Long editorial/story-review requests now use a compact typed wire grammar with
the complete original schema also included in the instruction. The independent
response observer still enforces every original required field, scene count,
type, enum and numeric limit. Research and Short request identities are unchanged.
See Google's documented [schema limits](https://ai.google.dev/gemini-api/docs/structured-output#limitations).

One private continuation is available for that exact failure shape: the original
queued root and its authenticated research-resume child, exactly one completed
research response and one explicit editorial 400, and no voice, video, cash or
other provider intent. Encrypted responses and immutable request records must
match their hashes. Unknown outcomes, a third attempt, owner holds, changed
channel/profile/spec or missing execution claims prevent admission. The original
research response is reused; normal independent story, audio, visual and public
release gates still apply. No previous request, result or ledger is reset.

For retained Shorts speech, whole measurement expressions such as
`forty-five-gram` and `45 gram` compare as the same number plus the unchanged unit.
The grammar is deliberately limited to 0–999 and explicit units. Wrong values,
units, signs, decimals, leading zeroes, malformed fragments and missing word
timestamps remain failures.

A queued audio-QC failure may claim one retained-voice continuation per original
root only when its private candidate metadata and existing blind Scribe response
prove a complete exact transcript with real word timings for the same audio
hash, channel, connection and lineage. All ancestor holds/cancellations remain
effective. This proof grants no audio or publication approval: the ordinary
saved-voice path downloads and validates the original bytes, rechecks the
unchanged narration independently, and runs prosody, media and final QC. It does
not authorize another synthesized voice. Lost queue acknowledgements retain
their one-time reservation and never trigger a repeat send.

Tests cover both ordinary worker entry paths, complete typed long-response
validation, concurrent queue claims, ancestor vetoes, immutable-history
preservation, corrupt/unknown transcript evidence and exact number equivalence.
