"""VPS cron: confirm an old Railway replica is gone, then fence unpublished work.

No age-based unlocking, deployment, model request, task enqueue, refund or
publication. Credentials stay in the existing operations CLI and SSH key.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

RUNS = Path('/home/youtube-dev/runs')
CLI = RUNS / 'railway-access-cli/node_modules/@railway/cli/bin/railway'
IDENTITY = '/home/youtube-dev/.ssh/railway-youtube-operations-ed25519'
PROJECT = '2533bc40-884a-4aa3-adaf-fa2e94da9f44'
ENV = '433ef374-8f84-4a3e-8970-ccb42ab2cbc0'
SERVICE = 'f01382fd-8c4c-49fe-af21-20c6a066f3e7'


def command(args, body=None):
    result = subprocess.run([str(CLI), *args], input=body, capture_output=True, text=True, timeout=45)
    if result.returncode or len(result.stdout) > 256_000:
        raise RuntimeError('operations_read_unavailable')
    return result.stdout


def remote(operation, candidate=None, observation=None):
    if operation not in {'candidates', 'record_removed_replica'}:
        raise ValueError('unsupported_operation')
    # Serialize data as a Python literal inside a quoted, fixed remote program;
    # never interpolate a returned string as executable shell text.
    data = {'operation': operation, 'candidate': candidate, 'observation': observation}
    program = ("import os,json\nfrom app.services import production_worker_execution as w\n"
        "data=" + repr(data) + "\n"
        "fd=os.dup(1);null=os.open(os.devnull,os.O_WRONLY);os.dup2(null,1);os.dup2(null,2);os.close(null)\n"
        "try:\n result=w.candidates() if data['operation']=='candidates' else w.record_removed_replica(data['candidate'],data['observation'])\n"
        "except Exception:\n result={'status':'unverified'}\n"
        "with os.fdopen(fd,'w')as out:out.write('WATCHDOG_RESULT:'+json.dumps(result,sort_keys=True)+'\\n')\n")
    raw = command(['ssh', '-i', IDENTITY, '-p', PROJECT, '-e', ENV, '-s', SERVICE,
                   '--', 'python -c ' + shlex.quote(program)])
    rows = [line.split(':', 1)[1] for line in raw.splitlines() if line.startswith('WATCHDOG_RESULT:')]
    if len(rows) != 1:
        raise RuntimeError('worker_observation_unavailable')
    return json.loads(rows[0])


def removal_observation(candidate, current, query):
    owner = candidate['execution']['identity']
    if (not isinstance(owner, dict) or owner.get('instance_id') == current.get('instance_id')
            or any(owner.get(k) != value or current.get(k) != value for k, value in (
                ('project_id', PROJECT), ('environment_id', ENV), ('service_id', SERVICE)))
            or not all(isinstance(owner.get(k), str) and re.fullmatch(r'[0-9a-f-]{36}', owner[k])
                       for k in ('deployment_id', 'instance_id'))):
        return None
    payload = query(owner['deployment_id'])
    if type(payload) is not dict or payload.get('errors'):
        return None
    deployment = payload.get('data', {}).get('deployment')
    if (type(deployment) is not dict or deployment.get('id') != owner['deployment_id']
            or any(deployment.get(k) != value for k, value in (
                ('projectId', PROJECT), ('environmentId', ENV), ('serviceId', SERVICE)))
            or type(deployment.get('instances')) is not list):
        return None
    instances = [row for row in deployment['instances'] if type(row) is dict and row.get('id') == owner['instance_id']]
    if len(instances) != 1 or instances[0].get('status') != 'REMOVED':
        return None
    return {'source': 'authenticated_railway_deployment_query',
        **{k: owner[k] for k in ('project_id', 'environment_id', 'service_id', 'deployment_id', 'instance_id')},
        'instance_status': 'REMOVED', 'observed_at': datetime.now(timezone.utc).isoformat(),
        'response_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def query_deployment(deployment_id):
    query = 'query($id:String!){deployment(id:$id){id projectId environmentId serviceId status instances{id status}}}'
    return json.loads(command(['api', query, '--variables', '@-', '--compact'], json.dumps({'id': deployment_id})))


def run():
    snapshot = remote('candidates')
    current, candidates = snapshot.get('current_identity'), snapshot.get('candidates')
    if type(current) is not dict or type(candidates) is not list or len(candidates) > 2:
        raise RuntimeError('worker_snapshot_unverified')
    actions = []
    for candidate in candidates:
        proof = removal_observation(candidate, current, query_deployment)
        if proof is not None:
            actions.append(remote('record_removed_replica', candidate, proof))
    return {'status': 'checked', 'candidate_count': len(candidates), 'actions': actions,
            'observed_at': datetime.now(timezone.utc).isoformat()}


if __name__ == '__main__':
    try:
        result = run()
    except Exception:
        result = {'status': 'unavailable', 'actions': [], 'observed_at': datetime.now(timezone.utc).isoformat()}
    # One small latest-status file; durable recovery evidence is in Redis.
    target = RUNS / 'production-watchdog-latest.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, sort_keys=True))
    temporary.chmod(0o600)
    temporary.replace(target)
    print(json.dumps(result, sort_keys=True))
