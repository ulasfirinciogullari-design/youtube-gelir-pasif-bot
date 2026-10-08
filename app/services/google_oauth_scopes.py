"""Accept Google's incremental scope superset without repeating token exchange.

The response is parsed by oauthlib before it raises its scope-change Warning.
Keep the required-scope check and parse the same token again with its actual
grants. Never relax scope validation globally or trust callback query scopes.
"""
import json

from oauthlib.oauth2.rfc6749.parameters import parse_token_response
from oauthlib.oauth2.rfc6749.tokens import OAuth2Token


def fetch_google_token(flow, *, code, required_scopes):
    try:
        return flow.fetch_token(code=code, timeout=20)
    except Warning as warning:
        token = getattr(warning, 'token', None)
        granted = getattr(warning, 'new_scope', None)
        if (type(warning) is not Warning or type(token) is not OAuth2Token
                or not isinstance(granted, (list, tuple, set)) or not 1 <= len(granted) <= 32
                or any(type(scope) is not str or not 1 <= len(scope) <= 2048 for scope in granted)
                or not set(required_scopes).issubset(granted)
                or set(token.get('scope', [])) != set(granted)):
            raise
        # Reparse only the already received token; do not resend the one-use
        # authorization code. The session setter also updates its HTTP client.
        accepted = parse_token_response(json.dumps(dict(token)), scope=list(granted))
        flow.oauth2session.token = accepted
        return accepted
