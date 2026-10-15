"""Give the critic exact source excerpts to cite without retyping quotations.

Identifiers bind URL, complete excerpt bytes and position. Resolving a supplied
identifier is a lookup, never fuzzy quote repair or an entailment verdict.
"""
import hashlib
import json
import re


def catalogue(pages):
    rows = []
    for page in pages:
        text, url = page['text'], page['url']
        digest = hashlib.sha256(text.encode()).hexdigest()
        start = 0
        while start < len(text):
            end = min(start + 1000, len(text))
            if end < len(text):
                # Keep complete sentences when possible; otherwise preserve a
                # contiguous word-bounded excerpt. No punctuation is edited.
                stops = list(re.finditer(r'[.!?](?=\s)', text[start + 450:end]))
                end = start + 450 + stops[-1].end() if stops else text.rfind(' ', start + 450, end)
                if end <= start:
                    end = min(start + 1000, len(text))
            quote = text[start:end]
            # A tiny trailing fragment belongs to the preceding passage.
            if 0 < len(text) - end < 12:
                end = len(text); quote = text[start:end]
            identity = json.dumps([url, digest, start, end], ensure_ascii=False, separators=(',', ':'))
            rows.append({'passage_id': hashlib.sha256(identity.encode()).hexdigest()[:24],
                         'source_url': url, 'quote': quote})
            start = end
    if not rows or len(rows) > 220 or len({row['passage_id'] for row in rows}) != len(rows):
        raise ValueError('included_factual_audit_invalid')
    return rows


def resolve(quotations, passages):
    """Return exact detached quotations; preserve the observed provider reply."""
    lookup = {row['passage_id']: row for row in passages}
    resolved = []
    for citation in quotations:
        if type(citation) is not dict or set(citation) != {'passage_id'}:
            raise ValueError('included_factual_audit_invalid')
        identity = citation['passage_id']
        if type(identity) is not str or identity not in lookup:
            raise ValueError('included_factual_audit_invalid')
        row = lookup[identity]
        resolved.append({key: row[key] for key in ('source_url', 'quote')})
    return resolved
