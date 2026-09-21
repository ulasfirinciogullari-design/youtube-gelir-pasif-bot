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


VERSION = 4
MARKER = 'INCLUDED_SENTENCE_SOURCE_AUDIT_V4'

# A narrow negative check, not a substitute for semantic source review. A
# chronology-only quote cannot establish an explicitly narrated financing link,
# even if the model marks the whole sentence supported. Matching this pattern
# in a quote does NOT establish entailment or override a negative model verdict.
_FINANCING_CLAIM = re.compile(
    r'\b(?:fund(?:ed|s|ing)|financ(?:ed|es|ing)|bankroll(?:ed|s|ing)|'
    r'finanse|fonla\w*)\b', re.IGNORECASE,
)
_FINANCING_EVIDENCE = re.compile(
    r'\b(?:fund(?:ed|s|ing)?|financ(?:e|ed|es|ing)|bankroll(?:ed|s|ing)?|'
    r'reinvest\w*|finans\w*|fonla\w*)\b', re.IGNORECASE,
)
_TIME_METRIC = re.compile(r'\b(?:times?|durations?)\b', re.IGNORECASE)
_REDUCTION = re.compile(r'\b(?:cut(?:s|ting)?|reduc\w*|shorten\w*|less|lower\w*|decreas\w*|fell)\b', re.IGNORECASE)
_PERCENTAGE = re.compile(r'%|\bpercent\b|\bper\s+cent\b', re.IGNORECASE)
_PROHIBITION = re.compile(r'\b(?:forbidden|prohibit\w*|illegal|unlawful|banned|yasak\w*)\b', re.IGNORECASE)
_PROHIBITION_EVIDENCE = re.compile(
    r'\b(?:forbid\w*|prohibit\w*|illegal|unlawful|banned|yasak\w*)\b|'
    r'\bnot\s+(?:allowed|permitted|legal)\b|\bmust\s+not\b', re.IGNORECASE)


def _quantified_time_reduction(text):
    # A necessary English metric predicate only, never proof of entailment.
    # In particular, "checkout lines moving 40% faster" does not supply it.
    return bool(_TIME_METRIC.search(text) and _REDUCTION.search(text) and _PERCENTAGE.search(text))


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
          'PRESERVE MEASURED QUANTITIES: an increase in speed or throughput is not '
          'the same percentage decrease in elapsed time. For example, checkout lines '
          'moving 40% faster do not establish that transaction times fell by 40%. '
          'Require the narrated metric, direction, percentage and qualifications '
          'to be supported explicitly; do not approve a silently converted measurement. '
          'COST IS NOT PROHIBITION: a process being costly, slow or impractical does '
          'not establish that it is forbidden, illegal or prohibited. Reject that '
          'stronger claim unless the source explicitly states the prohibition. '
          'CHRONOLOGY IS NOT FINANCING: early product success followed by later company '
          'growth does not show that those sales funded, financed or bankrolled a later '
          'product or empire. Even two accurate quotations about the early success and '
          'the later product do not establish the missing financial relationship. Require '
          'explicit evidence of that exact relationship, not a plausible progression. '
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


def validate(response, scenes, pages, *, reject_invalid_quotations=False):
    """Return detached actual review and findings; malformed evidence fails closed."""
    lines, sources = _material(scenes, pages)
    _require(type(response) is dict and set(response) == {'factual_audit', 'editorial_review'}
        and type(response['editorial_review']) is dict)
    audit = response['factual_audit']
    _require(type(audit) is dict and set(audit) == {'sentences'})
    rows = audit['sentences']
    _require(type(rows) is list and len(rows) == len(lines))
    failures, validation_findings = [], []
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
        seen, invalid_quotations = set(), False
        for quote in quotes:
            _require(type(quote) is dict and set(quote) == {'source_url', 'quote'})
            url, text = quote['source_url'], _plain(quote['quote'], 12, 1400)
            _require(type(url) is str)
            exact = url in sources and text in sources[url]['text'] and (url, text) not in seen
            if not exact:
                _require(reject_invalid_quotations is True)
                invalid_quotations = True
            seen.add((url, text))
        if invalid_quotations:
            # Keep the actual model row intact. This is a negative finding,
            # never a reconstructed quotation or an acceptance exception.
            finding = {'position': row['position'], 'assessment': 'unsupported',
                'narration': row['narration'], 'reason':
                'At least one supplied quotation is not unique exact contiguous text '
                'from its cited retrieved source. This sentence is not verified. '
                + row['reason']}
            failures.append(finding)
            validation_findings.append(deepcopy(finding))
        elif row['assessment'] != 'supported':
            failures.append({'position': row['position'], 'assessment': row['assessment'],
                'reason': row['reason'], 'narration': row['narration']})
        elif (_PROHIBITION.search(row['narration'])
                and not any(_PROHIBITION_EVIDENCE.search(q['quote']) for q in quotes)):
            finding = {'position': row['position'], 'assessment': 'unsupported',
                'narration': row['narration'], 'reason':
                'The narration asserts a prohibition, but its exact quotations do '
                'not state one. A costly or time-consuming process is not thereby forbidden.'}
            failures.append(finding)
            validation_findings.append(deepcopy(finding))
        elif (_FINANCING_CLAIM.search(row['narration'])
                and not any(_FINANCING_EVIDENCE.search(q['quote']) for q in quotes)):
            finding = {'position': row['position'], 'assessment': 'unsupported',
                'narration': row['narration'], 'reason':
                'The narration asserts a financing relationship, but none of its exact '
                'source quotations explicitly describes financing. Chronology, product '
                'success and later growth do not establish that link.'}
            failures.append(finding)
            validation_findings.append(deepcopy(finding))
        elif (_quantified_time_reduction(row['narration'])
                and not any(_quantified_time_reduction(q['quote']) for q in quotes)):
            finding = {'position': row['position'], 'assessment': 'unsupported',
                'narration': row['narration'], 'reason':
                'The narration quantifies a reduction in time, but none of its exact '
                'quotations explicitly states a quantified time reduction. A percentage '
                'increase in speed or throughput does not establish that same percentage '
                'decrease in elapsed time. Preserve the source measurement.'}
            failures.append(finding)
            validation_findings.append(deepcopy(finding))
    report = {'version': VERSION, 'accepted': not failures, 'sentences': deepcopy(rows),
        'validation_findings': validation_findings,
        'sources': [{'url': url, 'text_sha256': source['text_sha256'],
            'excerpt_sha256': source['excerpt_sha256']} for url, source in sources.items()]}
    return deepcopy(response['editorial_review']), report, failures
