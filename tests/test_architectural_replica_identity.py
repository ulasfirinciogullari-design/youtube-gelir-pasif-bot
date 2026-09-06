"""Real architectural copies must not become toy prompts or toy-only QC."""
import pytest

from app.services.visual_identity import (
    manufactured_replica_guardrail,
    manufactured_replica_required,
)


EURO_SCENES = (
    {
        'narration': 'Spijkenisse kenti, yedi köprüyü birebir renkleriyle kanallarına inşa etti.',
        'ai_prompt': ('An establishing medium shot of the real-world colorful 10-euro bridge replica '
                      'in Spijkenisse, Netherlands, spanning a quiet residential canal on an '
                      'overcast day, pedestrians walking across, crisp photorealistic.'),
        'visual_queries': ['Spijkenisse Netherlands euro bridge real canal view',
                           'Spijkenisse euro bridges architecture pedestrian walking'],
    },
    {
        'narration': 'Böylece hayali köprüler sonunda gerçek bir şehre kavuştu.',
        'ai_prompt': ('A continuous shot of a hand holding an authentic 50-euro banknote in front '
                      'of the matching bright orange 50-euro bridge replica spanning a canal '
                      'in Spijkenisse, Netherlands, creating a clear side-by-side visual '
                      'comparison, cinematic documentary style.'),
        'visual_queries': ['holding 50 euro note in front of matching Spijkenisse bridge',
                           'Spijkenisse Eurobruggen 50 euro bridge replica canal water'],
    },
)


@pytest.mark.parametrize('scene', EURO_SCENES)
def test_actual_euro_bridge_identity_does_not_demand_plastic_or_toy_cues(scene):
    assert manufactured_replica_required(scene) is False
    assert manufactured_replica_guardrail(scene) == ''


@pytest.mark.parametrize('text', [
    'Pedestrians cross a full-sized bridge replica over a canal.',
    'People enter a replica of the building.',
    'The replica bridge spans the river.',
    'A modern replica of a castle stands on the hill.',
    'The original house and its replica house are shown separately.',
    'Köprü replikası kanalın üzerinden geçiyor.',
    'Köprünün replikasında insanlar yürüyor.',
    'Binanın replikası ziyaretçilere açıldı.',
    'Replika köprü kanalın iki yakasını birleştiriyor.',
    'A real bridge replica, not a toy or miniature, spans the canal.',
])
@pytest.mark.parametrize('field', ['narration', 'ai_prompt', 'visual_queries'])
def test_architectural_noun_phrase_does_not_impose_a_toy_identity(text, field):
    scene = {field: [text] if field == 'visual_queries' else text}
    assert manufactured_replica_required(scene) is False
    assert manufactured_replica_guardrail(scene) == ''


@pytest.mark.parametrize('text', [
    'A plastic toy bridge replica sits on the table.',
    'A scale model of a bridge replica sits in a glass cabinet.',
    'A miniature replica of the building stands on a desk.',
    'A Lego replica bridge spans the toy river.',
    'A doll stands beside the full-sized bridge replica.',
    'Oyuncak köprü replikası masada duruyor.',
    'Köprü replikasının minyatürü vitrinde duruyor.',
    'A replica of the bridge stands beside a plastic octopus replica.',
])
def test_explicit_toy_or_other_replica_in_same_scene_is_still_required(text):
    scene = {'ai_prompt': text}
    assert manufactured_replica_required(scene) is True
    assert 'Require 2+ clear manufactured cues' in manufactured_replica_guardrail(scene)


def test_architecture_in_one_field_cannot_hide_toy_identity_in_another():
    scene = {'narration': 'A bridge replica spans the canal.',
             'ai_prompt': 'A detailed miniature, no humans.',
             'visual_queries': ['bridge replica canal water']}
    assert manufactured_replica_required(scene) is True
    assert 'Authored exclusion: no person, human or hand.' in manufactured_replica_guardrail(scene)


@pytest.mark.parametrize('text', [
    'An octopus replica lies on wet sand.',
    'A replica is displayed in a glass case.',
    'Replikası cam kutuda sergileniyor.',
    'Replikayla müzede poz veriyor.',
])
def test_unqualified_existing_replica_contract_is_not_relaxed(text):
    assert manufactured_replica_required({'narration': text}) is True


def test_no_authenticity_or_quality_approval_is_added_to_scene():
    scene = dict(EURO_SCENES[0])
    original = dict(scene)
    manufactured_replica_required(scene)
    manufactured_replica_guardrail(scene)
    assert scene == original
    assert not {'qa_approved', 'authentic', 'score', 'pass'}.intersection(scene)
