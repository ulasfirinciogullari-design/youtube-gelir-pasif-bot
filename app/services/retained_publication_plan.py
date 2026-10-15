"""Bind an admitted retained final and episode number to one private upload plan.

Only the dedicated execution capability may prepare this plan. Source readers
have already passed before claim; their private immutable artifacts, final file
and all watched source records are revalidated here. No model or YouTube call
runs in this module, and a plan alone cannot insert or release a video.
"""
from copy import deepcopy
from pathlib import Path
import re
import threading
import weakref

from app.services import retained_delivery_dispatch as delivery
from app.services import retained_production_admission as admission
from app.services import retained_final_artifacts as staging
from app.services import retained_render_consumer as render
from app.services import retained_cut_evidence as cuts
from app.services import youtube_automation as automation
from app.services.youtube_discovery_metadata import topic_metadata_fallback

PLAN_KEY = admission.PREFIX + ':publication_plan'
_ISSUED = weakref.WeakKeyDictionary()
_ATTEMPTED = weakref.WeakKeyDictionary()
_LOCK = threading.RLock()


class RetainedPublicationPlanError(RuntimeError):
    """Fixed error text; no private content or storage/backend response."""


def _require(value):
    if not value:
        raise RetainedPublicationPlanError('retained_publication_plan_unverified')


class PreparedRetainedPublication:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('retained_publication_plan_private')

    @property
    def record(self):
        return deepcopy(_checked(self)['record'])

    def __repr__(self):
        return '<PreparedRetainedPublication bound-files-only>'


def _checked(value):
    _require(type(value) is PreparedRetainedPublication)
    with _LOCK:
        state = _ISSUED.get(value)
    _require(state is not None and state['owner'] == threading.get_ident())
    return state


def _metadata(raw, manifest):
    metadata = staging.artifacts._object(raw)
    _require(cuts._raw(metadata) == raw)
    final = manifest['prepared_final']
    _require(metadata['version'] == 1 and metadata['kind'] == 'retained_final_metadata'
        and metadata['source_task_id'] == admission.continuity.LEAF_ID
        and metadata['original_task_id'] == admission.continuity.ROOT_ID
        and metadata['source_snapshot'] == final['source_snapshot']
        and metadata['artifacts'] == {k: v for k, v in final['artifacts'].items() if k != 'metadata'}
        and all(type(metadata[k]) is type(v) and metadata[k] == v for k, v in staging._FLAGS.items()))
    local, story, visual, audio = (metadata[k] for k in
        ('local_render', 'story_component', 'visual_component', 'audio_component'))
    _require(admission._hash(local) == final['local_render_sha256']
        and local['local_render_verified'] is True and local['local_final_gates']['pass'] is True
        and local['final_aac_independently_listened'] is False
        and story['component_pass'] is visual['component_pass'] is audio['component_pass'] is True
        and story['read_acknowledged'] is audio['journal_read_acknowledged'] is True
        and local['source_commitments'] == visual['commitments']
        and local['audio_evidence_sha256'] == admission._hash(audio)
        and admission._hash(metadata['package']) == visual['commitments']['immutable_core_sha256'])
    for name, identity in (('video', local['final']), ('captions', local['captions'])):
        _require(identity == {k: final['artifacts'][name][k] for k in ('sha256', 'size')})
    return metadata


def _series_keys(profile, child):
    c = admission.continuity
    _require(profile.get('series_id') == 'paranin-arka-yuzu-s1'
             and type(profile.get('series_total')) is int and profile['series_total'] == 8)
    scope = c.CHANNEL_ID + ':' + profile['series_id']
    return (automation.SERIES_COUNTER_PREFIX + scope,
        automation.SERIES_ASSIGNMENT_PREFIX + scope + ':' + child,
        *(automation.SERIES_ASSIGNMENT_PREFIX + scope + ':' + task for task in c.LINEAGE))


def _compose(metadata, manifest, profile):
    package = metadata['package']
    source = manifest['prepared_final']['source_snapshot']['source']
    _require(profile.get('auto_publish') is True and profile.get('production_enabled') is True
             and profile.get('release_mode') == 'public'
             and profile['profile_revision'] == source['profile_revision']
             and profile['channel_id'] == source['channel_id'] == admission.continuity.CHANNEL_ID
             and metadata['language'] == profile['default_language']
             and source['topic_index'] == 4 and source['cursor'] == 5 and source['topic_count'] == 8)
    title = automation._one_line(package['title'], 100)
    description = str(package['narration']).strip()
    _require(title and description and len(description) <= 4500)
    fallback = topic_metadata_fallback(title, description)
    hashtags = automation._hashtags(['Shorts', *fallback['hashtags'], *automation._items(profile.get('hashtags'))])
    tags = automation._string_list([*fallback['tags'], *automation._items(profile.get('default_tags'))],
                                   maximum_items=30, maximum_length=100)
    series_name = automation._one_line(profile.get('series_name') or profile['series_id'], 100)
    title = automation._truncate_title(title, '(5/8)')
    description = series_name + ' · 5/8\n\n' + description
    sources = [v.get('url') if type(v) is dict else v for v in package.get('sources', [])]
    sources = [s for s in sources if type(s) is str and re.match(r'^https?://', s)]
    if sources:
        description += '\n\nKaynaklar:\n' + '\n'.join(sources[:20])
    footer = str(profile.get('description_footer') or '').strip()
    if footer:
        description += '\n\n' + footer
    if hashtags:
        description += '\n\n' + ' '.join('#' + h for h in hashtags)
    local = metadata['local_render']
    quality = {'quality_disposition': 'retained_component_review_pass', 'manual_qa_required': False,
        'admission_manifest_sha256': admission._hash(manifest),
        'local_render_sha256': admission._hash(local), 'final_aac_independently_listened': False,
        'components': {name: admission._hash(metadata[name + '_component']) for name in ('story', 'visual', 'audio')}}
    return automation.validate_publish_plan({'schema_version': 1, 'source_task_id': manifest['child_id'],
        'target_channel_id': source['channel_id'], 'profile_revision': source['profile_revision'],
        'title': title, 'description': description, 'tags': tags, 'hashtags': hashtags,
        'category_id': str(profile.get('category_id') or '28'), 'default_language': metadata['language'],
        'contains_synthetic_media': True, 'thumbnail_key': manifest['prepared_final']['artifacts']['thumbnail']['key'],
        'require_thumbnail': bool(profile.get('require_thumbnail')), 'release_mode': 'public', 'publish_at': None,
        'series': {'id': profile['series_id'], 'name': series_name, 'number': 5, 'total': 8},
        'quality_snapshot': quality, 'created_at': delivery._now()})


def prepare_retained_publication(execution, s3, *, bucket, workdir):
    """Privately materialize exact bytes; reserve episode five and one plan."""
    try:
        running = delivery._execution(execution)
        with _LOCK:
            _require(execution not in _ATTEMPTED)
            _ATTEMPTED[execution] = True
        manifest = delivery.verify_execution(execution)
        cuts._storage_guard(s3, bucket)
        base = render._directory(workdir)
        directory = base / 'retained-publication-inputs'
        directory.mkdir(mode=0o700, exist_ok=False)
        artifacts = manifest['prepared_final']['artifacts']
        metadata = _metadata(cuts._read_private(s3, bucket, artifacts['metadata']), manifest)
        files = {}
        for name, filename in (('video', 'final.mp4'), ('captions', 'captions.srt'), ('thumbnail', 'thumbnail.jpg')):
            pointer = artifacts[name]
            files[name] = render._write(directory, filename, cuts._read_private(s3, bucket, pointer),
                                       {k: pointer[k] for k in ('sha256', 'size')})
        render._sync_directory(directory)
        client = running['client']
        with client.pipeline() as pipe:
            current, _ = admission._read_claim(pipe)
            _require(current == manifest and delivery._stored(pipe, delivery.EXECUTION_KEY) == running['execution'])
            profile = staging.artifacts._object(pipe.get(admission.continuity._PROFILE + admission.continuity.CHANNEL_ID))
            keys = _series_keys(profile, manifest['child_id'])
            pipe.watch(PLAN_KEY, *keys)
            _require(pipe.exists(PLAN_KEY, *keys[1:]) == 0 and pipe.get(keys[0]) == '4'
                     and type(pipe.pttl(keys[0])) is int and pipe.pttl(keys[0]) == -1)
            plan = _compose(metadata, manifest, profile)
            record = {'version': 1, 'kind': 'retained_publication_plan',
                'manifest_sha256': admission._hash(manifest), 'execution_sha256': admission._hash(running['execution']),
                'plan': plan, 'artifacts': artifacts, 'series_keys': list(keys),
                'connection_id': manifest['prepared_final']['source_snapshot']['source']['current_connection_id'],
                'authorization_epoch': manifest['prepared_final']['source_snapshot']['source']['authorization_epoch'],
                'upload_started': False, 'publication_complete': False}
            for row in files.values():
                _require(render._file(row['path'], row['identity']) == row)
            pipe.multi()
            pipe.set(PLAN_KEY, admission._raw(record).decode(), nx=True)
            pipe.set(keys[0], '5')
            pipe.set(keys[1], '5', nx=True)
            admission._ack(pipe.execute(), [True, True, True])
        value = object.__new__(PreparedRetainedPublication)
        state = {'execution': execution, 'record': record, 'files': files,
                 'owner': threading.get_ident(), 'directory': str(directory)}
        with _LOCK:
            _ISSUED[value] = state
        verify_retained_publication(value)
        return value
    except RetainedPublicationPlanError:
        raise
    except Exception:
        raise RetainedPublicationPlanError('retained_publication_plan_unverified') from None


def verify_retained_publication(value):
    """Recheck live source, exact files, plan and durable episode allocation."""
    try:
        state = _checked(value)
        running = delivery._execution(state['execution'])
        manifest = delivery.verify_execution(state['execution'])
        with running['client'].pipeline() as pipe:
            current, _ = admission._read_claim(pipe)
            _require(current == manifest and delivery._stored(pipe, PLAN_KEY) == state['record']
                     and delivery._stored(pipe, delivery.EXECUTION_KEY) == running['execution'])
            keys = state['record']['series_keys']
            pipe.watch(*keys)
            _require(pipe.mget(keys[:2]) == ['5', '5'] and pipe.exists(*keys[2:]) == 0
                     and all(type(t) is int and t == -1 for t in (pipe.pttl(k) for k in keys[:2])))
            render._directory(Path(state['directory']))
            for row in state['files'].values():
                _require(render._file(row['path'], row['identity']) == row)
            admission._read_ack(pipe)
        return deepcopy(state['record'])
    except RetainedPublicationPlanError:
        raise
    except Exception:
        raise RetainedPublicationPlanError('retained_publication_plan_unverified') from None
