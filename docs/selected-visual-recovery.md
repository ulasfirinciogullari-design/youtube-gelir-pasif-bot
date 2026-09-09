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

## Read-only verification and remaining recovery work

`load_selected_visual_checkpoint` requires independently resolved current
binding and storyboard hash, validates the whole manifest and reads every saved
object to verify byte identity. It returns private candidates. It creates no
render input directory, repair claim, budget allowance, model request or upload.
All approval/reuse/publication flags remain false.

The job stores only the separate `selected_visual_checkpoint` pointer. It does
not set `repair_available`, replace `result.video_key`, mark success, clear the
channel hold or advance its series cursor. Existing raw-generation journals
remain separate; this checkpoint preserves the final selected candidate set.

An actual recovery dispatcher still needs a single-use claim, verified unchanged
accepted inputs, exact-cut and final audio/video QA, and the original scene /
family / month / additional-cash admission before any new paid repair. Legacy
paid jobs without a reconciled immutable scene plan cannot acquire a fresh
zero-used plan from this checkpoint. No existing failed production job is made
eligible retroactively, and no automatic repair or unattended production claim
follows from these preservation tests.
