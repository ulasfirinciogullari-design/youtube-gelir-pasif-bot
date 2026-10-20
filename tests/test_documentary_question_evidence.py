import pytest

from app.services.visual_qc import _state_change_required


@pytest.mark.parametrize('narration', [
    'Büyük teslimat kamyonları dar sokaklardan neden kayboluyor?',
    'Why are large delivery vans disappearing from city centres?',
    'Why did cash disappear from these shops?',
    'How did the company remove this bottleneck?',
    'Grafit gerçekten kayboluyor mu?',
    'Does the eraser completely remove the drawing?',
])
def test_explanatory_question_does_not_promise_visible_disappearance(narration):
    assert _state_change_required({'narration': narration}) is False


@pytest.mark.parametrize('narration', [
    'The eraser removes the line. Why are the vans disappearing?',
    'Why did the old process disappear? The cleaner removes the stain.',
    'It erases a line; why is the van disappearing?',
    'Pembe silgi çizgiyi siliyor. Kamyonlar neden kayboluyor?',
    'Grafit kayboluyor mu? Silgi kenardaki lekeyi temizler.',
    'The eraser removes a line, does that explain the process?',
    'The large van disappears after passing behind a building.',
])
def test_affirmative_removal_keeps_its_before_action_after_contract(narration):
    assert _state_change_required({'narration': narration}) is True
