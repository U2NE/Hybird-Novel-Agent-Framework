from __future__ import annotations

import unittest

from novel_agent_framework.models import StoryDelta
from novel_agent_framework.state_tx import compare_deltas


class StateTransactionTests(unittest.TestCase):
    def test_three_way_mismatch_blocks(self) -> None:
        expected = StoryDelta.from_dict(
            {"changes": [{"kind": "fact", "entity_key": "x", "field": "v", "new_value": 1}]}
        )
        declared = StoryDelta.from_dict(
            {"changes": [{"kind": "fact", "entity_key": "x", "field": "v", "new_value": 1}]}
        )
        observed = StoryDelta.from_dict(
            {"changes": [{"kind": "fact", "entity_key": "x", "field": "v", "new_value": 2}]}
        )
        result = compare_deltas(expected, declared, observed)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["mismatches"]), 1)

    def test_duplicate_delta_identity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StoryDelta.from_dict(
                {
                    "changes": [
                        {"kind": "fact", "entity_key": "x", "field": "v", "new_value": 1},
                        {"kind": "fact", "entity_key": "x", "field": "v", "new_value": 2},
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
