# Long-form delivery family — staged, not commissioned

## Product target, not current throughput

The current owner target is continuous server operation: observe relevant trends
and channel performance, select original suitable topics, produce and review
videos, and publish up to the channel's permitted daily capacity while respecting
actual API quotas, approved spending and quality. No numeric channel limit or
unlimited production allowance has been verified. Existing series keep their
episode order; trend selection must not rewrite consumed topics or resurrect
deleted videos. The user's computer and an open conversation must not be needed.

Keep a bounded stock of completed, individually reviewed videos so eligible
publication can continue through a temporary production outage. Replenish below
the configured stock target; stop new paid production when it is full. Select
timely videos before they become stale while preserving series order, and
recheck publication eligibility and exact accepted assets before release. The
stock target, freshness policy and independent publisher are not commissioned.

The earlier **five Shorts plus one roughly eight-minute long per day across both
channels combined** is now a reference workload, not the owner's permanent cap.
Its efficient format remains useful: three Shorts reuse the long video's authored
stories, narration and moving footage, with two independent originals. The current
two-production concurrency guard is unchanged; a higher publication target alone
does not authorize more simultaneous paid jobs or prove throughput. Channel/series
memory, metadata, thumbnails and verified release followed by next eligible work
remain required.

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
9. A separate server task requests owned-video metrics refresh every five minutes
   using the existing channel/account/publication proofs. Background reads skip
   fresh observations and reconnect-required channels, wait longer after quota
   errors, and share the manual refresh lock. Empty or unavailable job discovery
   leaves prior observations untouched. This removes the browser trigger
   dependency; it does not add measured trend selection, retention or revenue.

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
- Dynamic selection under channel/API/spending limits and the reviewed-video
  stock are not yet commissioned. The former five-Shorts/one-long reference mix
  is not a hard daily target. This patch enables an explicitly broader topic to
  become eight minutes; it does not replace existing series topics or prove
  capacity. Measured trend and performance evidence still needs integration
  into future editorial selection.
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
- Metrics observation shares the existing worker queue. Two occupied render
  workers can delay or expire an observation, so five minutes is its requested
  schedule, not a guaranteed collection interval. Worker capacity must be checked
  at commissioning; the existing short refresh lock is not a guarantee that
  unusually long read requests can never overlap.
- The shared metrics cache still represents only the queried subset, at most
  50 videos per channel. A later nonempty subset can replace unqueried older
  observations, including absence evidence. Durable full publication/deletion
  history remains a separate requirement; do not use this cache as its ledger.

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
- Fill and drain the reviewed-video stock independently of the producer; do not
  publish an expired topical story or a later serial episode just to fill a slot.
  A channel upload limit and a project API quota are separate stops. Keep the
  accepted video and its receipt when either stops dispatch; uncertain upload
  outcomes still require reconciliation before any resubmission.

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
