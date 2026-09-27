"""Which YouTube channels this deployment manages.

Defaults are the live bot's Capital and Margin channels. A separate deployment
(the new system on a test channel) points Capital at its own channel with
STUDIO_CAPITAL_CHANNEL_ID and turns Margin off with an empty
STUDIO_MARGIN_CHANNEL_ID, so it never produces for the live bot's channels.
"""
import re

CAPITAL_DEFAULT = 'UC5v9AvNtD3PTLgo6m1jROOA'
MARGIN_DEFAULT = 'UCgvESYtYbn2w9R2ExBOF_cw'
_PATTERN = re.compile(r'UC[0-9A-Za-z_-]{22}')


def _configured(name: str, default: str) -> str:
    try:
        from app.config import settings
        value = getattr(settings, name, default)
    except Exception:
        value = default
    value = str(default if value is None else value).strip()
    if value and not _PATTERN.fullmatch(value):
        raise ValueError(f'{name.upper()} must be a YouTube channel id (UC...) or empty')
    return value


CAPITAL = _configured('studio_capital_channel_id', CAPITAL_DEFAULT)
MARGIN = _configured('studio_margin_channel_id', MARGIN_DEFAULT)
if not CAPITAL:
    raise ValueError('STUDIO_CAPITAL_CHANNEL_ID must not be empty')
if CAPITAL == MARGIN:
    raise ValueError('Capital and Margin must be different channels')
# Managed channels in a stable order; Margin drops out when it is turned off.
MANAGED = tuple(channel for channel in (CAPITAL, MARGIN) if channel)
