import unittest

from app.services.visual_routing import (
    SHORT_PREVIEW_AI_FIRST_RUNWAY_CAP,
    SHORT_PREVIEW_RUNWAY_CAP,
    SHORT_PREVIEW_RUNWAY_REPAIR_CAP,
    SHORT_PREVIEW_TOTAL_PAID_CREATE_CAP,
    preview_authored_ai_limit,
    preview_paid_ai_limit,
    preview_runway_repair_indices,
    preview_total_paid_create_cap,
    should_rank_runway_candidate,
)


class ShortPreviewVisualRoutingTests(unittest.TestCase):
    def test_production_shorts_authorship_matches_total_paid_cap_for_every_mix(self):
        for mix in ('real_first', 'balanced', 'ai_first'):
            options = {'mode': 'production', 'format': 'shorts', 'visual_mix': mix}
            with self.subTest(mix=mix):
                self.assertEqual(preview_authored_ai_limit(options, 5, 0.5), 2)
                self.assertEqual(preview_paid_ai_limit(options, 5, 0.5), 2)
                self.assertEqual(preview_total_paid_create_cap(options, 0.5), 2)
                self.assertEqual(preview_authored_ai_limit(options, 1, 0.5), 1)
                self.assertIsNone(preview_authored_ai_limit(options, 5, 1.0))
                self.assertIsNone(preview_paid_ai_limit(options, 5, 1.0))
        self.assertIsNone(preview_authored_ai_limit(
            {'mode': 'production', 'format': 'landscape'}, 5, 0.5,
        ))

    def test_ai_first_preview_is_bounded_to_six_authored_ai(self):
        self.assertEqual(SHORT_PREVIEW_RUNWAY_CAP, 4)
        self.assertEqual(SHORT_PREVIEW_AI_FIRST_RUNWAY_CAP, 6)
        self.assertEqual(
            preview_authored_ai_limit(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                8,
                0.5,
            ),
            6,
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

    def test_exact_thirty_second_paid_preview_limit_is_two_for_every_mix(self):
        self.assertEqual(SHORT_PREVIEW_TOTAL_PAID_CREATE_CAP, 2)
        self.assertEqual(
            preview_paid_ai_limit(
                {'mode': 'preview', 'visual_mix': 'real_first'},
                7,
                0.5,
            ),
            1,
        )
        self.assertEqual(
            preview_paid_ai_limit(
                {'mode': 'preview', 'visual_mix': 'balanced'},
                7,
                0.5,
            ),
            2,
        )
        self.assertEqual(
            preview_paid_ai_limit(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                7,
                0.5,
            ),
            2,
        )
        self.assertEqual(
            preview_total_paid_create_cap({'mode': 'preview'}, 0.5),
            2,
        )
        self.assertIsNone(
            preview_paid_ai_limit(
                {'mode': 'production', 'visual_mix': 'ai_first'},
                7,
                0.5,
            )
        )
        self.assertIsNone(
            preview_total_paid_create_cap({'mode': 'production'}, 0.5)
        )
        self.assertIsNone(
            preview_total_paid_create_cap({'mode': 'preview'}, 0.55)
        )

    def test_non_exact_short_preview_keeps_existing_primary_limits(self):
        expected = {
            'real_first': 1,
            'balanced': SHORT_PREVIEW_RUNWAY_CAP,
            'ai_first': SHORT_PREVIEW_AI_FIRST_RUNWAY_CAP,
        }
        for visual_mix, cap in expected.items():
            with self.subTest(visual_mix=visual_mix):
                self.assertEqual(
                    preview_paid_ai_limit(
                        {'mode': 'preview', 'visual_mix': visual_mix},
                        7,
                        0.55,
                    ),
                    cap,
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

    def test_final_ai_repairs_are_evidence_led_and_bounded(self):
        scenes = [
            {'ai_prompt': 'scene zero'},
            {'ai_prompt': None},
            {'ai_prompt': 'scene two'},
            {'ai_prompt': 'scene three'},
        ]
        self.assertEqual(SHORT_PREVIEW_RUNWAY_REPAIR_CAP, 2)
        self.assertEqual(
            preview_runway_repair_indices(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                0.5,
                [0, 1, 2, 3],
                scenes,
                {0, 2, 3},
                {
                    0: {'score': 40},
                    1: {'score': 20},
                    2: {'score': 30},
                    3: {'score': 50},
                },
            ),
            [2, 0],
        )

    def test_exact_stock_revalidation_failure_gets_priority_in_same_cap(self):
        scenes = [
            {'ai_prompt': None},
            {'ai_prompt': 'authored scene one'},
            {'ai_prompt': 'authored scene two'},
        ]
        self.assertEqual(
            preview_runway_repair_indices(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                0.5,
                [0, 1, 2],
                scenes,
                {1, 2},
                {
                    0: {'score': 38},
                    1: {'score': 20},
                    2: {'score': 10},
                },
                exact_revalidation_scene_indices={0},
            ),
            [0, 2],
        )

    def test_three_exact_stock_failures_still_use_only_two_repairs(self):
        scenes = [
            {'ai_prompt': None},
            {'ai_prompt': None},
            {'ai_prompt': None},
        ]
        self.assertEqual(
            preview_runway_repair_indices(
                {'mode': 'preview', 'visual_mix': 'ai_first'},
                0.5,
                [0, 1, 2],
                scenes,
                set(),
                {
                    0: {'score': 35},
                    1: {'score': 25},
                    2: {'score': 15},
                },
                exact_revalidation_scene_indices={0, 1, 2},
            ),
            [2, 1],
        )

    def test_exact_preview_repairs_share_remaining_job_wide_paid_slots(self):
        scenes = [
            {'ai_prompt': 'scene zero'},
            {'ai_prompt': 'scene one'},
            {'ai_prompt': 'scene two'},
        ]
        reviews = {
            0: {'score': 30},
            1: {'score': 20},
            2: {'score': 10},
        }
        for attempts, expected in ((0, [2, 1]), (1, [2]), (2, [])):
            with self.subTest(attempts=attempts):
                self.assertEqual(
                    preview_runway_repair_indices(
                        {'mode': 'preview', 'visual_mix': 'ai_first'},
                        0.5,
                        [0, 1, 2],
                        scenes,
                        {0, 1, 2},
                        reviews,
                        paid_create_attempts=attempts,
                    ),
                    expected,
                )

    def test_repairs_never_expand_beyond_short_preview_or_paid_submissions(self):
        scenes = [{'ai_prompt': 'scene zero'}, {'ai_prompt': 'scene one'}]
        self.assertEqual(
            preview_runway_repair_indices(
                {'mode': 'preview'},
                0.5,
                [0, 1],
                scenes,
                {1},
                {0: {'score': 20}, 1: {'score': 40}},
            ),
            [1],
        )
        self.assertEqual(
            preview_runway_repair_indices(
                {'mode': 'production'},
                0.5,
                [0, 1],
                scenes,
                {0, 1},
                {0: {'score': 20}, 1: {'score': 40}},
            ),
            [],
        )

    def test_repair_requires_a_real_final_review_and_successful_base_clip(self):
        scenes = [{'ai_prompt': 'scene zero'}, {'ai_prompt': 'scene one'}]
        self.assertEqual(
            preview_runway_repair_indices(
                {'mode': 'preview'},
                0.5,
                [0, 1],
                scenes,
                {0},
                {1: {'score': 10}},
            ),
            [],
        )


if __name__ == '__main__':
    unittest.main()

