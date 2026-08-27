from pathlib import Path
import boto3
from botocore.config import Config
from app.config import settings


def _client():
    missing = [
        name for name, value in {
            'BUCKET': settings.bucket,
            'ACCESS_KEY_ID': settings.access_key_id,
            'SECRET_ACCESS_KEY': settings.secret_access_key,
            'ENDPOINT': settings.endpoint,
        }.items() if not value
    ]
    if missing:
        raise RuntimeError('Bucket is not configured: missing ' + ', '.join(missing))

    return boto3.client(
        's3',
        endpoint_url=settings.endpoint,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name=settings.region or 'auto',
        config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'}),
    )


def upload_file(local_path: str | Path, object_key: str, content_type: str = 'video/mp4') -> dict:
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    client = _client()
    client.upload_file(
        str(path),
        settings.bucket,
        object_key,
        ExtraArgs={'ContentType': content_type},
    )
    return {'bucket': settings.bucket, 'key': object_key, 'size': path.stat().st_size}


def presigned_download_url(object_key: str, expires_seconds: int = 86400) -> str:
    return _client().generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.bucket, 'Key': object_key},
        ExpiresIn=expires_seconds,
    )
