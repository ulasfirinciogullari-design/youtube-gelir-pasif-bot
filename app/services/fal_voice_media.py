"""Keep the accepted complete MP3, with provider and storage hash bindings."""
import httpx
from pathlib import Path
import tempfile
import subprocess

from app.config import settings
from app.services import fal_voice_adapter as api, kie_voice_ledger as ledger
from app.services import storage, whisper_transcription as whisper
from app.services.kie_voice_media import _get
from app.services.fal_video import validate_fal_media_url


def mp3(journal, result):
    rows = journal.read(); receipt = rows['result']
    api.require(api.result(ledger.restore(receipt)) == result)
    s3 = storage._client(single_attempt=True)
    if 'media' in rows:
        record = rows['media']
        api.require(record['result_receipt_sha256'] == receipt['response_sha256'])
        data = _get(s3,record['object_key'],whisper.WHISPER_MAX_AUDIO_BYTES)
        api.require(ledger.sha(data) == record['sha256'])
        return data,record
    audio = result['audio']; url = validate_fal_media_url(audio['url'])
    api.require(audio.get('content_type') == 'audio/mpeg'
        and type(audio.get('file_size')) is int and 0 < audio['file_size'] <= whisper.WHISPER_MAX_AUDIO_BYTES)
    with httpx.Client(timeout=45,trust_env=False,follow_redirects=False) as client:
        with client.stream('GET',audio['url']) as response:
            api.require(response.status_code == 200)
            chunks,size = [],0
            for chunk in response.iter_bytes():
                size += len(chunk); api.require(size <= audio['file_size'])
                chunks.append(chunk)
    data = b''.join(chunks); api.require(len(data) == audio['file_size'])
    snapshot = whisper._snapshot_audio(data,'.mp3',allow_natural_short=True,allow_commissioned_long=True)
    descriptor = snapshot.descriptor({})['audio']; digest = ledger.sha(data)
    key = 'provider_audio/fal_voice/' + receipt['response_sha256'] + '/' + digest + '.mp3'
    s3.put_object(Bucket=settings.bucket,Key=key,Body=data,ContentType='audio/mpeg')
    api.require(ledger.sha(_get(s3,key,whisper.WHISPER_MAX_AUDIO_BYTES)) == digest)
    record = {'version':1,'result_receipt_sha256':receipt['response_sha256'],
        'object_key':key,'sha256':digest,'audio':descriptor,'source_seconds':snapshot.samples/48000,
        'complete_original':True,'conversion':None}
    journal.append('media',record)
    return data,record


def prepared(journal,result):
    """Normalize once to the exact production loudness; keep both originals."""
    original,source=mp3(journal,result);rows=journal.read();s3=storage._client(single_attempt=True)
    if 'prepared_media' in rows:
        record=rows['prepared_media']
        api.require(record['original_sha256']==source['sha256']
            and record['source_media_sha256']==ledger.sha(ledger.raw(source))
            and record['normalization']=='complete_loudnorm_-15_-1_7_mp3_192k_v1')
        data=_get(s3,record['object_key'],whisper.WHISPER_MAX_AUDIO_BYTES)
        api.require(ledger.sha(data)==record['sha256'])
        return data,record
    with tempfile.TemporaryDirectory() as directory:
        before,after=Path(directory)/'original.mp3',Path(directory)/'prepared.mp3'
        before.write_bytes(original)
        subprocess.run(['ffmpeg','-y','-nostdin','-i',str(before),'-af','loudnorm=I=-15:TP=-1.0:LRA=7',
            '-c:a','libmp3lame','-b:a','192k',str(after)],check=True,timeout=45,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        data=after.read_bytes()
    snapshot=whisper._snapshot_audio(data,'.mp3',allow_natural_short=True,allow_commissioned_long=True)
    api.require(abs(snapshot.samples/48000-source['source_seconds'])<.1,'fal_voice_normalization_duration_changed')
    digest=ledger.sha(data);key='provider_audio/fal_voice/'+source['result_receipt_sha256']+'/'+digest+'.mp3'
    s3.put_object(Bucket=settings.bucket,Key=key,Body=data,ContentType='audio/mpeg')
    api.require(ledger.sha(_get(s3,key,whisper.WHISPER_MAX_AUDIO_BYTES))==digest)
    record={'version':1,'object_key':key,'sha256':digest,'audio':snapshot.descriptor({})['audio'],
        'source_seconds':snapshot.samples/48000,'original_sha256':source['sha256'],
        'source_media_sha256':ledger.sha(ledger.raw(source)),
        'normalization':'complete_loudnorm_-15_-1_7_mp3_192k_v1'}
    journal.append('prepared_media',record)
    return data,record
