"""An authored fictional timeline insert using owned footage and exact clocks.

No provider request, stock download, or quality approval occurs here. This is
an alternative editorial shot, not a resubmission of filtered provider media.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess

CONTRACT = ('An authored 2D fictional evidence insert in navy, amber and ivory. '
    'The previously generated gallery exit plays in a framed portrait monitor. '
    'Below it, two designed clock dials tick continuously: the camera reads 23:59, '
    'the cracked independent watch reads 00:00, exactly one minute ahead. '
    'These precise fictional times are deliberately drawn graphics, not generated '
    'text or authentic surveillance evidence. The insert explains Mira comparing '
    'the camera timeline with her father\'s watch; no actor or jog-dial shot is used.')


def eligible(dispatch, checkpoint, journal, index):
    """Only the actual terminal refusal in this benign, authored first episode."""
    from app.services import content_plan as plan
    from app.services.framecase_cadence import CHANNEL_ID
    from app.services.fal_video import _fal_error_types
    from app.services.youtube_auth import _decrypt_json
    series = dispatch['item'].get('series') or {}
    if (index not in (2, 3) or dispatch['channel_id'] != CHANNEL_ID
            or series.get('id') != 'framecase_clock_that_lied_s01' or series.get('number') != 1
            or '1' not in checkpoint.get('clips', {})
            or (journal.get('context') or {}).get('lineage_id') != dispatch['task_id']):
        return False
    digest = plan._sha(checkpoint['package'])
    for row in journal.get('requests', {}).values():
        request, result = row.get('request') or {}, row.get('result') or {}
        if (request.get('scene_index') != index or request.get('package_sha256') != digest
                or result.get('http_status') != 422):
            continue
        raw = _decrypt_json(result['encrypted_response'])['response'].encode()
        if hashlib.sha256(raw).hexdigest() != result['response_sha256']:
            raise ValueError('Unverified original refusal')
        if 'content_policy_violation' in _fal_error_types(json.loads(raw)):
            return True
    return False


def _svg(path, body, width, height):
    svg = path.with_suffix('.svg')
    svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                   f'viewBox="0 0 {width} {height}">{body}</svg>')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-threads', '1', '-i', str(svg),
                    '-frames:v', '1', '-threads', '1', str(path)], check=True, capture_output=True, timeout=30)


def _face(cx, cy, *, watch):
    body = (f'<circle cx="{cx}" cy="{cy}" r="138" fill="#060e18"/>'
        f'<circle cx="{cx}" cy="{cy}" r="130" fill="#a17b40" stroke="#e6c584" stroke-width="3"/>'
        f'<circle cx="{cx}" cy="{cy}" r="116" fill="#f0e6cc" stroke="#785b31" stroke-width="4"/>')
    for tick in range(60):
        angle = tick * math.pi / 30
        inner = 95 if tick % 5 == 0 else 103
        x1, y1 = cx + inner * math.sin(angle), cy - inner * math.cos(angle)
        x2, y2 = cx + 110 * math.sin(angle), cy - 110 * math.cos(angle)
        body += f'<path d="M{x1:.3f} {y1:.3f}L{x2:.3f} {y2:.3f}" stroke="#192836" stroke-width="{3 if tick % 5 == 0 else 1}"/>'
    for number, x, y in (('12', cx, cy-70), ('3', cx+76, cy+8), ('6', cx, cy+86), ('9', cx-76, cy+8)):
        body += f'<text x="{x}" y="{y}" text-anchor="middle" font-family="serif" font-size="24" fill="#182c39">{number}</text>'
    if watch:
        body += (f'<circle cx="{cx}" cy="{cy-158}" r="19" fill="none" stroke="#cea461" stroke-width="7"/>'
            f'<path d="M{cx-77} {cy-84}l28 37 -14 29 38 28 -9 34 43 56m-34-90 48-15 22 24" '
            'fill="none" stroke="#adbdba" stroke-opacity=".72" stroke-width="2"/>')
    return body


def render(gallery, output, seconds):
    """Render real object animation at 30 fps; source footage is never reversed."""
    if type(seconds) is not int or not 5 <= seconds <= 10:
        raise ValueError('Invalid clue edit window')
    output = Path(output); work = output.parent / (output.stem + '_art'); work.mkdir(exist_ok=True)
    body = ('<defs><linearGradient id="bg" x2=".8" y2="1"><stop stop-color="#173340"/>'
        '<stop offset="1" stop-color="#080f1a"/></linearGradient></defs>'
        '<rect width="720" height="1280" fill="url(#bg)"/>'
        '<rect x="129" y="31" width="462" height="756" rx="18" fill="#070d16" stroke="#b58b4a" stroke-width="3"/>'
        '<path d="M45 843H675" stroke="#846b43"/>'
        '<text x="360" y="829" text-anchor="middle" font-family="serif" font-size="23" letter-spacing="4" fill="#ddc395">ONE MINUTE APART</text>')
    for cx, watch, label, time in ((190, False, 'CAMERA', '23:59'), (530, True, 'THE WATCH', '00:00')):
        body += _face(cx, 1038, watch=watch)
        body += (f'<text x="{cx}" y="1196" text-anchor="middle" font-family="sans-serif" font-size="17" letter-spacing="3" fill="#acbab7">{label}</text>'
            f'<text x="{cx}" y="1246" text-anchor="middle" font-family="serif" font-size="36" letter-spacing="5" fill="#e6d3ac">{time}</text>')
    background = work / 'background.png'; _svg(background, body, 720, 1280)
    hands = []
    for name, length, width, color in (('hour', 57, 7, '#142733'), ('minute', 86, 4, '#142733'), ('second', 101, 2, '#ad582e')):
        path = work / (name + '.png')
        _svg(path, f'<path d="M130 142V{130-length}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'
             '<circle cx="130" cy="130" r="5" fill="#b98c43"/>', 260, 260)
        hands.append(path)
    command = ['ffmpeg', '-y', '-v', 'error', '-filter_complex_threads', '1', '-threads', '1',
               '-loop', '1', '-i', str(background), '-threads', '1', '-i', str(gallery)]
    for hand in hands * 2:
        command += ['-loop', '1', '-threads', '1', '-i', str(hand)]
    filters = ['[0:v]format=rgba[bg]',
        '[1:v]scale=408:726:flags=lanczos,fps=30,tpad=stop_mode=clone:stop_duration=10[gallery]',
        '[bg][gallery]overlay=156:46:shortest=0[v0]']
    for clock, start in enumerate((86375, 35)):
        for index, period in enumerate((43200, 3600, 60)):
            n = clock * 3 + index
            filters += [f'[{n+2}:v]format=rgba,rotate=2*PI*({start % period}+t)/{period}:c=none:ow=iw:oh=ih[h{n}]',
                        f'[v{n}][h{n}]overlay={60+340*clock}:908:shortest=0[v{n+1}]']
    command += ['-filter_complex', ';'.join(filters), '-map', '[v6]', '-an', '-r', '30',
        '-frames:v', str(seconds * 30), '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast',
        '-crf', '18', '-pix_fmt', 'yuv420p', str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=180)
    return {'renderer': 'framecase_clock_timeline_v1', 'new_provider_requests': 0,
            'source_sha256': hashlib.sha256(Path(gallery).read_bytes()).hexdigest()}
