from copy import deepcopy
import hashlib
import json

import pytest

from app.services import included_factual_audit as audit


TEXT = ('The total cost of producing a penny includes the costs of materials, '
        'facilities, and overhead. Existing pennies remain legal tender. '
        'New circulating pennies are no longer manufactured.')
URL = 'https://home.treasury.gov/news/featured-stories/penny-production-cessation-faqs'
PAGES = [{'url': URL, 'text': TEXT, 'text_sha256': hashlib.sha256(TEXT.encode()).hexdigest()}]
LINES = [{'position': 0, 'narration': 'Toplam maliyet; malzemeleri, tesisleri ve genel giderleri kapsıyordu.'},
         {'position': 1, 'narration': 'İşçilik ve tesis giderleri madeni paranın değerini çoktan aştı.'},
         {'position': 2, 'narration': 'Yüksek harcamalar yüzünden yeni madeni paraların basımı durduruldu.'},
         {'position': 3, 'narration': 'Mevcut sentler piyasada alışverişi aksatmadan ödemeleri tamamlıyor.'}]


def response():
    return {'editorial_review': {'positive': True}, 'factual_audit': {'sentences': [
        {**line, 'assessment': 'supported' if position == 0 else 'unsupported',
         'reason': ('The quoted total includes all listed costs together.' if position == 0 else
                    'The cited source does not establish the additional assertion in this sentence.'),
         'quotations': [{'source_url': URL, 'quote': TEXT.split('. ')[0] + '.'}]}
        for position, line in enumerate(LINES)]}}


def test_actual_negative_findings_override_a_positive_editorial_review_without_changing_evidence():
    actual = response(); before = deepcopy(actual)
    critic, report, failures = audit.validate(actual, LINES, PAGES)
    assert critic == actual['editorial_review']
    assert report['accepted'] is False
    assert [row['position'] for row in failures] == [1, 2, 3]
    assert report['sentences'] == actual['factual_audit']['sentences']
    assert report['sources'][0]['excerpt_sha256'] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert actual == before
    report['sentences'][0]['assessment'] = 'uncertain'
    assert actual == before


@pytest.mark.parametrize('change', [
    'missing_sentence', 'extra_sentence', 'duplicate_position', 'bool_position', 'changed_narration',
    'changed_quote', 'foreign_url', 'duplicate_quote', 'empty_quotes_supported', 'empty_reason',
    'extra_field', 'unknown_verdict', 'extra_envelope', 'no_audit', 'noncontiguous_quote',
])
@pytest.mark.parametrize('repair_evidence', [False, True])
def test_forged_partial_or_ambiguous_source_assessments_are_rejected(change, repair_evidence):
    value = response(); rows = value['factual_audit']['sentences']; row = rows[0]
    if change == 'missing_sentence':rows.pop()
    elif change == 'extra_sentence':rows.append(deepcopy(rows[-1]))
    elif change == 'duplicate_position':rows[1]['position'] = 0
    elif change == 'bool_position':row['position'] = False
    elif change == 'changed_narration':row['narration'] = 'A different claim was reviewed.'
    elif change == 'changed_quote':row['quotations'][0]['quote'] = 'This quote never appeared in the source.'
    elif change == 'foreign_url':row['quotations'][0]['source_url'] = 'https://example.org/'
    elif change == 'duplicate_quote':row['quotations'].append(deepcopy(row['quotations'][0]))
    elif change == 'empty_quotes_supported':row['quotations'] = []
    elif change == 'empty_reason':row['reason'] = ''
    elif change == 'extra_field':row['waive'] = True
    elif change == 'unknown_verdict':row['assessment'] = 'probably'
    elif change == 'extra_envelope':value['pass'] = True
    elif change == 'no_audit':value.pop('factual_audit')
    elif change == 'noncontiguous_quote':row['quotations'][0]['quote'] = 'The total cost ... Existing pennies remain legal tender.'
    if repair_evidence and change in {'changed_quote', 'foreign_url', 'duplicate_quote', 'noncontiguous_quote'}:
        before = deepcopy(value)
        critic, report, failures = audit.validate(value, LINES, PAGES, reject_invalid_quotations=True)
        assert critic == {'positive': True} and not report['accepted']
        assert [item['position'] for item in failures] == [0, 1, 2, 3]
        assert failures[0]['assessment'] == 'unsupported'
        assert report['validation_findings'] == [failures[0]]
        assert report['sentences'] == before['factual_audit']['sentences'] and value == before
    else:
        with pytest.raises(ValueError):
            audit.validate(value, LINES, PAGES, reject_invalid_quotations=repair_evidence)


def test_reversed_real_sentences_are_negative_evidence_not_an_accepted_quotation():
    first = 'A "star" sheet is used to replace the imperfect sheet.'
    second = 'Reusing an exact serial number to replace an imperfect note is costly and time consuming.'
    text = first + ' ' + second
    pages = [{'url': URL, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
    lines = [{'position': 0, 'narration': 'Re-running duplicate numbers is forbidden, so presses insert special star sheets.'}]
    actual = {'editorial_review': {'causal_claim_supported': True}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': 'supported', 'reason': 'The quotations describe the replacement process.',
         'quotations': [{'source_url': URL, 'quote': second + ' ' + first}]}]}}
    before = deepcopy(actual)
    with pytest.raises(ValueError):audit.validate(actual, lines, pages)
    _, report, failures = audit.validate(actual, lines, pages, reject_invalid_quotations=True)
    assert not report['accepted'] and len(failures) == 1
    assert 'contiguous' in failures[0]['reason']
    assert report['sentences'] == before['factual_audit']['sentences'] and actual == before


@pytest.mark.parametrize('claim', ['forbidden', 'prohibited', 'illegal', 'unlawful', 'banned', 'yasaktır'])
def test_cost_does_not_establish_a_prohibition_even_with_an_exact_quote_and_positive_review(claim):
    text = 'Reusing an exact serial number to replace an imperfect note is costly and time consuming.'
    lines = [{'position': 0, 'narration': f'Re-running duplicate numbers is {claim}.'}]
    pages = [{'url': URL, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
    actual = {'editorial_review': {'causal_claim_supported': True}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': 'supported', 'reason': 'Compare the claimed prohibition with its source.',
         'quotations': [{'source_url': URL, 'quote': text}]}]}}
    before = deepcopy(actual)
    _, report, failures = audit.validate(actual, lines, pages)
    assert not report['accepted'] and len(failures) == 1
    assert 'prohibition' in failures[0]['reason'] and report['validation_findings'] == failures
    assert report['sentences'][0]['assessment'] == 'supported' and actual == before


@pytest.mark.parametrize('assessment', ['supported', 'unsupported', 'uncertain'])
def test_prohibition_words_do_not_override_the_independent_semantic_verdict(assessment):
    text = 'The rule prohibits reusing the same serial number.'
    lines = [{'position': 0, 'narration': 'Reusing that serial number is prohibited.'}]
    pages = [{'url': URL, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
    actual = {'editorial_review': {}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': assessment, 'reason': 'The exact rule and affected number must match.',
         'quotations': [{'source_url': URL, 'quote': text}]}]}}
    _, report, failures = audit.validate(actual, lines, pages)
    assert report['accepted'] is (assessment == 'supported')
    assert bool(failures) is (assessment != 'supported') and report['validation_findings'] == []


def test_no_quote_is_a_rejection_not_a_reason_to_invent_one():
    value = response()
    for row in value['factual_audit']['sentences']:
        row.update(assessment='uncertain', quotations=[])
    _, report, failures = audit.validate(value, LINES, PAGES)
    assert not report['accepted'] and len(failures) == len(LINES)


@pytest.mark.parametrize('change', ['none', 'reordered', 'missing', 'duplicated', 'changed_narration',
                                  'missing_narration', 'wrong_position', 'bool_position'])
def test_omitted_redundant_indexes_still_require_exact_complete_ordered_narration(change):
    actual = response()
    rows = actual['factual_audit']['sentences']
    for row in rows:
        row.pop('position')
    if change == 'reordered':rows[1], rows[2] = rows[2], rows[1]
    elif change == 'missing':rows.pop()
    elif change == 'duplicated':rows[1] = deepcopy(rows[0])
    elif change == 'changed_narration':rows[1]['narration'] += ' Another assertion.'
    elif change == 'missing_narration':rows[1].pop('narration')
    elif change == 'wrong_position':rows[1]['position'] = 0
    elif change == 'bool_position':rows[1]['position'] = True
    before = deepcopy(actual)
    if change == 'none':
        critic, report, failures = audit.validate(actual, LINES, PAGES)
        assert critic == {'positive': True} and report['accepted'] is False
        assert [row['position'] for row in failures] == [1, 2, 3]
        assert report['sentences'] == before['factual_audit']['sentences']
        assert all('position' not in row for row in report['sentences'])
    else:
        with pytest.raises(ValueError):audit.validate(actual, LINES, PAGES)
    assert actual == before


@pytest.mark.parametrize('verb', ['funded', 'financed', 'bankrolled', 'finanse etti', 'fonladı'])
def test_real_chronology_does_not_establish_a_financing_link_even_with_positive_model_verdict(verb):
    text = 'The cards produced a sales boom. Years later, the company created Mario.'
    pages = [{'url': URL, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
    lines = [{'position': 0, 'narration': f'The card boom {verb} the later gaming empire.'}]
    actual = {'editorial_review': {'causal_claim_supported': True}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': 'supported', 'reason': 'There is an operational progression.',
         'quotations': [{'source_url': URL, 'quote': quote} for quote in (
             'The cards produced a sales boom.', 'Years later, the company created Mario.')]}]}}
    before = deepcopy(actual)
    _, report, failures = audit.validate(actual, lines, pages)
    assert len(failures) == 1 and failures[0]['assessment'] == 'unsupported'
    assert 'financing' in failures[0]['reason']
    assert not report['accepted'] and report['version'] == audit.VERSION
    assert report['sentences'][0]['assessment'] == 'supported'  # actual response is preserved
    assert report['validation_findings'] == failures and actual == before


@pytest.mark.parametrize('claim', [
    'Connected registers pulled prices instantly, cutting lane transaction times forty percent.',
    'Checkout times were reduced by 40 percent.',
    'Customers spent 40% less time checking out.',
])
def test_speed_increase_cannot_become_the_same_percentage_time_reduction(claim):
    quote = 'Checkout lines were moving 40% faster.'
    lines = [{'position': 0, 'narration': claim}]
    pages = [{'url': URL, 'text': quote, 'text_sha256': hashlib.sha256(quote.encode()).hexdigest()}]
    actual = {'editorial_review': {'quantities_supported': True}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': 'supported', 'reason': 'The quoted metric must match the narration.',
         'quotations': [{'source_url': URL, 'quote': quote}]}]}}
    before = deepcopy(actual)
    _, report, failures = audit.validate(actual, lines, pages)
    assert not report['accepted'] and len(failures) == 1 and 'time reduction' in failures[0]['reason']
    assert report['sentences'][0]['assessment'] == 'supported' and actual == before
    assert report['validation_findings'] == failures


@pytest.mark.parametrize('assessment', ['supported', 'unsupported', 'uncertain'])
def test_explicit_time_measurement_still_requires_independent_semantic_verdict(assessment):
    quote = 'Measured checkout transaction times were reduced by 40 percent.'
    lines = [{'position': 0, 'narration': 'Checkout times were reduced by forty percent.'}]
    pages = [{'url': URL, 'text': quote, 'text_sha256': hashlib.sha256(quote.encode()).hexdigest()}]
    actual = {'editorial_review': {}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': assessment, 'reason': 'Assess the exact population and measurement.',
         'quotations': [{'source_url': URL, 'quote': quote}]}]}}
    _, report, failures = audit.validate(actual, lines, pages)
    assert report['accepted'] is (assessment == 'supported')
    assert bool(failures) is (assessment != 'supported')
    assert report['validation_findings'] == []


@pytest.mark.parametrize('assessment', ['supported', 'unsupported', 'uncertain'])
def test_an_explicit_financing_quote_never_overrides_the_semantic_verdict(assessment):
    text = 'The company reinvested its card profits to develop its first electronic game.'
    lines = [{'position': 0, 'narration': 'Card profits funded the first electronic game.'}]
    pages = [{'url': URL, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
    actual = {'editorial_review': {}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': assessment, 'reason': 'Compare the specific products and financing.',
         'quotations': [{'source_url': URL, 'quote': text}]}]}}
    _, report, failures = audit.validate(actual, lines, pages)
    assert report['accepted'] is (assessment == 'supported')
    assert bool(failures) is (assessment != 'supported')
    assert report['validation_findings'] == []


def test_unquoted_financing_words_or_the_reviewers_reason_cannot_satisfy_the_guard():
    text = 'The card division boomed. An unrelated company funded a different project.'
    lines = [{'position': 0, 'narration': 'The card boom financed Mario.'}]
    pages = [{'url': URL, 'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]
    actual = {'editorial_review': {}, 'factual_audit': {'sentences': [
        {**lines[0], 'assessment': 'supported', 'reason': 'The boom financed the later game.',
         'quotations': [{'source_url': URL, 'quote': 'The card division boomed.'}]}]}}
    _, report, failures = audit.validate(actual, lines, pages)
    assert not report['accepted'] and len(failures) == 1


def test_request_contains_every_clause_complete_original_contract_and_stable_source_identity():
    schema = {'type': 'object', 'properties': {'original': {'type': 'boolean'}}}
    before = deepcopy(schema)
    prompt, wrapped = audit.request('The complete original critic rubric.', schema, LINES, PAGES)
    assert audit.MARKER in prompt and 'The complete original critic rubric.' in prompt
    assert wrapped['properties']['editorial_review'] == schema == before
    assert all(line['narration'] in prompt for line in LINES)
    for phrase in ('labor', 'TOTAL', 'coin production', 'disruption', 'uncertain'):
        assert phrase in prompt
    changed = deepcopy(PAGES); changed[0]['retrieved_at'] = '2099-01-01'; changed[0]['body_sha256'] = 'a' * 64
    assert audit.request('The complete original critic rubric.', schema, LINES, changed) == (prompt, wrapped)
    assert json.dumps(wrapped)


def test_rejection_diagnostics_preserve_scene_reasons_but_do_not_echo_source_urls_or_keys():
    from app.services.planning_diagnostics import story_planning_error
    actual = response()
    actual['factual_audit']['sentences'][1]['reason'] = 'api_key=sk-this-is-private do not echo'
    error = story_planning_error('Source audit rejected unsupported narration before media',
        scenes=LINES, review=actual)
    details = error.planning_diagnostics
    assert details['candidate_kind'] == 'rejected_critic_candidate'
    assert details['publish_eligible'] is False
    rows = details['review']['factual_audit']['sentences']
    assert rows[2]['assessment'] == 'unsupported'
    assert rows[2]['reason'] == actual['factual_audit']['sentences'][2]['reason']
    assert rows[1]['reason'] == '[credential-bearing text omitted]'
    assert URL not in json.dumps(details) and 'sk-this' not in json.dumps(details)


@pytest.mark.parametrize('second_passes', [True, False])
def test_full_story_rewrite_gets_every_failure_and_stops_after_one_correction(monkeypatch, second_passes):
    from app.services import director
    from test_included_story_review import fixture_story
    package, _ = fixture_story()
    monkeypatch.setattr(director, '_studio_plan_provider', lambda: 'openai')
    monkeypatch.setattr(director.settings, 'openai_api_key', 'test-only', raising=False)
    monkeypatch.setattr(director, 'OpenAI', lambda **kwargs: object())
    plans, reviews = [], []
    failures = [{'position': i, 'assessment': 'unsupported', 'narration': line['narration'],
        'reason': 'The exact clause exceeds its evidence. ' * 20} for i, line in enumerate(LINES)]
    def plan(client, compact, *args, **kwargs):
        plans.append(deepcopy(compact))
        return {**deepcopy(package), 'qc_summary': []}
    def review(*args, **kwargs):
        reviews.append(kwargs)
        if len(reviews) == 1:
            error = director._WholeStoryRepairRequired(['causal_claim_supported'], json.dumps(failures))
            error.source_claim_failures = deepcopy(failures)
            raise error
        if not second_passes:
            raise RuntimeError('still unsupported')
        result = deepcopy(package)
        result['stock_scene_qc'] = {'version': director._STOCK_SCENE_QC_VERSION,
            'story_review': {'accepted': True}, 'ending_pair_review': {'accepted': True}}
        return result
    monkeypatch.setattr(director, '_run_director', plan)
    monkeypatch.setattr(director, '_repair_short_stock_scenes', review)
    def run():
        return director.direct_and_qc(package, 'Soğukta telefonun pili neden hızla düşer?', .5, 'tr',
            {'mode': 'preview', 'pace': 'balanced'})
    if second_passes:
        run()
    else:
        with pytest.raises(RuntimeError, match='still unsupported'):run()
    assert len(plans) == len(reviews) == 2
    assert plans[1]['source_claim_failures'] == failures
    assert len(json.dumps(plans[1]['source_claim_failures'])) > 480
    assert reviews[1].get('allow_whole_story_repair', False) is False
