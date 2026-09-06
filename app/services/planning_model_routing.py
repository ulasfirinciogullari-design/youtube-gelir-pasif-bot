"""Task-local fresh editorial routing; immutable recovery keeps its old route."""
from contextlib import contextmanager
from contextvars import ContextVar


_FRESH_ROUTE = ContextVar('studio_fresh_planning_route', default=None)


@contextmanager
def fresh_planning_route(*, enabled: bool):
    """Called only after the worker validates a fresh root or private rebuild.

    Never modify the shared settings object or accept route fields from a form,
    storyboard, or model response. ContextVar prevents concurrent tasks leaking
    routes into each other, and the token is reset even when planning fails.
    """
    if enabled is not True:
        yield None
        return
    from app.config import settings

    model = str(getattr(settings, 'studio_fresh_plan_openai_model', 'gpt-6-astra') or '').strip()
    if not model:
        raise RuntimeError('STUDIO_FRESH_PLAN_OPENAI_MODEL must not be empty')
    route = ('openai', model)
    token = _FRESH_ROUTE.set(route)
    try:
        yield {'provider': route[0], 'model': route[1]}
    finally:
        _FRESH_ROUTE.reset(token)


def planning_provider(settings) -> str:
    route = _FRESH_ROUTE.get()
    provider = route[0] if route else str(
        getattr(settings, 'studio_plan_provider', 'openai') or ''
    ).strip().casefold()
    if provider not in {'openai', 'gemini'}:
        raise RuntimeError('STUDIO_PLAN_PROVIDER must be openai or gemini')
    return provider


def planning_openai_model(settings) -> str:
    route = _FRESH_ROUTE.get()
    return route[1] if route else settings.openai_model


def planning_response_text(response) -> str:
    # A truncated or refused response must never pass merely because a JSON
    # substring happens to parse. Preserve legacy handling outside this scope.
    if _FRESH_ROUTE.get() and getattr(response, 'status', None) != 'completed':
        raise RuntimeError('Fresh editorial response did not complete')
    output = response.output_text
    if _FRESH_ROUTE.get() and (not isinstance(output, str) or not output.strip()):
        raise RuntimeError('Fresh editorial response has no text')
    return output
