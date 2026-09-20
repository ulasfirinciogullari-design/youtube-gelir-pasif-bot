# Renewing a YouTube connection

The channel page distinguishes a stored connection from a fresh successful
measurement. A cached Google permission error produces a reconnect notice on
both the channel card and the dashboard. Rendering these notices reads cached
data; it does not refresh credentials or remove a connection.

Each channel has its own **Yeniden bağla** action. The authenticated,
same-origin POST binds that channel ID into the encrypted, one-use OAuth state.
Targeted states use version 3, so a preceding application version rejects them
instead of completing a reconnect without checking its target during a rollout.
After Google verifies the selected channel, the callback checks that it matches
the requested channel before storing credentials. Selecting a different brand
or personal channel produces a recovery message and preserves existing channel
records. The general **Google ile kanal bağla** action still adds a new channel.

Reconnect keeps the browser binding, PKCE, expiry and global authorization epoch
checks. A second authorization, disconnect or callback replay cannot bypass
those checks. Existing channels can be renewed even at the channel-count limit.

Renewed Google access does not clear production holds, change topic order,
authorize an old captured request or establish API funding. Those checks remain
separate. Verification uses synthetic Google transport and exercises both
matching and mismatched channel selection without real consent or publication.
