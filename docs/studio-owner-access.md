# One-time owner access after Studio key rotation

Rotating `FACTORY_API_TOKEN` invalidates old Studio cookies and header credentials.
It must not call Studio logout or reconnect YouTube: logout increments the OAuth
authorization epoch, which is part of retained-source continuity. The provider
credential store, financial journals, source assets and OAuth tokens remain
separate from the Studio session credential.

An operator can mint a 256-bit one-time grant directly on the server after the
new strong session key is deployed. No HTTP endpoint can mint a grant, and an
old Studio cookie cannot authorize one. The grant binds the dedicated application
encryption key and current session-key fingerprint. Its encrypted Redis record
uses the grant digest as the key and expires; lifetime defaults to one hour and
is bounded to at most 24 hours.

The owner opens `/studio/access#<grant>` through a privately delivered link.
The fragment is never part of the HTTP request URL. The page takes it into a
script closure and removes it from browser history; only the owner's button
submits it in a bounded same-origin POST body. A GET does not consume the grant.
Neither the grant nor the session key is rendered into HTML or error messages.

Consumption validates the encrypted record, current context and remaining TTL,
then deletes the grant with a watched acknowledgement before issuing the
existing secure, HttpOnly, SameSite=Strict Studio cookie. Replays, expired or
changed records, races and uncertain acknowledgements cannot issue a cookie or
retry automatically. Success redirects to the Abacus key-entry page; it does
not verify or promote a provider key, dispatch production or initialize spending.

The live rotation procedure stages a new random key privately, preserves the
existing local vault and all other service variables, and changes only the
Studio token on web, worker and scheduler. Cloud configuration updates have no
cross-service atomic compare-and-swap guarantee. Uncertain updates require a
read-only comparison with the preserved before/new values; they must not be
blindly repeated. Grants are minted only after the new runtime and local scoped
access agree on the stronger token.
