"""Narrow comparison for a Redis-cjson transport detail, never a job grant."""


def fresh_scheduled_spec_matches(stored, runtime):
    """Retain exact equality except the known empty editorial signal array.

    The scheduler authors scope_signals=[] for a focused Short. The paid-budget
    Lua journal re-encodes the whole job and Redis cjson can turn this one empty
    array into {}. Compare a copy at that exact path only; do not modify either
    frozen record, canonicalize other fields, or relax the caller's eligibility
    and private full-rebuild checks.
    """
    if type(stored) is not dict or type(runtime) is not dict:
        return False
    if stored == runtime:
        return True
    saved = stored.get('production_editorial')
    expected = runtime.get('production_editorial')
    fields = {'version', 'format', 'duration_minutes', 'reason_code', 'reason', 'scope_signals'}
    if (
        type(saved) is not dict or type(expected) is not dict
        or set(saved) != fields or set(expected) != fields
        or type(expected.get('version')) is not int or expected['version'] != 1
        or expected.get('format') != 'shorts'
        or type(expected.get('duration_minutes')) not in (int, float)
        or expected['duration_minutes'] != .5
        or expected.get('reason_code') not in {'focused_or_unspecified_scope', 'explicit_short_direction'}
        or type(expected.get('reason')) is not str or not expected['reason']
        or type(saved.get('scope_signals')) is not dict or saved['scope_signals']
        or type(expected.get('scope_signals')) is not list or expected['scope_signals']
    ):
        return False
    compared = {**stored, 'production_editorial': {**saved, 'scope_signals': []}}
    return compared == runtime
