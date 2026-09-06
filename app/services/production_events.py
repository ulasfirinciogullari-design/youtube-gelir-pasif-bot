"""Best-effort wake-up only; the normal dispatcher owns every job decision."""


def request_production_tick() -> bool:
    try:
        from app.production_tasks import production_tick

        # Do not wait through broker retries or let a wake-up failure replace
        # an already-committed publication/delivery result. Beat is the fallback.
        production_tick.apply_async(expires=55, retry=False)
        return True
    except Exception:
        return False
