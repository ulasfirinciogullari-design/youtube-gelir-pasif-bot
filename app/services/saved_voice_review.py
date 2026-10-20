"""Keep unapproved narration across failed subscription-funded story reviews."""


def review_options(task_id, package, voice_result):
    from app.services.production_included_router import enabled
    if not enabled():
        return {}
    from app.services.audio_checkpoint import persist_audio_candidate_checkpoint
    from app.services.studio_state import get_job, update_job

    # The normal checkpoint writer validates and stores the original bytes,
    # exact spoken text and frozen plan, explicitly requiring every QA gate.
    # Save before the independent critic so a critic/transport failure cannot
    # make the next retry lose the already-paid narration.
    fields = persist_audio_candidate_checkpoint(task_id, package, voice_result)
    update_job(task_id, **fields, audio_candidate_checkpoint_error=None)
    current = get_job(task_id)
    if not isinstance(current, dict) or current.get('audio_candidate_checkpoint') != fields['audio_candidate_checkpoint']:
        raise RuntimeError('Saved narration checkpoint acknowledgement unavailable')
    # A review-only continuation must not ask a writer to replace stock
    # queries behind the existing narration. The complete fresh independent
    # source/story critic still runs against the original plan.
    return {'immutable_stock_routes': True}
