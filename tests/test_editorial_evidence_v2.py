"""V2 uses actual duration/language and normalized raw ASR, never old QA flags."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services import external_editorial_review as review
from test_external_editorial_review import case, raw, saved, REAL_STORAGE_PROOF
from test_external_artifact_import import _manifest, _captions


NARRATION = [
    'Kasada barkodla okutulan ilk ürün neydi? Bir sakız paketi.',
    'Tarih, yirmi altı Haziran bin dokuz yüz yetmiş dört.',
    "Amerika'da bir kasiyer, sakız paketini tarayıcının üzerinden geçirdi.",
    'Çizgiler fiyatı değil, ürünün kimliğini taşıyordu.',
    'Bilgisayar kayıtlı fiyatı buldu; kasiyer rakamları tek tek yazmadı.',
    'Bugün o bip sıradan. Peki, sen kasiyeri mi, otomatik kasayı mı seçersin?',
]
EDGES = [0, 6433, 12767, 18267, 22133, 27733, 35833]


def manifest_v2(value):
    value.update(version=2, duration_ms=35833, language='tr')
    for i, (scene, text) in enumerate(zip(value['scenes'], NARRATION)):
        scene.update(narration=text, start_ms=EDGES[i], end_ms=EDGES[i + 1])
    return value


def sync_asr(c, update=None):
    asr = json.loads(c.pack['asr_provider_evidence_json'])
    if update:
        update(asr['unapproved_provider_evidence']['payload'])
    c.pack['asr_provider_evidence_json'] = raw(asr)
    prosody = json.loads(c.pack['prosody_result_json'])
    prosody['result']['preflight']['provider_evidence_sha256'] = review._sha(c.pack['asr_provider_evidence_json'].encode())
    c.pack['prosody_result_json'] = raw(prosody)


@pytest.fixture
def v2(case):
    c = case
    c.pack['version'] = 2
    manifest = manifest_v2(c.pack['manifest'])
    asr = json.loads(c.pack['asr_provider_evidence_json'])
    binding = asr['binding']
    binding.update(manifest_sha256=review._digest(manifest), sample_frames=1720000,
                   duration_seconds=1720000 / 48000)
    expected = ' '.join(NARRATION)
    heard = expected.replace('yirmi altı', '26').replace('bin dokuz yüz yetmiş dört', '1974')
    asr['unapproved_provider_evidence'].update(language='tr', payload={
        'language': 'turkish', 'text': heard,
        'words': [{'word': word, 'start': i * .6, 'end': i * .6 + .5}
                  for i, word in enumerate(review._words(heard))],
    })
    c.pack['asr_provider_evidence_json'] = raw(asr)
    for key in ('asr_attempt_json', 'prosody_attempt_json'):
        value = json.loads(c.pack[key]); value.update(binding); c.pack[key] = raw(value)
    prosody = json.loads(c.pack['prosody_result_json'])
    prosody['binding'] = deepcopy(binding)
    prosody['result']['preflight'].update(text_exact=False, text_normalized_exact=True)
    prosody['result']['scores'].update(pronunciation=75, naturalness=75, pacing=75,
                                     sentence_flow=75, emphasis=75, roboticness=20)
    c.pack['prosody_result_json'] = raw(prosody)
    sync_asr(c)
    for i, frame in enumerate(c.pack['frames']):
        frame['frame_index'] = [60, 240, 383, 580, 750, 950][i]
        c.pack['scenes'][i]['frame_indices'] = [frame['frame_index']]
    c.source['spec'].update(language='tr', duration_minutes=35833 / 60000)
    c.source['result']['language'] = 'tr'
    c.source['result']['external_provenance'].update(
        manifest_version=2, duration_ms=35833, manifest_sha256=review._digest(manifest))
    c.proof.update(manifest_sha256=review._digest(manifest))
    c.proof['media_structure'].update(frame_count=1075)
    c.client.set(review.JOB_PREFIX + c.task, raw(c.source))
    reservation = json.loads(c.client.get(review.ingest.RESERVATION_PREFIX + c.task))
    reservation['job_sha256'] = review._digest(c.source)
    c.client.set(review.ingest.RESERVATION_PREFIX + c.task, raw(reservation))
    profile_key = review.ingest.PROFILE_PREFIX + c.source['spec']['production_channel_id']
    profile = json.loads(c.client.get(profile_key)); profile['languages'] = ['tr']
    c.client.set(profile_key, raw(profile))
    return c


def test_v2_real_shape_accepts_53_spoken_words_and_numeric_date_without_rewriting_raw(v2):
    c = v2
    assert len(' '.join(NARRATION).split()) == 53
    original = deepcopy(c.pack)
    receipt = review.create_editorial_review(c.task, c.pack)
    assert receipt['evidence']['version'] == 2 and c.pack == original
    assert receipt['server_proof']['media_structure']['frame_count'] == 1075
    assert receipt['word_timing_verified'] is receipt['automated_qa_approved'] is False
    result = saved(c)['result']
    assert result['word_timing_verified'] is result['audio_transcription_verified'] is result['qa_approved'] is False
    assert result['quality_disposition'] == 'editorial_review_pass'
    assert review.validate_editorial_publication(saved(c)) == receipt
    raw_asr = json.loads(receipt['evidence']['asr_provider_evidence_json'])
    assert '1974' in raw_asr['unapproved_provider_evidence']['payload']['text']
    assert 'bin dokuz yüz yetmiş dört' in receipt['evidence']['manifest']['scenes'][1]['narration']


@pytest.mark.parametrize('old,new', [('1974', '1975'), ('Haziran', 'Temmuz'), ("Amerika'da", "Almanya'da"), ('neydi?', '')])
def test_v2_normalization_does_not_accept_wrong_year_month_place_or_missing_word(v2, old, new):
    sync_asr(v2, lambda p: p.update(text=p['text'].replace(old, new)))
    with pytest.raises(review.EditorialReviewError): review._evidence(v2.pack)


def test_v2_requires_raw_word_sequence_and_preserves_unknown_zero_duration_timing(v2):
    c = v2
    sync_asr(c, lambda p: p['words'][0].update(end=p['words'][0]['start']))
    assert review._evidence(c.pack)[3] == [0]
    sync_asr(c, lambda p: p['words'][0].update(word='Yanlış'))
    with pytest.raises(review.EditorialReviewError): review._evidence(c.pack)


@pytest.mark.parametrize('damage', ['literal_lie', 'normalized_missing', 'old_asr_hash', 'low_score', 'robotic',
                                   'fake_global_qa', 'bad_frame', 'old_duration', 'wrong_language'])
def test_v2_evidence_failures_remain_failures(v2, damage):
    c = v2
    prosody = json.loads(c.pack['prosody_result_json'])
    if damage == 'literal_lie': prosody['result']['preflight']['text_exact'] = True
    elif damage == 'normalized_missing': prosody['result']['preflight'].pop('text_normalized_exact')
    elif damage == 'old_asr_hash': prosody['result']['preflight']['provider_evidence_sha256'] = '0' * 64
    elif damage == 'low_score': prosody['result']['scores']['naturalness'] = 69
    elif damage == 'robotic': prosody['result']['scores']['roboticness'] = 31
    elif damage == 'fake_global_qa': prosody['result']['combined_audio_approved'] = True
    elif damage == 'bad_frame': c.pack['frames'][-1]['frame_index'] = 1075
    elif damage == 'old_duration':
        value = json.loads(c.pack['asr_provider_evidence_json']); value['binding']['duration_seconds'] = 30
        c.pack['asr_provider_evidence_json'] = raw(value)
    elif damage == 'wrong_language':
        value = json.loads(c.pack['asr_provider_evidence_json']); value['unapproved_provider_evidence']['language'] = 'en'
        c.pack['asr_provider_evidence_json'] = raw(value)
    c.pack['prosody_result_json'] = raw(prosody)
    with pytest.raises(review.EditorialReviewError): review._evidence(c.pack)


@pytest.mark.parametrize('field,value', [('manifest_version', 1), ('manifest_version', True), ('duration_ms', 30000)])
def test_v2_cannot_rebind_a_legacy_or_changed_server_import(v2, field, value):
    v2.source['result']['external_provenance'][field] = value
    v2.client.set(review.JOB_PREFIX + v2.task, raw(v2.source))
    with pytest.raises(review.EditorialReviewError): review.create_editorial_review(v2.task, v2.pack)
    assert v2.calls == []


def test_v1_contract_remains_english_65_words_and_86_quality(case):
    value = json.loads(case.pack['prosody_result_json'])
    value['result']['scores']['naturalness'] = 75
    case.pack['prosody_result_json'] = raw(value)
    with pytest.raises(review.EditorialReviewError): review._evidence(case.pack)
    case.pack['version'] = 2
    with pytest.raises(review.EditorialReviewError): review._evidence(case.pack)


@pytest.fixture
def blind_crosscheck(v2):
    from app.services import audio_qc
    c = v2
    expected = ' '.join(NARRATION)
    def omit_day(payload):
        payload['text'] = payload['text'].replace('26 ', '')
        payload['words'] = [w for w in payload['words'] if w['word'] != '26']
    sync_asr(c, omit_day)
    primary = json.loads(c.pack['asr_provider_evidence_json'])
    payload = primary['unapproved_provider_evidence']['payload']
    comparison = audio_qc.compare_transcript(expected, payload['text'], words=payload['words'],
                                             provider='openai', comparison_language='tr')
    assert comparison['score'] < 100 and comparison['pass'] is False
    prosody = json.loads(c.pack['prosody_result_json'])
    result = prosody['result']
    result['preflight'].update(text_normalized_exact=False, text_comparison_score=comparison['score'])
    response = {'transcript': expected, 'prosody': {
        'pass': True, 'scores': deepcopy(result['scores']), 'issues': [], 'summary': 'Clear intelligible Turkish narration.'}}
    validated = audio_qc._validate_prosody_review(response['prosody'], expected,
        audio_duration_seconds=primary['binding']['duration_seconds'], transcript_evidence=None, language='tr')
    result.update(validated)
    secondary = audio_qc.compare_transcript(expected, response['transcript'], provider='gemini', comparison_language='tr')
    digest = review._digest(response)
    result.update(raw_response_sha256=digest, independent_transcript_crosscheck={
        'provider': 'gemini', 'transcript': response['transcript'], 'normalized_exact': True,
        'comparison_score': secondary['score'], 'mismatch_details': secondary['mismatch_details'],
        'raw_response_sha256': digest})
    c.pack['prosody_result_json'] = raw(prosody)
    c.pack['prosody_raw_response_json'] = raw({
        'format': 'parsed_model_json_unapproved', 'provider': 'gemini', 'model': 'gemini-test',
        'binding': deepcopy(primary['binding']), 'response': response, 'response_sha256': digest,
        'diagnostic_only': True, 'asr_approved': False, 'combined_audio_approved': False,
        'word_timing_verified': False, 'qa_approved': False, 'publish_eligible': False})
    return c


def test_blind_secondary_exact_transcript_preserves_failed_whisper_and_unverified_timings(blind_crosscheck):
    c = blind_crosscheck
    original = deepcopy(c.pack)
    receipt = review.create_editorial_review(c.task, c.pack)
    assert c.pack == original and receipt['evidence'] == original
    preflight = json.loads(receipt['evidence']['prosody_result_json'])['result']['preflight']
    assert preflight['text_normalized_exact'] is False and preflight['text_comparison_score'] < 100
    assert receipt['word_timing_verified'] is receipt['automated_qa_approved'] is False
    assert saved(c)['result']['audio_transcription_verified'] is False
    assert review.validate_editorial_publication(saved(c)) == receipt


@pytest.mark.parametrize('damage', ['wrong_date', 'missing_day', 'binding', 'model', 'raw_hash', 'derived_hash',
                                   'false_primary_pass', 'raw_negative', 'changed_prosody', 'missing_raw',
                                   'fake_timing', 'fake_crosscheck_pass'])
def test_secondary_requires_actual_consistent_bound_raw_and_complete_words(blind_crosscheck, damage):
    c = blind_crosscheck
    retained = json.loads(c.pack['prosody_raw_response_json'])
    prosody = json.loads(c.pack['prosody_result_json'])
    result = prosody['result']
    if damage in ('wrong_date', 'missing_day'):
        retained['response']['transcript'] = retained['response']['transcript'].replace(
            'yetmiş dört' if damage == 'wrong_date' else 'yirmi altı ',
            'yetmiş beş' if damage == 'wrong_date' else '')
        digest = review._digest(retained['response'])
        retained['response_sha256'] = result['raw_response_sha256'] = digest
        result['independent_transcript_crosscheck'].update(
            transcript=retained['response']['transcript'], raw_response_sha256=digest)
    elif damage == 'binding': retained['binding']['pcm_sha256'] = '0' * 64
    elif damage == 'model': retained['model'] = 'old-unrelated-model'
    elif damage == 'raw_hash': retained['response_sha256'] = '0' * 64
    elif damage == 'derived_hash': result['independent_transcript_crosscheck']['raw_response_sha256'] = '0' * 64
    elif damage == 'false_primary_pass': result['preflight'].update(text_normalized_exact=True, text_comparison_score=100)
    elif damage == 'raw_negative':
        retained['response']['prosody']['pass'] = False
        digest = review._digest(retained['response'])
        retained['response_sha256'] = result['raw_response_sha256'] = digest
        result['independent_transcript_crosscheck']['raw_response_sha256'] = digest
    elif damage == 'changed_prosody': result['scores']['naturalness'] = 99
    elif damage == 'fake_timing': retained['word_timing_verified'] = True
    elif damage == 'fake_crosscheck_pass': result['independent_transcript_crosscheck']['normalized_exact'] = 1
    c.pack['prosody_raw_response_json'] = raw(retained)
    c.pack['prosody_result_json'] = raw(prosody)
    if damage == 'missing_raw': c.pack.pop('prosody_raw_response_json')
    with pytest.raises(review.EditorialReviewError): review._evidence(c.pack)


def test_real_v2_complete_pcm_and_1075_frames_are_server_verified(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Local FFmpeg unavailable')
    video, captions = tmp_path / 'master.mp4', tmp_path / 'captions.srt'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'color=c=navy:s=720x1280:r=30:d=35.833333333', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=48000:duration=35.833333333', '-ac', '2', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-n', str(video)],
        check=True, capture_output=True, timeout=90)
    captions.write_bytes(b'placeholder')
    manifest = manifest_v2(_manifest(video, captions))
    captions.write_bytes(_captions(manifest['scenes']))
    manifest['files']['captions'].update(size=captions.stat().st_size, sha256=review._sha(captions.read_bytes()))
    descriptor = review.artifact.validate_staged_external_artifact(tmp_path, video, captions, manifest)
    pcm = tmp_path / 'observed.pcm'
    assert review._decode_complete_pcm(video, pcm).returncode == 0
    decoded = pcm.read_bytes()
    binding = {'sample_rate': 48000, 'channels': 2, 'sample_frames': len(decoded) // 4,
               'pcm_sha256': review._sha(decoded)}
    metadata = raw({key: value for key, value in descriptor.items() if key != 'files'}).encode()
    result = {'external_descriptor_id': descriptor['descriptor_id']}
    objects = {}
    for key, suffix, data in [('video', 'master.mp4', video.read_bytes()), ('caption', 'captions.srt', captions.read_bytes()),
                              ('metadata', 'metadata.json', metadata)]:
        result[key + '_key'] = 'external-masters/v1/' + review._sha(data) + '/' + suffix
        objects[result[key + '_key']] = data
    monkeypatch.setattr(review.ingest.storage, 'download_file', lambda key, path: Path(path).write_bytes(objects[key]))
    proof = REAL_STORAGE_PROOF({'result': result}, manifest, binding)
    assert proof['media_structure']['frame_count'] == 1075
    assert proof['pcm_sha256'] == binding['pcm_sha256']
    assert proof['server_verified_stored_bytes_and_pcm'] is True
    with pytest.raises(review.EditorialReviewError):
        REAL_STORAGE_PROOF({'result': result}, manifest, {**binding, 'pcm_sha256': '0' * 64})
    reference_binding = {**binding, 'video_sha256': review._sha(video.read_bytes()),
                         'video_size': video.stat().st_size,
                         'duration_seconds': binding['sample_frames'] / binding['sample_rate']}
    proof = REAL_STORAGE_PROOF({'result': result}, manifest, reference_binding, reference_video=video.read_bytes())
    assert proof['pcm_verification_mode'] == 'same_runtime_reference_equivalence'
    assert proof['reference_and_current_server_pcm_exact_match'] is True
    assert proof['retained_sample_frames'] == binding['sample_frames']
    with pytest.raises(review.EditorialReviewError) as caught:
        REAL_STORAGE_PROOF({'result': result}, manifest,
                           {**reference_binding, 'duration_seconds': reference_binding['duration_seconds'] + .11},
                           reference_video=video.read_bytes())
    assert caught.value.editorial_phase == 'editorial_review_reference_structure'
