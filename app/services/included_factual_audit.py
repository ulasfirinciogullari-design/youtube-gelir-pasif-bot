"""Cited, sentence-by-sentence review inside the existing included critic call.

Exact quotations establish which retrieved text the reviewer used, not that
the text entails the claim. The independent model must judge entailment too;
an unsupported, uncertain or malformed assessment cannot authorize narration.
No provider transport, spending authority or retry lives in this module.
"""
from copy import deepcopy
import hashlib
import json
import re


VERSION = 1
MARKER = 'INCLUDED_SENTENCE_SOURCE_AUDIT_V1'


def _require(value):
    if not value:
        raise ValueError('included_factual_audit_invalid')


def _plain(value, minimum=1, maximum=2000):
    _require(type(value) is str and minimum <= len(value.strip()) <= maximum
        and not any(ord(char) < 32 and char not in '\n\r\t' for char in value))
    return value


def _object(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties),
        'additionalProperties': False}


def _material(scenes, pages):
    _require(type(scenes) is list and 1 <= len(scenes) <= 12
        and type(pages) is list and 1 <= len(pages) <= 5)
    lines = []
    for position, scene in enumerate(scenes):
        _require(type(scene) is dict and type(scene.get('position')) is int
            and scene['position'] == position)
        lines.append({'position': position, 'narration': _plain(scene.get('narration'))})
    sources = {}
    for page in pages:
        _require(type(page) is dict)
        url, text = _plain(page.get('url')), _plain(page.get('text'), 12, 18000)
        digest = page.get('text_sha256')
        _require(re.fullmatch(r'[0-9a-f]{64}', digest or '') is not None
            and url not in sources)
        # Full-page hash can differ from the bounded prompt excerpt hash.
        sources[url] = {'text': text, 'text_sha256': digest,
            'excerpt_sha256': hashlib.sha256(text.encode()).hexdigest()}
    return lines, sources


def request(critic_prompt, critic_schema, scenes, pages):
    """Wrap the complete existing rubric; do not replace or relax its checks."""
    from app.services.included_research_sources import source_prompt
    lines, _ = _material(scenes, pages)
    string = {'type': 'string'}
    quote = _object({'source_url': string, 'quote': string})
    row = _object({'position': {'type': 'integer'}, 'narration': string,
        'assessment': {'type': 'string', 'enum': ['supported', 'unsupported', 'uncertain']},
        'reason': string, 'quotations': {'type': 'array', 'items': quote}})
    schema = _object({'factual_audit': _object({'sentences': {'type': 'array', 'items': row}}),
        'editorial_review': deepcopy(critic_schema)})
    prompt = (MARKER + '\nThe rubric below contains example values, not prefilled verdicts. '
        'Assess the evidence independently before answering every original editorial check.\n'
        + critic_prompt + source_prompt(pages)
        + '\nRESPONSE FORMAT OVERRIDE: return the complete wrapper with factual_audit and '
          'editorial_review, as specified by the supplied schema. Put the complete original '
          'critic response inside editorial_review, preserving every original required check. '
          'For factual_audit, assess EVERY full narration below at its unchanged position, '
          'including implied premises of questions and every clause of a compound sentence. '
          'Copy narration exactly. Use supported only if ALL factual content is entailed by '
          'the retrieved text. Unsupported or merely plausible inferences fail. When unsure, '
          'use uncertain. Supply short, verbatim, contiguous quotations from the retrieved '
          'pages, with the exact source_url; never invent a quote or quote the candidate. '
          'A supported sentence requires at least one quotation. The reason must compare '
          'the precise claim to the cited text, not simply restate that it is supported. '
          'Check subject, time, geography, quantity, causation and qualifications separately. '
          'A TOTAL cost containing several components does not establish that one component '
          'or a subset alone exceeds face value. For example, a total penny cost including '
          'materials, facilities and overhead does NOT establish that metal alone, or labor '
          'and facilities alone, exceed face value. A policy ending new circulating pennies '
          'does NOT end all coin production. Existing legal tender does NOT imply payments '
          'continue everywhere without shortages, rounding or disruption. A possible, local '
          'or historical result must not become universal, guaranteed or current. If any '
          'clause is broader than its evidence, reject the whole sentence and identify that '
          'clause in reason. For a nonfactual question, cite the evidence for its premise '
          'and answer rather than fabricating evidence for a viewer opinion. No rewriting '
          'or omitted lines are allowed in this assessment.\nEXACT FINAL NARRATION TO AUDIT:\n'
        + json.dumps(lines, ensure_ascii=False, separators=(',', ':')))
    return prompt, schema


def validate(response, scenes, pages):
    """Return detached actual review and findings; malformed evidence fails closed."""
    lines, sources = _material(scenes, pages)
    _require(type(response) is dict and set(response) == {'factual_audit', 'editorial_review'}
        and type(response['editorial_review']) is dict)
    audit = response['factual_audit']
    _require(type(audit) is dict and set(audit) == {'sentences'})
    rows = audit['sentences']
    _require(type(rows) is list and len(rows) == len(lines))
    failures = []
    for line, row in zip(lines, rows):
        _require(type(row) is dict and set(row) == {
            'position', 'narration', 'assessment', 'reason', 'quotations'}
            and type(row['position']) is int and row['position'] == line['position']
            and row['narration'] == line['narration']
            and row['assessment'] in {'supported', 'unsupported', 'uncertain'})
        _plain(row['reason'], 12, 1800)
        quotes = row['quotations']
        _require(type(quotes) is list and len(quotes) <= 4
            and (bool(quotes) or row['assessment'] != 'supported'))
        seen = set()
        for quote in quotes:
            _require(type(quote) is dict and set(quote) == {'source_url', 'quote'})
            url, text = quote['source_url'], _plain(quote['quote'], 12, 1400)
            _require(type(url) is str and url in sources and text in sources[url]['text']
                and (url, text) not in seen)
            seen.add((url, text))
        if row['assessment'] != 'supported':
            failures.append({'position': row['position'], 'assessment': row['assessment'],
                'reason': row['reason'], 'narration': row['narration']})
    report = {'version': VERSION, 'accepted': not failures, 'sentences': deepcopy(rows),
        'sources': [{'url': url, 'text_sha256': source['text_sha256'],
            'excerpt_sha256': source['excerpt_sha256']} for url, source in sources.items()]}
    return deepcopy(response['editorial_review']), report, failures
