import unittest

from app.services.visual_identity import (
    manufactured_replica_guardrail,
    manufactured_replica_required,
)


class ManufacturedReplicaIdentityTests(unittest.TestCase):
    def test_lego_toy_scene_is_detected_from_authored_fields(self):
        scene = {
            'narration': 'Bu Lego ahtapotu kıyıda bulundu.',
            'ai_prompt': 'Orange plastic toy, no humans.',
            'visual_queries': ['weathered toy octopus on sand'],
        }

        self.assertTrue(manufactured_replica_required(scene))
        guardrail = manufactured_replica_guardrail(scene)
        self.assertIn('Require 2+ clear manufactured cues', guardrail)
        self.assertIn('no person, human or hand', guardrail)

    def test_real_animal_and_generic_plastic_are_not_false_positives(self):
        for scene in (
            {
                'narration': 'Ahtapot kayalıkların arasında yüzüyor.',
                'ai_prompt': 'Live octopus in a natural reef.',
            },
            {
                'narration': 'Dalgalar plastik çöpleri kıyıya taşıyor.',
                'visual_queries': ['plastic debris on shoreline'],
            },
        ):
            with self.subTest(scene=scene):
                self.assertFalse(manufactured_replica_required(scene))
                self.assertEqual(manufactured_replica_guardrail(scene), '')

    def test_negated_replica_identity_is_not_rewritten_as_a_toy(self):
        for narration in (
            'A real living octopus, not a toy or replica, crosses the reef.',
            'This is not a plastic toy.',
            'This is not really a toy; it is a living octopus.',
            'No humans and not a toy.',
            'Bu bir oyuncak değil, gerçek bir ahtapot.',
            'Bu bir oyuncak değildir; gerçek ahtapottur.',
            'Bu bir oyuncak değildi; canlı ahtapottu.',
            'Bu bir oyuncak olmadığı için gerçek hayvana benziyor.',
            'Bu oyuncak ahtapot değil.',
            'Bu oyuncak ya da replika değil.',
            'Oyuncak olmayan canlı ahtapot kayalıkta yüzüyor.',
        ):
            with self.subTest(narration=narration):
                scene = {'narration': narration}
                self.assertFalse(manufactured_replica_required(scene))
                self.assertEqual(manufactured_replica_guardrail(scene), '')

    def test_unrelated_words_do_not_trigger_replica_identity(self):
        for narration in (
            'Aile Legoland girişinde sıraya girdi.',
            'Ressam figüratif bir kompozisyon hazırladı.',
            'Replikasyon deneyi laboratuvarda sürüyor.',
            'Figüran sahneye çıktı.',
            'Oyuncakçı dükkânı açtı.',
            'Maketçi atölyede çalışıyor.',
            'Figüratiflik sanat akımında tartışıldı.',
        ):
            with self.subTest(narration=narration):
                self.assertFalse(manufactured_replica_required({
                    'narration': narration,
                }))

    def test_turkish_inflections_keep_explicit_replica_identity(self):
        for narration in (
            'Oyuncağı ıslak kumdan çıkardı.',
            'Figürü tepsiye bıraktı.',
            'Maketin küçük pervanesi dönüyor.',
            'Replikası cam kutuda sergileniyor.',
            'Minyatürü fırçayla temizliyor.',
            'Çocuk oyuncakla oynuyor.',
            'Oyuncağıyla sahilde oynuyor.',
            'Figürüyle poz veriyor.',
            'Maketiyle çalışıyor.',
            'Replikasıyla sahneye çıktı.',
            'Minyatürüyle kompozisyon kurdu.',
            'Figürle masada bir sahne kuruyor.',
            'Maketle çekim yapıyor.',
            'Replikayla müzede poz veriyor.',
            'Minyatürle ayrıntıyı gösteriyor.',
        ):
            with self.subTest(narration=narration):
                self.assertTrue(manufactured_replica_required({
                    'narration': narration,
                }))

    def test_guardrail_preserves_authored_soft_or_stylized_design(self):
        guardrail = manufactured_replica_guardrail({
            'narration': 'Peluş oyuncak ahtapotun büyük gözleri görünüyor.',
        })

        self.assertIn('woven/plush construction', guardrail)
        self.assertIn('Preserve authored face/limbs', guardrail)
        self.assertNotIn('no organic skin, eyes, anatomy or suckers', guardrail)
