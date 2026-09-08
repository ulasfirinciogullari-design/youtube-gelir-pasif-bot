# Long-form delivery family — staged, not commissioned

## Product target, not current throughput

The starting cadence target is **five Shorts plus one roughly eight-minute
long video per day across both channels combined**, not per channel. Three Shorts
reuse the long video's authored stories, narration and moving footage; two are
independent originals. Two concurrent cloud productions, channel/series memory,
metadata, thumbnails, optional short virtual-presenter segments, and verified
public release followed by the next eligible production remain the complete
product scope. The user's computer and an open desktop conversation must not be
required for runtime production.

The earlier USD368.80/month comparison, approximately USD260/month API estimate
and USD500/month reserve came from the assistant. They are not user-approved
envelopes, allowances or spending targets, mandatory purchases, measured
throughput or prices covering all personal subscriptions. No new monthly limit
has been determined. Prefer existing legitimate rights, beginning with the
user's Abacus AI subscription, after verifying plan credits, commercial output
rights and supported automation access. Do not silently buy new subscriptions,
enable cash overage or turn an old estimate into a limit. The 8 September account
audit remains historical billing evidence, subject to this correction and
verification of current account entitlements.

## This change implements

1. Explicit eight-minute editorial selection, preserving legacy 30-second/three-
   minute behavior when disabled and respecting an explicitly Shorts-only channel.
2. A single research/director package with exactly three distinct, contiguous,
   whole-scene Shorts selections, each with its own hook, complete answer, title
   and description. The final scene narration is hash-bound before voice/media.
3. Exact scene frame boundaries from the actual rendered CFR master, rather than
   guessed timestamps. A persisted content-bound manifest survives local cleanup.
4. A cloud family job that downloads the same verified master once and produces
   three 1080x1920 candidates without TTS, image/video generation or speed changes.
   The center crop is a candidate composition, not a claim that subjects fit.
5. Durable dispatch/execution claims, channel/connection/profile/cancellation
   rechecks, separate child records and preservation of a successful long master
   if a derivative export fails. Broker uncertainty never repurchases the parent.
6. The existing cloud minute tick can discover a missed family enqueue; accepted
   or uncertain claims are not blindly resent. Long-family failures do not trigger
   the old automatic whole-pipeline rebuild.
7. New family executions checkpoint each successful portrait independently. A
   positively recorded CPU render/export failure may receive one automatic
   recovery execution. It verifies retained job records and stored media hashes,
   keeps the original child IDs, and renders only missing cuts. It grants no
   paid media generation or publication permission. Unknown broker outcomes,
   hard-interrupted executions and old uncheckpointed failures stay held.
8. Legacy series promotion now rejects indexed outstanding delivery-family jobs
   in the same channel, including successful masters and private derivatives.
   A mutable completion label cannot authorize a profile change that would
   orphan the family. Other channels retain their normal eligibility.

The feature defaults OFF (`studio_longform_delivery_enabled=false`). Its scheduler
also requires the spending guard to be ON; this check does not initialize a ledger
or make incomplete provider quotes usable. The unchanged legacy path does not
capture new frame-map diagnostics or queue these derivative jobs.

## Deliberately not claimed complete

- The three derived files are **private candidates awaiting automated review**.
  They do not inherit landscape QA, set `automated_qc_pass`, consume series numbers,
  or call YouTube upload. Actual portrait/audio review, immutable accepted proof,
  derived-series numbering and public-release ordering still need integration.
- The current legacy series promotion remains Shorts-specific; it must not rotate
  a long-form family's profile while its derivatives are outstanding. The new
  indexed-job guard closes the known premature-rotation path, but durable
  discovery after job expiry and validated family-publication receipts still
  need integration before long-family rotation can be commissioned.
- The combined daily five-Shorts/one-long selection policy is not yet commissioned.
  This patch enables an explicitly broader topic to become eight minutes; it does
  not secretly replace existing series topics or treat the target as a guarantee.
- Ambiguous dispatches and hard-interrupted CPU executions remain visible holds.
  One bounded recovery of a caught render/export failure is now supported; a
  second failure, missing/changed checkpoint or child cancellation stays held.
  This does not establish unattended recovery from every worker interruption.
- Required speech/multimodal/grounded-planning price bounds, remaining-period
  reconciliation of real usage and actual budget activation remain open. The
  explicit opening-usage import and bounded Gemini text quote are implemented;
  they have not been commissioned against production. Existing failed parent
  production holds are not cleared by this change.
- Virtual-presenter provider commissioning, dubbing and broader UI work are still
  part of the overall workflow; adding this fan-out does not complete them.

Do not enable the new family flag in production until the remaining publication,
review, ordering and budget conditions above are implemented and verified.

## App-independent operation acceptance

The following are required acceptance checks, not implemented-feature or 24/7
throughput claims. Record evidence from the commissioned workflow before
describing it as unattended:

- With the desktop app and conversation closed, the server scheduler and worker
  still discover, execute and monitor eligible work. Production must not depend
  on a chat turn, an open user computer or a manually issued next-job command.
- At a configured paid limit, new paid dispatch stops across primary and fallback
  routes. Eligible free monitoring, accepted-operation polling and reuse of
  existing assets may continue without clearing spending history or quality holds.
- After independent video/audio acceptance, verify an actual public YouTube
  release receipt and channel/episode order. That receipt makes the next eligible
  job discoverable; analytics arrival must not be a prerequisite for continuation.
- Restart a scheduler or worker and verify checkpoint recovery preserves accepted
  artifacts and job identities. An uncertain provider or broker outcome must
  remain held or be reconciled, never blindly replayed as a new paid request.
- Observe the full production → review → public release → next eligible video
  chain, including a budget stop and recovery, on the server. Passing offline
  tests, keeping a process alive or seeing one completed render is not proof of
  24/7 operation or the daily cadence target.

## Verification

Run the complete suite and media fixtures on the GitHub-hosted CI runner or the
verified YouTube OVH development server, with bounded CPU/memory. The owner
reported severe PC slowdown; do not run rendering, test suites, dependency
installation or builds on the owner's PC. This task verified the existing OVH
checkout directly; no setup was repeated. The existing Railway services remain
the production execution host; credentials and production services were not
migrated to the development server.

Offline tests use fake provider/storage transports and real Redis semantics via
fakeredis. A tiny, locally generated synthetic FFmpeg fixture verifies the actual
portrait cut, original audio-stream presence, frame count and duration. This is
technical evidence only, not an editorial review of a real channel video and not
evidence of daily production capacity. No paid generation is required to run it.
