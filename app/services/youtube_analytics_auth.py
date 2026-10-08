"""Optional Analytics grant bound to an existing connection, without replacing it."""
import hashlib
import re

from app.services import youtube_auth as auth, youtube_metrics as metrics

SCOPES = ['https://www.googleapis.com/auth/yt-analytics.readonly',
          'https://www.googleapis.com/auth/youtube.readonly']
PREFIX = 'youtube_studio:analytics:credential:v1:'
_COMMIT = '''
if (redis.call('GET', KEYS[1]) or '0') ~= ARGV[1]
 or redis.call('GET', KEYS[2]) ~= ARGV[2]
 or redis.call('GET', KEYS[3]) ~= ARGV[3]
 or redis.call('SISMEMBER', KEYS[4], ARGV[4]) ~= 1 then return 0 end
redis.call('SET', KEYS[5], ARGV[5])
redis.call('INCR', KEYS[1])
return 1
'''


def _digest(cipher):
    return hashlib.sha256(cipher.encode()).hexdigest()


def _context(channel_id):
    contexts = metrics._contexts(auth._redis())
    matches = [row for row in contexts if row['channel_id'] == channel_id and not row['blocked']]
    if len(matches) != 1:
        raise auth.YouTubeAuthError('Analytics channel is not connected')
    return matches[0]


def binding(channel_id):
    context = _context(channel_id)
    return {'connection_id': context['connection_id'], 'credential_sha256': _digest(context['cipher'])}


def validate_state(state):
    value = state.get('analytics_binding')
    if (state.get('purpose') != 'analytics' or type(value) is not dict
            or set(value) != {'connection_id', 'credential_sha256'}
            or type(value['connection_id']) is not str or not metrics._ID.fullmatch(value['connection_id'])
            or type(value['credential_sha256']) is not str
            or not re.fullmatch('[0-9a-f]{64}', value['credential_sha256'])):
        raise auth.OAuthStateError('Analytics authorization binding is invalid')


def persist(credentials, state, epoch):
    validate_state(state)
    channel_id = state['target_channel_id']
    context = _context(channel_id)
    if state['analytics_binding'] != {'connection_id': context['connection_id'],
                                      'credential_sha256': _digest(context['cipher'])}:
        raise auth.OAuthStateError('Analytics channel changed during authorization')
    granted = getattr(credentials, 'granted_scopes', None)
    if granted is not None and not set(SCOPES).issubset(granted):
        raise auth.YouTubeAuthError('Required Analytics permissions were not granted')
    token = credentials.refresh_token
    if type(token) is not str or not 1 <= len(token) <= 4096:
        raise auth.YouTubeAuthError('Analytics offline authorization is missing')
    encrypted = auth._encrypt_json({'version': 1, 'channel_id': channel_id,
        **state['analytics_binding'], 'refresh_token': token, 'scopes': SCOPES})
    client = auth._redis()
    committed = client.eval(_COMMIT, 5, auth.AUTH_EPOCH_KEY, auth.CHANNEL_PREFIX + channel_id,
        auth.CREDENTIAL_PREFIX + channel_id, auth.CHANNEL_INDEX_KEY, PREFIX + channel_id,
        str(epoch), context['raw'], context['cipher'], channel_id, encrypted)
    if committed != 1:
        raise auth.OAuthStateError('Analytics authorization was superseded')


def read_credentials(context, encrypted):
    if type(encrypted) is not str or not 1 <= len(encrypted) <= 32768:
        raise metrics.YouTubeMetricsError('permission')
    value = auth._decrypt_json(encrypted)
    if (type(value) is not dict or type(value.get('version')) is not int or value['version'] != 1
            or value.get('channel_id') != context['channel_id']
            or value.get('connection_id') != context['connection_id']
            or value.get('credential_sha256') != _digest(context['cipher'])
            or value.get('scopes') != SCOPES or type(value.get('refresh_token')) is not str
            or not 1 <= len(value['refresh_token']) <= 4096):
        raise metrics.YouTubeMetricsError('permission')
    credentials = auth.Credentials(token=None, refresh_token=value['refresh_token'], token_uri=auth.TOKEN_URI,
        client_id=auth.settings.google_client_id, client_secret=auth.settings.google_client_secret, scopes=SCOPES)
    request = auth.GoogleRequest()
    credentials.refresh(lambda *a, **kw: request(*a, **{**kw, 'timeout': 20}))
    granted = getattr(credentials, 'granted_scopes', None)
    if not credentials.token or granted is not None and not set(SCOPES).issubset(granted):
        raise metrics.YouTubeMetricsError('permission')
    return credentials
