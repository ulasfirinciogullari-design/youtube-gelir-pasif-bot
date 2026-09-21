from copy import deepcopy
import hashlib

import pytest

from app.services import included_source_passages as passages, included_factual_audit as audit
from test_included_factual_audit import PAGES, LINES, response


@pytest.mark.parametrize('assessment', ['supported', 'unsupported', 'uncertain'])
def test_lookup_preserves_exact_source_and_observed_negative_verdict(assessment):
    catalog = passages.catalogue(PAGES)
    actual = response()
    actual['factual_audit']['sentences'] = actual['factual_audit']['sentences'][:1]
    row = actual['factual_audit']['sentences'][0]
    row.update(assessment=assessment, quotations=[{'passage_id': catalog[0]['passage_id']}])
    before = deepcopy(actual)
    _, report, failures = audit.validate(actual, LINES[:1], PAGES)
    assert report['accepted'] is (assessment == 'supported')
    assert bool(failures) is (assessment != 'supported')
    assert actual == before and report['sentences'] == before['factual_audit']['sentences']
    assert report['referenced_passages'] == catalog
    assert catalog[0]['quote'] == PAGES[0]['text']


@pytest.mark.parametrize('damage', ['text', 'url', 'id', 'extra', 'mixed', 'duplicate'])
def test_reference_cannot_select_changed_or_unknown_text(damage):
    pages = deepcopy(PAGES); catalog = passages.catalogue(pages)
    actual = response(); actual['factual_audit']['sentences'] = actual['factual_audit']['sentences'][:1]
    row = actual['factual_audit']['sentences'][0]
    row['quotations'] = [{'passage_id': catalog[0]['passage_id']}]
    if damage == 'text': pages[0]['text'] += ' New source version.'
    elif damage == 'url': pages[0]['url'] += '/different'
    elif damage == 'id': row['quotations'][0]['passage_id'] = 'a' * 24
    elif damage == 'extra': row['quotations'][0]['approved'] = True
    elif damage == 'mixed': row['quotations'].append({'source_url': pages[0]['url'], 'quote': pages[0]['text']})
    else: row['quotations'] *= 2
    with pytest.raises(ValueError): audit.validate(actual, LINES[:1], pages)


def test_catalogue_preserves_all_characters_and_bounded_contiguous_evidence():
    text = ('An exact sentence, including punctuation and qualifications. ' * 304)[:18000]
    source = {**PAGES[0], 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
    catalog = passages.catalogue([source])
    assert ''.join(row['quote'] for row in catalog) == text
    assert all(12 <= len(row['quote']) <= 1011 for row in catalog)
    assert len({row['passage_id'] for row in catalog}) == len(catalog)
    other = {**source, 'retrieved_at': '2099-01-01', 'body_sha256': 'c' * 64}
    assert passages.catalogue([other]) == catalog


def test_current_request_exposes_ids_and_exact_passages_without_freeform_quote_copying():
    prompt, schema = audit.request('Complete editorial rubric.', {'type': 'object'}, LINES, PAGES)
    quote_schema = schema['properties']['factual_audit']['properties']['sentences']['items']['properties']['quotations']['items']
    catalog = passages.catalogue(PAGES)
    assert quote_schema['required'] == ['passage_id']
    assert quote_schema['properties']['passage_id']['enum'] == [r['passage_id'] for r in catalog]
    assert PAGES[0]['text'] in prompt and 'independently check every narrated clause' in prompt


def test_exact_passage_does_not_establish_an_invented_financing_relation():
    catalog = passages.catalogue(PAGES); actual = response()
    line = {'position': 0, 'narration': 'The existing penny sales funded a new factory.'}
    row = actual['factual_audit']['sentences'][0]
    row.update(line, quotations=[{'passage_id': catalog[0]['passage_id']}])
    actual['factual_audit']['sentences'] = [row]
    _, report, failures = audit.validate(actual, [line], PAGES)
    assert not report['accepted'] and 'financing' in failures[0]['reason']
