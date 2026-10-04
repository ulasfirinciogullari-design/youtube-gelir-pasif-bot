"""New system: scheduled Shorts carry short on-screen captions; nothing else changes."""
from pathlib import Path
import shutil
import subprocess

import pytest

from app.services import render

SCENES = [{'narration': 'Çığ düştüğünde ağaçlar böyle eğilir, İstanbul şaşırır.'},
          {'narration': 'Gerçek {cevap} burada\\ saklı.'}]


def test_ass_captions_are_short_escaped_and_keep_turkish_letters(tmp_path):
    path = Path(render.make_shorts_caption_ass(SCENES, [2.0, 1.5], 3.5, tmp_path / 'captions.ass'))
    text = path.read_text(encoding='utf-8')
    assert text.startswith('[Script Info]') and 'PlayResX: 1080' in text and 'PlayResY: 1920' in text
    events = [line for line in text.splitlines() if line.startswith('Dialogue:')]
    captions = [line.split(',,', 2)[-1].split(',,', 1)[-1] for line in events]
    assert 'Çığ düştüğünde ağaçlar' in captions[0] and any('İstanbul' in caption for caption in captions)
    assert all(len(caption.split()) <= 4 for caption in captions)
    assert not any(char in ''.join(captions) for char in '{}\\')
    assert events[0].startswith('Dialogue: 0,0:00:00.00,') and events[-1].split(',')[2] == '0:00:03.50'


def test_sidecar_srt_keeps_its_longer_chunks():
    cues = render._scene_caption_cues(SCENES, [2.0, 1.5], 3.5)
    assert [part for _start, _end, part in cues] == [
        *render._caption_chunks(SCENES[0]['narration']), *render._caption_chunks(SCENES[1]['narration'])]
    assert cues[0][0] == 0 and cues[-1][1] == 3.5


@pytest.mark.parametrize('text,expected', [
    ('Bir iki üç dört beş altı yedi', ['Bir iki üç', 'dört beş altı yedi']),
    ('Neden? Çünkü su, buzdan hafiftir.', ['Neden?', 'Çünkü su,', 'buzdan hafiftir.']),
    ('Tek', ['Tek']),
])
def test_burned_caption_chunks_are_two_or_three_words(text, expected):
    assert render._short_caption_chunks(text) == expected


@pytest.mark.parametrize('name', ["it's.ass", 'a:b.ass', 'with space.ass', 'x,y.ass'])
def test_unquotable_paths_skip_burning(tmp_path, name):
    assert render._caption_filter(Path('/tmp') / name) is None
    assert render._caption_filter(tmp_path / 'captions.ass').startswith('subtitles=filename=')


def test_only_fresh_scheduled_production_shorts_burn(monkeypatch):
    from app import tasks
    from app.config import settings
    monkeypatch.setattr(settings, 'studio_shorts_burned_captions', True)
    options = {'production_scheduled': True, 'mode': 'production', 'format': 'shorts'}
    assert tasks._burn_short_captions(options) is True
    for change in ({'production_scheduled': False}, {'mode': 'preview'}, {'format': 'landscape'},
                   {'production_delivery': {'version': 1}}):
        assert tasks._burn_short_captions({**options, **change}) is False
    monkeypatch.setattr(settings, 'studio_shorts_burned_captions', False)
    assert tasks._burn_short_captions(options) is False


def test_new_system_default_is_on():
    from app.config import Settings
    assert Settings.model_fields['studio_shorts_burned_captions'].default is True


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),
                    reason='Native media tools required')
def test_real_shorts_render_burns_captions_and_keeps_every_gate(tmp_path, monkeypatch):
    if ' subtitles ' not in subprocess.run(['ffmpeg', '-hide_banner', '-filters'],
                                           capture_output=True, text=True).stdout:
        pytest.skip('ffmpeg without libass')

    def ffmpeg(*args):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], capture_output=True, check=True, timeout=60)

    voice = tmp_path / 'voice.wav'
    ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=3',
           '-c:a', 'pcm_s16le', str(voice))

    def normalize(_spec, path, seconds, *_args):
        ffmpeg('-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:r=30', '-frames:v', str(round(seconds * 30)),
               '-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(path))
    monkeypatch.setattr(render, 'normalize_clip', normalize)

    def bright_pixels(path):
        frame = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '0.5', '-i', str(path), '-frames:v', '1',
                                '-vf', 'format=gray', '-f', 'rawvideo', '-'],
                               capture_output=True, check=True).stdout
        rows = frame[1000 * 1080:1500 * 1080]
        return sum(1 for value in rows if value > 200)

    kwargs = dict(scenes=SCENES, scene_durations=[1.5, 1.5], scene_visual_paths=[['a'], ['b']],
                  output_resolution=render.SHORTS_RESOLUTION)
    plain = render.render_video(voice, ['a', 'b'], 'x', tmp_path / 'plain' / 'final.mp4', **kwargs)
    burned = render.render_video(voice, ['a', 'b'], 'x', tmp_path / 'burned' / 'final.mp4',
                                 burn_captions=True, **kwargs)
    assert (plain['burned_subtitles'], plain['text_layers']) == (False, 0)
    assert (burned['burned_subtitles'], burned['text_layers']) == (True, 1)
    assert burned['frame_count'] == plain['frame_count'] and burned['srt'].endswith('captions.srt')
    assert bright_pixels(plain['path']) == 0 and bright_pixels(burned['path']) > 2000
    # The static picture under changing captions is still caught.
    assert burned['max_freeze_seconds'] >= plain['max_freeze_seconds'] - 0.1 > 2
    landscape = render.render_video(voice, ['a', 'b'], 'x', tmp_path / 'wide' / 'final.mp4', burn_captions=True,
                                    **{**kwargs, 'output_resolution': render.LANDSCAPE_RESOLUTION})
    assert landscape['burned_subtitles'] is False
