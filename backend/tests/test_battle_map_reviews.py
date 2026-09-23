from unittest import TestCase

from sales_backend.services.battle_map_reviews import quadrant_code


class BattleMapReviewTests(TestCase):
    def test_quadrant_thresholds_are_deterministic(self) -> None:
        self.assertEqual(quadrant_code(70, 69.9), "main_attack")
        self.assertEqual(quadrant_code(70, 70), "customer_asset")
        self.assertEqual(quadrant_code(69.9, 69.9), "order_driven")
        self.assertEqual(quadrant_code(69.9, 70), "customer_resource")
