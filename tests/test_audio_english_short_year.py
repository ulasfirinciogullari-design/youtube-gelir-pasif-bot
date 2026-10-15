"""Actual Margin wording: spelling equivalence without inventing a century."""
from copy import deepcopy
import pytest
from app.services import audio_qc as qc

EXPECTED = ('In the nineteen fifties, Nintendo was primarily a traditional Japanese cardmaker. '
    'Their existing card games rarely appealed to younger children and households. '
    'To reach modern families, Nintendo licensed famous Disney characters in fifty-nine. '
    'Featuring beloved animated figures turned simple cards into exciting nursery toys. '
    "The resulting sales boom rapidly unlocked an entirely new children's category. "
    "That smart deal established Nintendo's lasting foundation in modern family entertainment.")


def test_real_spoken_narration_compares_without_resynthesizing_or_changing_words():
    heard=EXPECTED.replace('nineteen fifties','1950s').replace('cardmaker','card maker').replace('fifty-nine','59')
    result=qc.compare_transcript(EXPECTED,heard,comparison_language='en')
    assert result['pass'] is True and result['score']==100
    assert 'cardmaker' in EXPECTED and 'fifty-nine' in EXPECTED


@pytest.mark.parametrize('spoken,heard', [('in fifty-nine','in 59'),('since ninety','since 90'),
    ('during twenty-one','during 21'),('before ten','before 10'),('after eighty five','after 85'),
    ('in card-maker workshops','in cardmaker workshops'),('the card makers worked','the cardmakers worked')])
def test_exact_contextual_spelling_works_in_both_directions(spoken,heard):
    assert qc.compare_transcript(spoken,heard,comparison_language='en')['pass'] is True
    assert qc.compare_transcript(heard,spoken,comparison_language='en')['pass'] is True


@pytest.mark.parametrize('spoken,heard', [('in fifty-nine','in 1959'),('in fifty-nine','in 58'),
    ('in fifty-nine percent','in 59 percent'),('fifty-nine','59'),
    ('in fifty nine ten','in 69'),('in fifty, nine','in 59'),('in fifty/nine','in 59'),
    ('in fifty. Nine','in 59'),('in -fifty-nine','in 59'),('in fifty nine','in +59'),
    ('in fifty nine','in 059'),('cardmaker','cart maker'),('card maker','card marker'),
    ('card, maker','cardmaker'),('card/maker','cardmaker')])
def test_changed_values_ambiguous_runs_or_word_boundaries_remain_failures(spoken,heard):
    assert qc.compare_transcript(spoken,heard,comparison_language='en')['pass'] is False


def test_transcript_equivalence_does_not_fabricate_word_timing():
    words=[{'word':'In','start':0.0,'end':0.2},{'word':'59','start':0.3,'end':0.7}]
    before=deepcopy(words)
    result=qc.compare_transcript('In fifty-nine','In 59',words=words,provider='openai',comparison_language='en')
    assert result['pass'] is True and words==before
    broken=deepcopy(words);broken[1]['word']='fifty-nine'
    result=qc.compare_transcript('In fifty-nine','In 59',words=broken,provider='openai',comparison_language='en')
    with pytest.raises(qc.AudioQCError):qc._require_word_timing_evidence(result,'OpenAI')
