# Fal video integration

Install `FAL_KEY` as a secret on both the web service and `video-worker`.
With this code deployed, the default `STUDIO_VIDEO_PROVIDER=auto` selects Fal
when the key is present and otherwise preserves the existing provider route.
The scheduler does not need this credential. Never put the key in source code,
logs, prompts or browser JavaScript.

`STUDIO_VIDEO_PROVIDER=fal` explicitly requires Fal, including stopping on a
missing key; `legacy` selects the previous Runway/Gemini route. This switch does
not create a new funding allowance, change channel/publication settings or
resume held jobs. Configure it consistently on the web and worker services.

## Models and selection

`STUDIO_FAL_VIDEO_MODEL=auto` uses silent Veo Lite for shots up to eight seconds,
and silent Seedance 1.5 Pro for nine/ten-second shots. It preserves the existing
Veo family wherever duration allows.

The separate private Gemini Omni preview keeps its explicit reference-image
route; these text-to-video profiles do not silently replace its continuity input.

Operators may select these fixed profiles:

| Profile | Fal endpoint | Duration | Silent 720p list price |
| --- | --- | --- | --- |
| `veo_lite` | `fal-ai/veo3.1/lite` | Rounded up to 4/6/8 seconds | $0.03/second |
| `seedance_pro` | `fal-ai/bytedance/seedance/v1.5/pro/text-to-video` | 4–10 seconds in this pipeline | $1.20/million video tokens |
| `seedance_fast` | `fal-ai/bytedance/seedance/v1/pro/fast/text-to-video` | 2–10 seconds in this pipeline | $1/million video tokens |

Token quotes use `(1280 × 720 × 24 × seconds) / 1024`, including portrait
transpose, rounded **up** to the next whole cent per request. These are list
cost reservations, not invoices or account-balance claims. Existing funding
evidence remains responsible for taxes and other account charges.

The catalog was reviewed on 23 September 2026. The same list prices were
re-checked on the pages below on 1 and 2 October 2026 (Veo Lite 720p silent
$0.03/s; Seedance Pro silent $1.2 and Seedance Fast $1 per million tokens),
so revision `fal-video-2026-10-01-v2` is valid from 23 September until
1 November 2026 UTC.
Sources: [Veo Lite](https://fal.ai/models/fal-ai/veo3.1/lite),
[Seedance Pro](https://fal.ai/models/fal-ai/bytedance/seedance/v1.5/pro/text-to-video),
[Seedance Fast](https://fal.ai/models/fal-ai/bytedance/seedance/v1/pro/fast/text-to-video).
Each page's `/api` tab documents its exact request fields.

Veo has audio and prompt auto-fix explicitly disabled. Seedance Pro has audio
disabled; both Seedance profiles keep their input safety checker enabled.
No reference images, extra frames, multiple samples, alternative resolutions
or unknown fields are admitted under these quotes. Prompts cannot choose a
model or increase a price cap. Unsupported durations fail before submission.

Wan Turbo is available in the Fal account but is not yet a production profile:
its documented endpoint exposes no duration selector. Admit it only after
verifying returned shot coverage and quality. Account access to a model does
not itself establish its suitability for the production timeline.

## Spending and recovery

The existing commissioned-video mode uses the same durable per-episode journal,
authority and call cap for Fal and Gemini. A reservation precedes the POST;
the encrypted create/result responses allow accepted requests to resume with
GETs. An unknown POST never replays. Changing model, key or provider cannot
replace an unfinished shot. Old direct-Gemini requests stay pinned; restore
their provider mode to finish them before migrating those jobs.

Normal enforced production requires Fal funding evidence bound to the actual
key, exact endpoint and current catalog price revision. New scene plans
enumerate the reviewed Fal shapes while keeping the existing scene/family
ceilings. Missing, expired or exhausted funding stops before HTTP dispatch.
No automatic top-up, ledger reset or provider fallback is introduced.

Every generated clip remains subject to the existing motion, duration,
identity and final visual-quality checks. Model selection is not approval and
no equal-quality claim has been validated with paid comparison footage.

## Verification and rollout

1. Deploy this change on the same branch/version as the current web and worker.
2. Ensure both services have `FAL_KEY`; leave both model/provider settings at
   `auto`, or explicitly set the desired reviewed profile.
3. Verify the existing commissioning authority or normal Fal funding evidence.
   A key alone does not override the application's spending rules.
4. Inspect the next authorized job's provider record: `fal_veo_lite`,
   `fal_seedance_15_pro` or `fal_seedance_1_fast`. Confirm a successful queue
   result, saved clip and normal QA before considering the live path verified.

Offline tests cover model-specific payloads, quotes, credential and scene
binding, unknown-outcome replay prevention, queue URL validation, episode
capacity, recovery and the existing download/preservation paths. They do not
verify a live Fal key or account credit balance.
