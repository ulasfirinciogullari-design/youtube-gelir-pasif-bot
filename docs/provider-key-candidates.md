# Abacus key entry in Studio

`/studio/providers/abacus` lets the signed-in owner enter a replacement key
without a terminal or sending the secret through chat. It stages a candidate;
neither a successful save nor a public model catalogue proves provider access.
The response never displays the supplied value.

The route authenticates the existing Studio cookie and checks same-origin form
submission before reading a bounded request body. Only one form field is
accepted. Responses use no-store, a password input, same-origin referrer policy
and restrictive framing/content headers. Validation uses fixed messages and
does not return framework errors containing the submitted body.

The service encrypts the complete candidate with the existing dedicated
`APP_ENCRYPTION_KEY` and a provider-candidate domain separator. Ciphertext binds
the provider, purpose, namespace and original active-key fingerprint. There is
no session-token or API-key encryption fallback. A single non-expiring Redis
record is created with NX; a read acknowledgement checks exact bytes and
durability. A lost write/read acknowledgement produces no success claim and
does not delete or retry a potentially saved candidate. An existing candidate
cannot be overwritten by another submit.

Only the operator service API can decrypt the candidate; HTTP status exposes
presence and pending verification only. There is no provider request, model
call, key promotion, environment update, spending initialization, journal reset,
job dispatch or publication in this feature. Previous unknown requests stay
occupied even when an owner supplies a different key. Verification and a
separately reviewed recovery operation are still required before production.

The server's local `configure-youtube-abacus` fallback also stages a private
candidate without changing the active credential store. The local file and
Studio Redis candidate are separate entry mechanisms; neither promotes itself
or authorizes another generation request.
