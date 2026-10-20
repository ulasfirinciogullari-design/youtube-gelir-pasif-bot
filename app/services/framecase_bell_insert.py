"""Original vector animation of the fictional tower's two chimes.

The open bell is an intentional illustrated cutaway. This module neither calls
a provider nor approves media; the complete delivered edit still needs review.
"""
import math
from pathlib import Path
import subprocess

CONTRACT = ('Vertical original 2D animated insert of Bellwick\'s bronze tower bell '
    'inside its navy stone arch, in amber, ivory and muted teal. This is a deliberate '
    'illustrated cutaway: the interior clapper and both curved striking surfaces are '
    'visible. The same suspended bell swings through two clear chimes: the clapper '
    'contacts one inner side, then the other, with a subtle radiating echo at each '
    'contact, then settles. It visualizes the two chimes Mira hears in the recording. '
    'No character, watch-closing action, captions, logos or readable overlay. '
    'This is authored fictional animation, not authentic surveillance or archive.')


def frame(t):
    # Exactly two contacts at t=1 and t=3; both precede the 4.218s spoken cut.
    motion = math.sin(math.pi * min(t, 4) / 2) if t < 4 else 0
    bell_angle = 8 * motion
    clapper_angle = -35 * motion
    echo = max(max(0, 1 - abs(t - hit) / .28) for hit in (1, 3))
    body = '''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1280" viewBox="0 0 720 1280">
<defs>
 <linearGradient id="night" x2=".3" y2="1"><stop stop-color="#203c48"/><stop offset="1" stop-color="#050e1b"/></linearGradient>
 <radialGradient id="glow"><stop stop-color="#c1944c" stop-opacity=".3"/><stop offset="1" stop-color="#b38b4c" stop-opacity="0"/></radialGradient>
 <linearGradient id="stone"><stop stop-color="#182e39"/><stop offset=".5" stop-color="#38565a"/><stop offset="1" stop-color="#102630"/></linearGradient>
 <linearGradient id="bronze"><stop stop-color="#514024"/><stop offset=".25" stop-color="#d6ad66"/><stop offset=".55" stop-color="#805d32"/><stop offset=".82" stop-color="#e7c381"/><stop offset="1" stop-color="#72502b"/></linearGradient>
 <linearGradient id="inside" x2="0" y2="1"><stop stop-color="#453421"/><stop offset="1" stop-color="#0d1c24"/></linearGradient>
</defs>
<rect width="720" height="1280" fill="url(#night)"/>
<ellipse cx="360" cy="610" rx="350" ry="470" fill="url(#glow)"/>
<circle cx="566" cy="167" r="41" fill="#c6ccba" opacity=".62"/>
<circle cx="553" cy="156" r="40" fill="#203b47"/>
'''
    for i in range(44):
        x, y = (i * 173 + 37) % 720, (i * 107 + 43) % 580
        body += f'<circle cx="{x}" cy="{y}" r="{.6 + (i % 3) * .35}" fill="#d4cfaf" opacity="{.25 + .12 * math.sin(t*.7+i):.3f}"/>'
    for x, width, height in ((0, 78, 177), (67, 106, 260), (151, 79, 206),
                              (470, 107, 239), (566, 74, 299), (631, 99, 186)):
        y = 1280-height
        body += (f'<path d="M{x} 1280V{y}l{width/2} -38 {width/2} 38V1280" fill="#071421"/>'
                 f'<path d="M{x+width/2-8} {y+43}h16v31h-16z" fill="#af8141" opacity=".55"/>')
    body += '''<path d="M94 1160V465C94 173 626 173 626 465V1160H566V477C566 250 154 250 154 477V1160Z" fill="url(#stone)" stroke="#517071" stroke-width="2"/>
<path d="M121 1150V465C121 212 599 212 599 465V1150" fill="none" stroke="#7c8a76" stroke-opacity=".35" stroke-width="3"/>
<path d="M77 1100H170V1141H77ZM550 1100H643V1141H550Z" fill="#456064" stroke="#748477" stroke-width="2"/>
<path d="M96 561H152M96 679H152M96 797H152M96 915H152M568 561H624M568 679H624M568 797H624M568 915H624" stroke="#0b202b" stroke-width="4"/>
<path d="M169 405H551V439H169Z" fill="#3b3025" stroke="#a18150" stroke-width="3"/>
<path d="M190 421H530" stroke="#b08b50" stroke-opacity=".55" stroke-width="2"/>
<circle cx="193" cy="422" r="5" fill="#1b2326"/><circle cx="527" cy="422" r="5" fill="#1b2326"/>
'''
    body += f'<g transform="rotate({bell_angle:.5f} 360 426)">'
    body += '''<path d="M329 425H391V469H329Z" fill="url(#bronze)" stroke="#e1bd78" stroke-width="2"/>
<path d="M302 470Q302 455 360 455Q418 455 418 470L438 562Q450 655 498 731L516 751Q527 786 360 795Q193 786 204 751L222 731Q270 655 282 562Z" fill="url(#bronze)" stroke="#d9b573" stroke-width="3"/>
<path d="M316 493Q360 478 404 493L422 567Q431 651 478 727Q481 748 360 768Q239 748 242 727Q289 651 298 567Z" fill="url(#inside)" stroke="#ab8245" stroke-width="3"/>
<path d="M299 479Q360 493 421 479M281 565Q360 585 439 565" fill="none" stroke="#efd49b" stroke-opacity=".54" stroke-width="3"/>
<path d="M222 748Q360 787 498 748M211 775Q360 811 509 775" fill="none" stroke="#f0cd8a" stroke-width="3"/>
'''
    body += f'<g transform="rotate({clapper_angle:.5f} 360 534)">'
    body += '''<path d="M360 534V739" stroke="#17242a" stroke-width="20" stroke-linecap="round"/>
<path d="M357 539V734" stroke="#be9253" stroke-width="7" stroke-linecap="round"/>
<ellipse cx="360" cy="741" rx="19" ry="23" fill="url(#bronze)" stroke="#e0b16c" stroke-width="2"/>
<circle cx="360" cy="534" r="13" fill="#b18b51" stroke="#f1cc84" stroke-width="3"/>
</g></g>'''
    body += f'<g opacity="{echo:.4f}" fill="none" stroke="#e4bf7c" stroke-width="3">'
    body += '<path d="M166 622Q120 725 168 811M136 598Q72 724 135 840M554 622Q600 725 552 811M584 598Q648 724 585 840"/></g>'
    body += '<path d="M180 1197Q360 1155 540 1197" fill="none" stroke="#416366" stroke-opacity=".4" stroke-width="2"/></svg>'
    return body


def render(output, seconds):
    if type(seconds) is not int or not 5 <= seconds <= 10:
        raise ValueError('Invalid bell edit window')
    output = Path(output); work = output.parent / (output.stem + '_art'); work.mkdir(exist_ok=True)
    for index in range(seconds * 30):
        (work / f'frame_{index:04d}.svg').write_text(frame(index / 30))
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-threads', '1', '-framerate', '30',
        '-i', str(work / 'frame_%04d.svg'), '-an', '-frames:v', str(seconds * 30),
        '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', '18',
        '-pix_fmt', 'yuv420p', str(output)], check=True, capture_output=True, timeout=180)
    return {'renderer': 'framecase_twin_bell_v1', 'new_provider_requests': 0,
            'authored_chime_seconds': [1.0, 3.0]}
