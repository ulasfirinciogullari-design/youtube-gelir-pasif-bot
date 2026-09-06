from __future__ import annotations

from urllib.parse import urlsplit


def normalize_evidence_sources(
    value,
    *,
    min_count: int = 2,
    max_count: int = 5,
) -> list[dict]:
    if type(value) is not list:
        raise ValueError('sources must be a JSON list')
    if not min_count <= len(value) <= max_count:
        raise ValueError(
            f'sources must contain between {min_count} and {max_count} records'
        )

    normalized: list[dict] = []
    seen_urls: set[str] = set()
    for source in value:
        if type(source) is not dict or set(source.keys()) != {'url', 'evidence'}:
            raise ValueError(
                'each source must contain exactly url and evidence'
            )
        raw_url = source.get('url')
        raw_evidence = source.get('evidence')
        if type(raw_url) is not str or type(raw_evidence) is not str:
            raise ValueError('source url and evidence must be strings')

        url = raw_url.strip()
        evidence = raw_evidence.strip()
        try:
            parsed = urlsplit(url)
        except Exception as exc:
            raise ValueError('source URL is invalid') from exc
        if (
            parsed.scheme not in {'http', 'https'}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError('source URL needs an http(s) host')
        if len(evidence) < 12:
            raise ValueError('source evidence must be a concrete sentence')
        if url in seen_urls:
            continue
        seen_urls.add(url)
        normalized.append({
            'url': url,
            'evidence': evidence[:600],
        })

    if len(normalized) < min_count:
        raise ValueError(
            f'sources need at least {min_count} unique evidence records'
        )
    return normalized
