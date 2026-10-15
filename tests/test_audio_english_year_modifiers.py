import pytest
from app.services import audio_qc as qc


@pytest.mark.parametrize('expected,heard',[
    ('In late nineteen forty-seven, the machine arrived.','In late 1947, the machine arrived.'),
    ('Throughout nineteen forty-eight, experiments continued.','Throughout 1948, experiments continued.'),
    ('During early nineteen fifty-nine, sales grew.','During early 1959, sales grew.'),
    ('Since mid nineteen sixty-one, production continued.','Since mid 1961, production continued.'),
])
def test_complete_temporal_year_keeps_its_modifier(expected,heard):
    assert qc.compare_transcript(expected,heard,comparison_language='en')['pass']is True
    assert qc.compare_transcript(heard,expected,comparison_language='en')['pass']is True


@pytest.mark.parametrize('expected,heard',[
    ('In late nineteen forty-seven.','In late 1948.'),
    ('In late nineteen forty-seven.','In 1947.'),
    ('In late nineteen forty-seven.','In early 1947.'),
    ('In late nineteen, forty-seven.','In late 1947.'),
    ('In late, nineteen forty-seven.','In late, 1947.'),
    ('In late nineteen forty-seven dollars.','In late 1947 dollars.'),
    ('The late nineteen forty-seven machines.','The late 1947 machines.'),
    ('Throughout nineteen forty-eight, tests continued.','Throughout 1948, tests stopped.'),
])
def test_wrong_year_or_meaning_cannot_be_normalized_away(expected,heard):
    assert qc.compare_transcript(expected,heard,comparison_language='en')['pass']is False
