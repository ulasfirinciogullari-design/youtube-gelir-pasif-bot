import unittest

from app.services.visual_routing import (
    SHORT_PREVIEW_RUNWAY_CAP,
    preview_authored_ai_limit,
    should_rank_runway_candidate,
)


class ShortPreviewVisualRoutingTests(unittest.TestCase):
    def test_ai_first_five_scene_preview_is_bounded_to_three_authored_ai(self):
        self.assertEqual(SHORT_PREVIEW_RUNWAY_CAP, 3)
        self.assertEqual(
            preview_authored_ai_limit(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                5,
                0.5,
            ),
            3,
        )

    def test_balanced_and_real_first_preview_limits_are_unchanged(self):
        for visual_mix in ('balanced', 'real_first'):
            with self.subTest(visual_mix=visual_mix):
                self.assertEqual(
                    preview_authored_ai_limit(
                        {'mode': 'preview', 'visual_mix': visual_mix},
                        5,
                        0.5,
                    ),
                    5,
                )

    def test_ai_first_authored_scene_is_ranked_even_with_approved_stock(self):
        common = {
            'duration_minutes': 0.5,
            'authored_ai_prompt': True,
            'has_visual': True,
            'stock_score': 92,
            'quality_threshold': 86,
        }
        self.assertTrue(
            should_rank_runway_candidate(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                **common,
            )
        )
        for visual_mix in ('balanced', 'real_first'):
            with self.subTest(visual_mix=visual_mix):
                self.assertFalse(
                    should_rank_runway_candidate(
                        {'mode': 'preview', 'visual_mix': visual_mix},
                        **common,
                    )
                )

    def test_quality_fallback_remains_for_every_mix(self):
        for visual_mix in ('ai_first', 'balanced', 'real_first'):
            with self.subTest(visual_mix=visual_mix):
                self.assertTrue(
                    should_rank_runway_candidate(
                        {'mode': 'preview', 'visual_mix': visual_mix},
                        0.5,
                        authored_ai_prompt=visual_mix == 'ai_first',
                        has_visual=True,
                        stock_score=72,
                        quality_threshold=86,
                    )
                )


if __name__ == '__main__':
    unittest.main()
