"""Lossless provider input for already-approved explicit repair shots.

The caller still owns package approval, scene selection and the paid ledger.
This pure boundary must run for every selected repair before any reservation.
It does not rewrite a shot from preliminary stock-review/search suggestions.

The existing ``generate_scene`` route begins with Runway and may fall back to
Veo. Its API contract is 1,000 UTF-16 units, not the old composer's arbitrary
900-unit budget. Gemini documents 1,024 *tokens*, not characters:
https://ai.google.dev/gemini-api/docs/veo#model-versions
That is not interchangeable with this route's limit. A future direct-Gemini
route needs its own token validation; this helper does not select one or
silently truncate a longer direction to fit either provider.
"""
from __future__ import annotations


RUNWAY_FALLBACK_PROMPT_UTF16_LIMIT = 1000


class ProductionShotPromptError(ValueError):
    """Safe local failure; contains no authored text or provider details."""


def build_production_shot_prompt(
    scene: dict,
    *,
    provider_route: str = 'runway_fallback',
) -> str:
    """Return the complete authored direction, or fail before a paid create.

    No narration, prior critic query, synthetic identity clause or camera
    instruction is prepended. The approved direction itself is the contract;
    all ordinary identity, temporal, artifact and final quality gates remain.
    """
    if provider_route != 'runway_fallback' or not isinstance(scene, dict):
        raise ProductionShotPromptError('Unsupported production shot prompt contract')
    prompt = scene.get('ai_prompt')
    if (
        not isinstance(prompt, str) or not prompt.strip()
        or prompt != prompt.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in prompt)
    ):
        raise ProductionShotPromptError('Production shot direction must be explicit plain text')
    try:
        units = len(prompt.encode('utf-16-le')) // 2
    except UnicodeError:
        raise ProductionShotPromptError('Production shot direction contains invalid text') from None
    if units > RUNWAY_FALLBACK_PROMPT_UTF16_LIMIT:
        raise ProductionShotPromptError('Complete production shot direction exceeds the active provider route capacity')
    return prompt
