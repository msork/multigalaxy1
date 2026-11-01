import unittest
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from context_dataset import RAW_SCHEMA, normalize_context_dataset


def raw_fixture():
    return {
        "schema": RAW_SCHEMA,
        "provenance": {"authority": "raw native .anm bank imported by TrackId"},
        "sample_time_policy": "exact keys",
        "rig": {
            "profile_bones": ["bone_22"],
            "rest_bones": {"bone_22": {
                "rest_matrix": np.eye(4).tolist(), "rest_head": [0.0, 0.0, 0.0],
                "rest_tail": [0.0, 1.0, 0.0]}},
        },
        "actions": [{
            "action": "Waluigi_7_Batting_seq0", "bank": "Waluigi_7_Batting",
            "bank_index": 7, "sequence_index": 0, "split": "train",
            "frame_range": [0.0, 1.0], "exact_key_times": [0.0, 1.0],
            "samples": [{
                "time": 0.0, "normalized_time": 0.0,
                "bones": {"bone_22": {
                    "rest_relative_se3": {
                        "translation": [1.0, 2.0, 3.0],
                        "rotation_quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                    "local_basis_matrix": np.eye(4).tolist(),
                }},
                "branch_landmarks": {}, "relative_pose_quaternions_wxyz": {},
                "weighted_mesh_centroids": {}, "arm_region_mesh_summary": {},
            }],
        }, {
            "action": "Waluigi_7_Batting_seq11", "bank": "Waluigi_7_Batting",
            "bank_index": 7, "sequence_index": 11, "split": "holdout",
            "frame_range": [0.0, 1.0], "exact_key_times": [0.0, 1.0],
            "samples": [],
        }],
    }


class ContextDatasetTests(unittest.TestCase):
    def test_raw_transform_normalizes_to_pose_matrix(self):
        data = normalize_context_dataset(raw_fixture())
        sample = data["actions"][0]["samples"][0]
        np.testing.assert_allclose(sample["bones"]["bone_22"]["armature_pose"],
                                   [[1, 0, 0, 1], [0, 1, 0, 2],
                                    [0, 0, 1, 3], [0, 0, 0, 1]])

    def test_only_raw_anm_provenance_can_train(self):
        old = raw_fixture()
        old["schema"] = "mg1.converted-orphan-actions.v1"
        with self.assertRaises(ValueError):
            normalize_context_dataset(old)

    def test_action_splits_are_preserved(self):
        data = normalize_context_dataset(raw_fixture())
        self.assertEqual(data["training_actions"], ["Waluigi_7_Batting_seq0"])
        self.assertEqual(data["holdout_actions"], ["Waluigi_7_Batting_seq11"])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
