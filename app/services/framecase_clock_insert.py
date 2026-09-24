"""Original moving clock artwork with a mathematically fixed story clue.

No provider request or quality approval. The native clips and rejected verdict
remain evidence; this new authored shot requires independent story and film QA.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess

from app.services.framecase_clue_insert import _face

VERSION = 'framecase_opening_clocks_v1'
CONTRACT = ('Original illustrated 2D mystery opening in midnight navy, amber, ivory and teal. '
    'A cracked brass pocket watch rests on a rain-dark stone ledge in the foreground; '
    'the large municipal clock is visible on its tower behind it. Both clock dials '
    'have continuously moving second hands. The city reads 23:59, the independent '
    'watch reads 00:00: exactly one minute ahead throughout the shot. Stable clean '
    'dial numbers and small designed time labels make this authored clue legible. '
    'Gentle falling rain moves through the illustrated city. No person is depicted. '
    'The narration reports the earlier theft; the adjacent gallery shot establishes '
    'its aftermath with the guard and empty frame. No magical effects, '
    'spinning random clock hands, morphing cracks, camera-only motion or fake archive.')


def eligible(dispatch, checkpoint, journal):
    from app.services.framecase_cadence import CHANNEL_ID
    from app.services import content_plan as plan
    from app.services.youtube_auth import _decrypt_json
    series = dispatch['item'].get('series') or {}
    rows = journal.get('requests') or {}
    original = checkpoint.get('authored_replaced_clips', {}).get('0') or checkpoint.get('clips', {}).get('0', {})
    basis = checkpoint.get('opening_animation_basis') or checkpoint
    reviews = (basis.get('visual_qc') or {}).get('reviews', [])
    if (dispatch['channel_id'] != CHANNEL_ID or series.get('id') != 'framecase_clock_that_lied_s01'
            or series.get('number') != 1 or (journal.get('context') or {}).get('lineage_id') != dispatch['task_id']
            or len(rows) != 6 or original.get('revision') != 2
            or not any(row.get('scene_index') == 0 and row.get('revision') == 2
                       and row.get('asset') == original.get('asset') for row in checkpoint.get('creates', []))
            or len(str(basis.get('review_master_sha256') or '')) != 64
            or not any(row.get('scene_index') == 0 and type(row.get('score')) is int
                       and row['score'] < 86 for row in reviews)):
        return False
    package_sha = plan._sha(checkpoint['package'])
    for row in rows.values():
        result = row.get('result') or {}
        if ((row.get('request') or {}).get('package_sha256') != package_sha
                or result.get('http_status') not in (200, 422)):
            return False  # An accepted but unobserved result is never replaced.
        raw = _decrypt_json(result['encrypted_response'])['response'].encode()
        if hashlib.sha256(raw).hexdigest() != result['response_sha256']:
            raise ValueError('Unverified native clock result')
    return True


def hands(t, start):
    body = ''
    for period, length, width, color in ((43200, 57, 7, '#142733'),
            (3600, 86, 4, '#142733'), (60, 101, 2.4, '#ad582e')):
        angle = 360 * ((start + t) % period) / period
        body += (f'<path d="M0 12V{-length}" transform="rotate({angle:.7f})" '
                 f'stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>')
    return body + '<circle r="6" fill="#b98c43" stroke="#f0dcb3" stroke-width="1"/>'


def frame(t):
    body = '''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1280" viewBox="0 0 720 1280">
<defs>
 <linearGradient id="sky" x2=".4" y2="1"><stop stop-color="#24414f"/><stop offset="1" stop-color="#091320"/></linearGradient>
 <linearGradient id="tower"><stop stop-color="#172d39"/><stop offset=".6" stop-color="#436063"/><stop offset="1" stop-color="#263f4a"/></linearGradient>
 <radialGradient id="amber"><stop stop-color="#ddb574" stop-opacity=".45"/><stop offset="1" stop-color="#ddb574" stop-opacity="0"/></radialGradient>
 <linearGradient id="ledge" x2="0" y2="1"><stop stop-color="#59716d"/><stop offset=".2" stop-color="#253e47"/><stop offset="1" stop-color="#101f2c"/></linearGradient>
</defs>
<rect width="720" height="1280" fill="url(#sky)"/>
<ellipse cx="525" cy="365" rx="248" ry="330" fill="url(#amber)"/>
<circle cx="135" cy="156" r="39" fill="#d6d3b8" opacity=".6"/>
<circle cx="120" cy="146" r="39" fill="#24414f"/>
'''
    for x, width, y in ((0, 103, 592), (91, 129, 457), (214, 95, 525), (289, 76, 567)):
        body += f'<path d="M{x} 1115V{y}l{width/2} -42 {width/2} 42V1115Z" fill="#0d2432" stroke="#32505a" stroke-width="2"/>'
        for row in range(4):
            for col in range(2):
                body += f'<rect x="{x+18+col*width/2}" y="{y+40+row*81}" width="14" height="29" rx="7" fill="#c4a063" opacity="{.3+.06*((row+col)%3)}"/>'
    body += '''<path d="M351 1106V244L525 120 699 244V1106Z" fill="url(#tower)" stroke="#657971" stroke-width="3"/>
<path d="M333 246L525 99 714 246H685L525 133 371 246Z" fill="#122d3a" stroke="#ac9160" stroke-width="3"/>
<path d="M379 587H674M379 733H674M379 879H674M379 1025H674" stroke="#142c37" stroke-width="5"/>
<path d="M391 1103V666Q434 610 477 666V1103M570 1103V666Q613 610 656 666V1103" fill="#102431" stroke="#59706c" stroke-width="3"/>
'''
    body += '<g transform="translate(525 398) scale(1.12)">' + _face(0, 0, watch=False) + hands(t, 86345) + '</g>'
    body += '<text x="525" y="594" text-anchor="middle" font-family="serif" font-size="38" letter-spacing="5" fill="#e6d3ac">23:59</text>'
    for i in range(64):
        x, y = (i * 173 + 23) % 740 - 10, (i * 113 + t * 98) % 1190
        body += f'<path d="M{x:.2f} {y:.2f}l-5 19" stroke="#adc4c2" stroke-opacity=".16" stroke-width="1.2"/>'
    body += '''<path d="M0 1047Q355 993 720 1078V1280H0Z" fill="url(#ledge)"/>
<path d="M0 1050Q355 996 720 1081" fill="none" stroke="#94a397" stroke-opacity=".55" stroke-width="4"/>
<ellipse cx="285" cy="1095" rx="252" ry="37" fill="#051322" opacity=".7"/>
<path d="M289 563C109 497 39 693 95 827" fill="none" stroke="#413624" stroke-width="11"/>
<path d="M289 563C109 497 39 693 95 827" fill="none" stroke="#c39e61" stroke-width="5" stroke-dasharray="8 4"/>
'''
    body += '<g transform="translate(285 884) scale(1.62)">' + _face(0, 0, watch=True) + hands(t, 5) + '</g>'
    body += '<text x="285" y="1175" text-anchor="middle" font-family="serif" font-size="46" letter-spacing="5" fill="#efdab2">00:00</text></svg>'
    return body


def render(output, seconds):
    if type(seconds) is not int or not 5 <= seconds <= 10:
        raise ValueError('Invalid opening edit window')
    output = Path(output); work = output.parent / (output.stem + '_art'); work.mkdir(exist_ok=True)
    for index in range(seconds * 30):
        (work / f'frame_{index:04d}.svg').write_text(frame(index / 30))
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-threads', '1', '-framerate', '30',
        '-i', str(work / 'frame_%04d.svg'), '-an', '-frames:v', str(seconds * 30),
        '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '18',
        '-pix_fmt', 'yuv420p', str(output)], check=True, capture_output=True, timeout=180)
    return {'renderer': VERSION, 'new_provider_requests': 0, 'clock_offset_seconds': 60}
