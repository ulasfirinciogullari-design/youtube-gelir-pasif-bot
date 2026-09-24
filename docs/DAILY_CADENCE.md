# Capital and Margin daily delivery

The owner selected a ceiling of **one long video and five Shorts per channel per
Europe/Istanbul calendar day** on 2026-09-24. Capital and Margin are the only
managed channels. The separately managed animation channel is outside this rule.
The owner also requested an initial release of genuinely ready approved stock,
followed by a fresh daily batch. The activation audit found no such unqueued
ready master: two historical private uploads still lacked publication assets
or current delivery bindings. They are not silently released or duplicated.

The minute server scheduler runs independently of Studio and this task. Existing
ordered owner queues finish first. Afterwards a source-backed three-minute
documentary is installed as an ordinary queue item, followed by five Shorts.
The existing topic cursor, series numbering, source review, voice review, visual
review and final render gates remain active. An upper limit is not a guarantee
that six videos will pass quality review each day.

Production reservation and job creation happen in the same Redis transaction.
The three connected channels can each retain one queued or held assignment.
Two render processes execute those jobs; queue ownership does not reserve a
physical process. A held Margin/Framecase assignment therefore cannot prevent
Capital from joining the queue. The legacy topic scheduler still permits at
most two simultaneous legacy claims, and the combined queue refuses a fourth
channel assignment. Existing holds and their original records are never removed
to make room for another channel.

Publication has its own durable root claim before upload dispatch. Outstanding
publication claims count on subsequent days until the original delivery outcome
is known. A retry stays under the same root. Day rollover never deletes receipts,
replays an upload or resets paid work. A failed post-release bookkeeping write is
reconciled from the original source and publisher records. A slot reserved before
an upload record exists can re-enter ordinary publisher admission, which owns
the actual upload fence.

When an owner plan reaches 80 completed entries, its exact document and completion
records are archived before the next daily item is installed. Historical jobs,
provider journals and completion keys remain intact. An owner's removal of a
new, undispatched daily item is respected for that date.

Studio's Growth page shows the selected channel's daily counts. A video waiting
for the next calendar day remains viewable in its job page. Financial control is
separate: commissioning authorization does not alter existing spending receipts
or buy subscriptions, credits or automatic topups. The owner will choose the
final operating budget after commissioning.
## Rejected standalone documentaries

For Capital and Margin, a conclusively failed standalone documentary can move
to the plan's private attention list after its retained-media repair is exhausted,
or after a pre-speech factual/editorial rejection. Every ancestor, original
dispatch, terminal failure and provider receipt remains intact. Unknown native
requests, uploads, owner holds/cancellation, numbered episodes and unpublished
dependencies keep their hold. No completion receipt is fabricated. Existing
daily production reservations remain used, so a failed film cannot restart an
unlimited sequence of new long videos on the same day. The next eligible Shorts
or next day's documentary then use ordinary queue, budget and quality checks.

Long documentary critics constrain copied narration to the exact source lines.
A fully returned malformed critique gets at most one fresh assessment per batch;
the original response stays in history and an unknown transport is never retried.
An existing root that stopped before any speech or video after a malformed
critique can continue once under the same root and daily slot. All native model
responses must already be captured and the native voice/cash ledgers must prove
no media was submitted. A lost queue acknowledgement never causes another send.
A repeated failure of that child can move to attention under the rules above.
