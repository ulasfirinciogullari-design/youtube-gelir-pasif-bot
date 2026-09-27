"""An explicit same-channel OAuth renewal within the existing prepaid pool.

Each new connection gets a separate anchored grant. The original funding,
activation, requests and root choices stay immutable; an old root never moves
to the new connection. Reading a new OAuth record alone grants nothing.
"""
import json
import re

from app.services import kie_voice_ledger as ledger, youtube_auth as auth

PREFIX = ledger.PREFIX + 'reconnected_channel:'


def _keys(channel, connection):
    ledger.require(channel in ledger.CHANNELS and type(connection) is str
        and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', connection), 'kie_voice_reconnection_invalid')
    key = PREFIX + channel + ':' + connection
    return key, key + ':anchor'


def _current(pipe, channel):
    keys = (auth.CHANNEL_PREFIX + channel, auth.CREDENTIAL_PREFIX + channel)
    pipe.watch(*keys)
    encoded, credential = (pipe.get(key) for key in keys)
    if encoded is None:
        return None, None, None
    ledger.require(type(encoded) is str and len(encoded) <= 32768,
                   'kie_voice_reconnection_invalid')
    return json.loads(encoded), encoded, credential


def read(pipe, policy, channel):
    """Resolve only a separately authorized grant for the current connection."""
    current, encoded_channel, credential = _current(pipe, channel)
    if current is None:
        return None
    connection = current.get('connection_id')
    key, anchor_key = _keys(channel, connection)
    pipe.watch(key, anchor_key)
    encoded, anchor = pipe.get(key), pipe.get(anchor_key)
    if encoded is None and anchor is None:
        return None
    ledger.require(type(encoded) is str and len(encoded) <= 4096
        and anchor == ledger.sha(encoded) and pipe.pttl(key) == pipe.pttl(anchor_key) == -1,
        'kie_voice_reconnection_unverified')
    from app.services.kie_voice_production import activation
    active = activation(pipe, policy)
    record = json.loads(encoded)
    ledger.require(type(record) is dict and set(record) == {
        'version', 'purpose', 'channel_id', 'base_connection_id', 'connection_id',
        'policy_sha256', 'activation_sha256', 'channel_sha256', 'credential_sha256',
        'continuation_sha256', 'authorized_at', 'owner_evidence_sha256',
        'allocation_added_microcredits'} and type(record['version']) is int
        and record['version'] == 1 and record['purpose'] == 'same_channel_kie_voice_reconnection'
        and record['channel_id'] == current.get('id') == channel
        and record['base_connection_id'] == policy['channels'][channel]
        and record['connection_id'] == connection != record['base_connection_id']
        and record['policy_sha256'] == ledger.sha(ledger.raw(policy))
        and active is not None and record['activation_sha256'] == ledger.sha(ledger.raw(active))
        and record['channel_sha256'] == ledger.sha(encoded_channel)
        and type(credential) is str and bool(credential)
        and record['credential_sha256'] == ledger.sha(credential)
        and type(record['allocation_added_microcredits']) is int
        and record['allocation_added_microcredits'] == 0
        and type(record['owner_evidence_sha256']) is str
        and re.fullmatch('[0-9a-f]{64}', record['owner_evidence_sha256']),
        'kie_voice_reconnection_unverified')
    ledger._stamp(record['authorized_at'])
    ledger.require(ledger._connected_owner(pipe, channel, connection) == record['continuation_sha256'],
                   'kie_voice_reconnection_authority_changed')
    return record


def commission(foundation, channel, *, expected_connection_id, owner_evidence_sha256):
    """Private operator action after owner verification; never buys or refunds."""
    ledger.require(type(owner_evidence_sha256) is str
        and re.fullmatch('[0-9a-f]{64}', owner_evidence_sha256), 'kie_voice_reconnection_invalid')
    key, anchor_key = _keys(channel, expected_connection_id)
    from app.services import kie_voice_production as production, youtube_automation as profiles
    with foundation.client.pipeline() as pipe:
        policy, _, _ = ledger._read(pipe)
        ledger.require(policy is not None and channel in policy['channels'])
        ledger._foundation(pipe, foundation)
        active = production.activation(pipe, policy)
        ledger.require(active is not None, 'kie_voice_activation_missing')
        current, encoded_channel, credential = _current(pipe, channel)
        ledger.require(current is not None and current.get('id') == channel
            and current.get('connection_id') == expected_connection_id != policy['channels'][channel]
            and type(credential) is str and bool(credential), 'kie_voice_connection_changed')
        authority = ledger._connected_owner(pipe, channel, expected_connection_id)
        profile_key = profiles.PROFILE_PREFIX + channel
        pipe.watch(profile_key, key, anchor_key)
        profile = json.loads(pipe.get(profile_key) or '{}')
        ledger.require(profile.get('channel_id') == channel and profile.get('production_enabled') is True
            and profile.get('auto_publish') is True, 'kie_voice_channel_not_authorized')
        existing = read(pipe, policy, channel)
        if existing is not None:
            ledger.require(existing['owner_evidence_sha256'] == owner_evidence_sha256,
                           'kie_voice_reconnection_already_authorized')
            pipe.multi(); pipe.ping(); ledger.require(pipe.execute() == [True])
            return {'status': 'already_active', 'channel_id': channel, 'allocation_added_microcredits': 0}
        record = {'version': 1, 'purpose': 'same_channel_kie_voice_reconnection',
            'channel_id': channel, 'base_connection_id': policy['channels'][channel],
            'connection_id': expected_connection_id, 'policy_sha256': ledger.sha(ledger.raw(policy)),
            'activation_sha256': ledger.sha(ledger.raw(active)),
            'channel_sha256': ledger.sha(encoded_channel), 'credential_sha256': ledger.sha(credential),
            'continuation_sha256': authority, 'authorized_at': ledger.now().isoformat(),
            'owner_evidence_sha256': owner_evidence_sha256, 'allocation_added_microcredits': 0}
        encoded = ledger.raw(record)
        pipe.multi(); pipe.set(key, encoded, nx=True); pipe.set(anchor_key, ledger.sha(encoded), nx=True)
        ledger.require(pipe.execute() == [True, True], 'kie_voice_reconnection_write_uncertain')
    return {'status': 'active', 'channel_id': channel, 'allocation_added_microcredits': 0}
