# Preserved inputs for a rejected Shorts edit

A failed final visual review can leave several useful stock or generated clips
alongside the failed scenes. Previously, the general production path retained
some generated candidates and a rejected workprint, while the older repair
checkpoint supported only a small preview case. It did not preserve every
selected stock/AI input for a mixed production edit.

The terminal rejection branch now attempts a private selected-candidate
checkpoint before the existing workprint and temporary-directory cleanup. Its
scope is an approximately 30-second production Short with music off and all
three narration/duration/prosody gates passed. It supports every nonempty failed
scene split, including three failures out of six and all six failures. Failed
preservation leaves the original quality rejection unchanged.

## What is retained

- The original full storyboard, excluding only the two recovery attachment
  fields used by the existing storyboard fingerprint. It is not reconstructed
  from the narrower narration checkpoint.
- The final selected voice bytes, spoken-text metadata and measured scene
  timing. Requested duration, effective edit target and frame allocation are
  separate; a naturally shorter accepted voice is not forced into 900 frames.
- The selected candidate for every scene, including stock, with actual bytes,
  SHA256, size, source metadata, selected fraction and proposed render timing.
  Multi-candidate selection requires the recorded best index. Missing or
  conflicting scene evidence does not silently choose another file.
- The renderer source fingerprint, shot/crop selector and frame allocation.
  A changed renderer makes the loader ineligible. Adaptive letterbox cropping
  has not run at this rejection point and is explicitly marked unverified;
  preserving a proposed cut is not proof of its final rendered pixels.
- Historical final quality observations and the complete accepted-candidate /
  rejected partition. These observations do **not** prove that the final critic
  reviewed the subsequently selected exact cut. `qa_input_verified=false` and
  `exact_cut_qa_required=true` preserve that distinction.
- Server task, original lineage, channel and connection identities, plus a hash
  of the complete bounded ancestry and each server-authored job specification.
  Changed episode/profile details or intermediate parent links change the hash;
  ordinary progress, failure and checkpoint fields do not.

The context resolver uses bounded WATCH/EXEC/PING reads. It neither requires
nor initializes a spending ledger. A stable stored channel connection is not
proof of a fresh Google OAuth refresh.

Private objects are content-addressed and create-only. An existing object is
accepted only after reading and checking its actual bytes, not just its claimed
metadata. The manifest is written last. Individual videos are bounded to
100 MiB, all selected video inputs to 512 MiB, audio to 14 MiB and the manifest
to 2 MiB. A failed write can leave unreferenced private objects; it never triggers
new media generation or a refund.

## Verification and staged V6 recovery

`load_selected_visual_checkpoint` requires independently resolved current
binding and storyboard hash, validates the whole manifest and reads every saved
object to verify byte identity. It returns private candidates. It creates no
render input directory, repair claim, budget allowance, model request or upload.
All approval/reuse/publication flags remain false.

The job stores only the separate `selected_visual_checkpoint` pointer. It does
not set `repair_available`, replace `result.video_key`, mark success, clear the
channel hold or advance its series cursor. Existing raw-generation journals
remain separate; this checkpoint preserves the final selected candidate set.

The V6 materializer now retains the verified bytes in a private child work
directory. It restores the original voice profile and measured timing, leaves
rejected scene slots empty, and classifies retained stock and generated media
separately. Its repair indices must exactly match the original rejected set,
including all six rejected scenes or a mixed twelve-scene edit. It cannot accept
new caller-authored story or generation instructions.

The worker's V6 path freshly critiques the unchanged story without a stock
writer or script repair. Existing authored shot directions are transmitted
losslessly; a rejected stock scene uses the existing bounded prompt composer
against its original scene and stored rejection. A fresh exact-cut review of
the retained scenes must pass before any replacement create. Only original
rejected indices enter the paid loop, under the original scene/family and
additional-cash counters. Saved voice reuse never calls TTS or timing surgery.

Final review normalizes the actual selected cuts, including adaptive crop,
transitions and the measured final tail/trim, into separate QA copies. Fresh
semantic gates and scores must pass for every scene. These copies are never
render sources. Immediately before final render the worker rechecks original
audio, raw clips, selection, frame allocation, renderer and QA-copy identities.
Existing final audio/video and publication gates still apply. A negative final
review preserves the rejection; it cannot trigger an extra stock search or paid
replacement outside the frozen partition.

`prepare_selected_visual_recovery` verifies and stores a private immutable
candidate. `publish_selected_visual_recovery` stages that private checkpoint
with WATCH/NX and a durable receipt; its name does not mean YouTube publication.
It sets no runnable repair flag and consumes no claim or money. Generic retry,
legacy checkpoint consumption and dashboard availability synchronization keep
this V6 candidate fenced, even after the expiring checkpoint disappears.

A future commissioned dispatcher must atomically bind the immutable staged
record hash to the existing single-use repair claim. The read-only worker
verifier requires that proof, current owner/channel/profile/ancestry and an
unchanged full server package before materialization and subsequent paid/render
boundaries. Current visual/audio QA price and funding admission still need
commissioning before this dispatch can be enabled. Legacy paid jobs without a
reconciled immutable scene plan cannot acquire a fresh zero-used allowance.
Historical failed jobs without the selected-input checkpoint are not made
eligible retroactively. This implementation is staged, not live automation.
