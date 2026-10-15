import pytest

from app.services.visual_qc import _thermal_claim_required


@pytest.mark.parametrize('narration', [
    'Why would a furniture store serve hot meatballs?',
    'Hot meals encouraged customers to stay and shop.',
    'They offered visitors warm coffee and pastries.',
    'Hot dogs attracted more shoppers to the cafeteria.',
    'The founder offered a warm welcome to customers.',
    'The product became a hot seller.',
    'Sıcak yemek müşterileri mağazada daha uzun tuttu.',
    'Ziyaretçilere sıcak kahve ve köfte sunuldu.',
])
def test_serving_descriptions_do_not_demand_infrared_measurement(narration):
    scene = {'narration': narration}
    assert _thermal_claim_required(scene, [scene]) is False


@pytest.mark.parametrize('narration', [
    'Hot meals release heat into the air.',
    'A thermal camera measures the temperature of hot food.',
    'The insulated container traps the heat of hot meals.',
    'The battery produces heat while charging.',
    'The phone gets warmer during charging.',
    'Sıcak yemek ısıyı havaya verir.',
    'Termal kamera sıcak kahvenin sıcaklık dağılımını gösterir.',
    'Batarya şarj olurken ısı üretir.',
])
def test_actual_heat_claims_keep_their_required_proof(narration):
    scene = {'narration': narration}
    assert _thermal_claim_required(scene, [scene]) is True
