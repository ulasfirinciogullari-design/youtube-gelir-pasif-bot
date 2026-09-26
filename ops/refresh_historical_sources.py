"""Existing VPS: refresh reviewed public historical references without an AI call."""
import base64
import json
from pathlib import Path
import shlex
import subprocess
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import historical_source_cache as cache

RUNS = Path('/home/youtube-dev/runs')
CLI = RUNS / 'railway-access-cli/node_modules/@railway/cli/bin/railway'


def refresh():
    records = [cache.capture(url) for url in cache.URLS]
    payload = base64.b64encode(zlib.compress(json.dumps(records).encode())).decode('ascii')
    program = ("import os,json,base64,zlib\n"
        "from app.services import historical_source_cache as h,content_plan as p\n"
        "records=json.loads(zlib.decompress(base64.b64decode(" + repr(payload) + ")))\n"
        "fd=os.dup(1);null=os.open(os.devnull,os.O_WRONLY);os.dup2(null,1);os.dup2(null,2);os.close(null)\n"
        "try:\n result={'status':'refreshed','sources':[h.install(p._client(),v)for v in records]}\n"
        "except Exception:\n result={'status':'unavailable'}\n"
        "with os.fdopen(fd,'w')as out:out.write('SOURCE_REFRESH:'+json.dumps(result,sort_keys=True)+'\\n')\n")
    # Send the bounded source bodies through stdin, rather than an OS-sized argv.
    wire = base64.b64encode(zlib.compress(program.encode(), 9)).decode('ascii')
    receiver = ('import sys,base64,zlib;exec(zlib.decompress(base64.b64decode('
                'sys.stdin.buffer.read(' + str(len(wire)) + '))))')
    result = subprocess.run([str(CLI), 'ssh', '-i', '/home/youtube-dev/.ssh/railway-youtube-operations-ed25519',
        '-p', '2533bc40-884a-4aa3-adaf-fa2e94da9f44', '-e', '433ef374-8f84-4a3e-8970-ccb42ab2cbc0',
        '-s', 'f01382fd-8c4c-49fe-af21-20c6a066f3e7', '--', 'python -c ' + shlex.quote(receiver)],
        input=wire + '\n', capture_output=True, text=True, timeout=55)
    rows = [v.split(':', 1)[1] for v in result.stdout.splitlines() if v.startswith('SOURCE_REFRESH:')]
    if result.returncode or len(rows) != 1:
        raise RuntimeError('source_refresh_unavailable')
    return json.loads(rows[0])


if __name__ == '__main__':
    try:
        result = refresh()
    except Exception:
        result = {'status': 'unavailable'}
    from datetime import datetime, timezone
    result['observed_at'] = datetime.now(timezone.utc).isoformat()
    temporary = RUNS / 'historical-sources-latest.tmp'
    temporary.write_text(json.dumps(result, sort_keys=True)); temporary.chmod(0o600)
    temporary.replace(RUNS / 'historical-sources-latest.json')
    print(json.dumps(result, sort_keys=True))
    sys.exit(0 if result['status'] == 'refreshed' else 1)
