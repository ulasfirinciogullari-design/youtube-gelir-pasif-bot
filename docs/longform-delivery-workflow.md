# Long-form delivery family — staged, not commissioned

## Product target, not current throughput

The budgeted starting workload is **five Shorts plus one roughly eight-minute
long video per day across both channels combined**, not per channel. Three Shorts
reuse the long video's authored stories, narration and moving footage; two are
independent originals. Two concurrent cloud productions, channel/series memory,
metadata, thumbnails, optional short virtual-presenter segments, and verified
public release followed by the next eligible production remain the complete
product scope. The user's computer and an open desktop conversation must not be
required for runtime production.

The earlier working estimate was USD368.80/month, with reserve to USD500. This is
a comparison scenario, not an activated spending allowance, mandatory purchase,
measured throughput or all-personal-subscriptions-included price. Prefer existing
rights; do not silently buy new subscriptions or increase limits. The detailed
8 September account audit remains the authority for billing assumptions.

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
  a long-form family's profile while its derivatives are outstanding.
- The combined daily five-Shorts/one-long selection policy is not yet commissioned.
  This patch enables an explicitly broader topic to become eight minutes; it does
  not secretly replace existing series topics or treat the target as a guarantee.
- Ambiguous execution and failed exports remain visible, once-claimed holds.
  Recovery of partially completed CPU jobs without regenerating paid assets is a
  separate step, not a working self-healing/24x7 claim.
- Required speech/multimodal/grounded-planning price bounds, remaining-period
  accounting and actual budget activation remain open. Existing failed production
  holds are not cleared by this change.
- Virtual-presenter provider commissioning, dubbing and broader UI work are still
  part of the overall workflow; adding this fan-out does not complete them.

Do not enable the new family flag in production until the remaining publication,
review, ordering and budget conditions above are implemented and verified.

## Verification

Run the complete suite and media fixtures on the GitHub-hosted CI runner. The
owner reported severe local PC slowdown; do not start local render jobs, full
test suites, dependency installs or builds. A new server does not migrate the
memory used by the owner's open desktop apps or their browser sessions. The
existing Railway services remain the production execution host. This branch
does not purchase a server or relocate the current Codex conversation.

Offline tests use fake provider/storage transports and real Redis semantics via
fakeredis. A tiny, locally generated synthetic FFmpeg fixture verifies the actual
portrait cut, original audio-stream presence, frame count and duration. This is
technical evidence only, not an editorial review of a real channel video and not
evidence of daily production capacity. No paid generation is required to run it.
