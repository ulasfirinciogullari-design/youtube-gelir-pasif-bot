"""Full-length voice qualification backed by independent captured responses."""
from pathlib import Path
import json
import tempfile

from app.config import settings
from app.services import fal_voice_adapter as api, fal_voice_media, fal_voice_alignment
from app.services import kie_voice_ledger as kie, audio_qc as qc, production_spend_runtime as runtime


def _asr(foundation, context, digest, provider):
    from app.services import commissioning_audio as asr, commissioning_scribe as scribe, youtube_auth
    with foundation.client.pipeline() as pipe:
        if provider == 'openai':
            _,journal = asr._read(pipe)
            candidates = [(asr.JOURNAL_KEY,identity,row) for identity,row in journal['requests'].items()
                if row['context'] == context and row['request']['audio']['sha256'] == digest]
        else:
            api.require(provider == 'elevenlabs')
            candidates = []
            for key in foundation.client.scan_iter(match=scribe.PREFIX+'*',count=64):
                pipe.watch(key); value=pipe.get(key)
                if value is None: continue
                row=json.loads(value)
                if row.get('context') == context and row['request']['audio']['sha256'] == digest:
                    api.require(pipe.pttl(key) == -1); candidates.append((key,None,row))
        api.require(len(candidates) == 1)
        key,identity,row=candidates[0]; outcome=row['outcome']; api.require(outcome is not None)
        body=youtube_auth._decrypt_json(outcome['encrypted_response'])['response']
        api.require(kie.sha(body) == outcome['response_sha256'])
        pipe.multi(); pipe.ping(); api.require(pipe.execute() == [True])
    return {'key':key,'identity':identity,'record_sha256':kie.sha(kie.raw(row))},json.loads(body)


def run(journal, spoken, *, reassess=False):
    """No TTS; inspect the original immutable audio and append the verdict."""
    from app.services import production_included_router as router
    rows=journal.read()
    field = 'reassessment' if reassess else 'qualification'
    if field in rows: return rows[field]
    if reassess:
        api.require(rows['qualification']['pass'] is False)
    result=api.result(kie.restore(rows['result']))
    audio,media=fal_voice_media.prepared(journal,result)
    text=' '.join(spoken); language=journal.body['language_code']; seconds=media['source_seconds']
    api.require(journal.body['text'] == text and kie.sha(kie.raw(journal.body)) == rows['grant']['request_sha256'])
    timing=fal_voice_alignment.edit_plan(spoken,result['timestamps'],seconds)
    api.require(126 <= seconds <= 219, 'fal_voice_long_duration_rejected')
    context=runtime.resolve_context(journal.foundation.client,journal.task)
    api.require(context == rows['grant']['context'])
    token=runtime._TASK_ID.set(journal.task)
    try:
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'full_voice.mp3';path.write_bytes(audio)
            comparison=qc.verify_audio_narration(path,text,language=language)
            reference,payload=_asr(journal.foundation,context,media['sha256'],comparison['provider'])
            prosody=None; observed=None
            if comparison.get('pass') is True:
                router._LAST_OBSERVED.set(None)
                prosody=qc.verify_audio_prosody(path,text,audio_duration_seconds=seconds,
                    transcript_evidence=comparison,language=language)
                observed=router._LAST_OBSERVED.get()
    finally:
        runtime._TASK_ID.reset(token)
    qualification={'version':1,'body':journal.body,'spoken_scenes':spoken,
        'grant_sha256':kie.sha(kie.raw(rows['grant'])),'media_sha256':kie.sha(kie.raw(media)),
        'audio_sha256':media['sha256'],'timing':timing,'audio_qc':comparison,'asr':reference,
        'prosody':prosody,'prosody_provider_evidence':observed,
        'pass':comparison.get('pass') is True and prosody is not None and prosody.get('pass') is True}
    if reassess:
        qualification['supersedes_qualification_sha256'] = kie.sha(kie.raw(rows['qualification']))
    journal.append(field,qualification)
    return qualification


def verified(journal):
    """Recompute both judgments and bind the whole prosody request to audio."""
    from app.services import abacus_router_audio_adapter as adapter, commissioning_reasoning as reasoning
    from app.services.fal_voice_trial import qualifying_record
    rows=journal.read();q=qualifying_record(rows);g=rows['grant'];m=rows['prepared_media']
    api.require(q['version'] == 1 and q['pass'] is True
        and q['grant_sha256'] == kie.sha(kie.raw(g)) and q['media_sha256'] == kie.sha(kie.raw(m))
        and q['audio_sha256'] == m['sha256'] and q['body'] == journal.body
        and kie.sha(kie.raw(q['body'])) == g['request_sha256']
        and q['body']['text'] == ' '.join(q['spoken_scenes']))
    result=api.result(kie.restore(rows['result']));audio,media=fal_voice_media.prepared(journal,result)
    api.require(media == m and fal_voice_alignment.edit_plan(q['spoken_scenes'],result['timestamps'],m['source_seconds']) == q['timing'])
    language=q['body']['language_code'];text=q['body']['text'];context=g['context'];provider=q['audio_qc']['provider']
    ref,payload=_asr(journal.foundation,context,m['sha256'],provider);api.require(ref == q['asr'])
    comparison=qc._require_word_timing_evidence(qc.compare_transcript(text,payload['text'],words=payload['words'],
        provider=provider,comparison_language=language,language_code=payload.get('language_code',payload.get('language')),
        language_probability=payload.get('language_probability')),'Independent recognizer')
    token=runtime._TASK_ID.set(journal.task)
    try:
        if comparison['pass'] is not True and q['audio_qc'].get('name_pronunciation_review'):
            from app.services import audio_proper_name_review as names
            comparison=names.reassess(audio,text,comparison,evidence=q['audio_qc']['name_pronunciation_review'])
        api.require(comparison['pass'] is True,'fal_voice_blind_asr_rejected')
        prepared=adapter.prepare_longform_prosody_request(audio,api_key=settings.abacus_api_key,
            language=language,expected_narration=text,json_schema=qc._PROSODY_REVIEW_SCHEMA,
            system_instruction=qc._PROSODY_SYSTEM_INSTRUCTION.replace('Turkish','English') if language=='en' else qc._PROSODY_SYSTEM_INSTRUCTION)
    finally:
        runtime._TASK_ID.reset(token)
    native,schema,_=reasoning._request(prepared,'prosody',long_form=True)
    identity=reasoning._sha(reasoning._raw({'endpoint':reasoning.ENDPOINT,'body':native,'purpose':'prosody',
        'credential_sha256':reasoning._sha('gemini\0'+settings.gemini_api_key),'context':context}))
    keys=[reasoning.PREFIX+k+':'+identity for k in ('request','response')]
    with journal.foundation.client.pipeline() as pipe:
        pipe.watch(*keys);request,response=[json.loads(pipe.get(key))for key in keys]
        api.require(all(pipe.pttl(key)==-1 for key in keys) and request['context']==context
            and request['purpose']=='prosody' and request['request_sha256']==identity
            and request['legacy_request_sha256']==prepared.request_sha256)
        pipe.multi();pipe.ping();api.require(pipe.execute()==[True])
    output,evidence=reasoning._observe({'record':request},response,prepared,schema)
    api.require(q['prosody_provider_evidence']=={'purpose':'prosody','context':context,'evidence':evidence})
    review=qc._validate_prosody_review(output,text,audio_duration_seconds=m['source_seconds'],
        transcript_evidence=comparison,language=language,provider='gemini')
    api.require(review is not None and review['pass'] is True,'fal_voice_prosody_rejected')
    return {'qualification_sha256':kie.sha(kie.raw(q)),'grant_sha256':q['grant_sha256'],
        'audio_sha256':m['sha256'],'language':language,'voice':q['body']['voice'],
        'source_seconds':m['source_seconds'],'asr_score':comparison['score'],'prosody':review['scores']}
