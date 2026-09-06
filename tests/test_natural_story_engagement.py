"""Future-only honest engagement guidance, shared by writers and critic."""
import ast
from pathlib import Path

from app.services import director


def test_engagement_follows_the_answer_and_offers_a_relevant_follow_reason():
    rule = director._HUMAN_CURIOSITY_RULE
    assert 'after delivering the complete answer' in rule
    assert 'brief, honest question tied to this story' in rule
    assert 'when it fits naturally' in rule
    assert 'subscribe for a concrete, relevant kind of future story' in rule
    assert 'Keep the established visual subject' in rule
    assert 'do not add a separate CTA scene' in rule


def test_engagement_never_overrides_locked_text_budget_or_viewer_choice():
    rule = director._HUMAN_CURIOSITY_RULE
    for guard in ('Following is optional', 'never withhold the answer', 'demand engagement',
                  'promise rewards or guaranteed outcomes', 'invent a promised sequel',
                  'existing word budget', 'natural, breath-friendly pace',
                  'never speed up narration', 'not a new acceptance gate',
                  'Never alter exact locked or archived narration'):
        assert guard in rule


def test_existing_writer_and_critic_prompts_share_the_same_rule():
    folder = Path(director.__file__).parent
    for filename, minimum in (('research.py', 1), ('director.py', 2)):
        tree = ast.parse((folder / filename).read_text(encoding='utf-8'))
        uses = [node for node in ast.walk(tree) if isinstance(node, ast.Name)
                and node.id == '_HUMAN_CURIOSITY_RULE' and isinstance(node.ctx, ast.Load)]
        assert len(uses) >= minimum
