"""Pure Python tests for native arm action labels and sign-invariant metrics."""
import math
import unittest

from label_native_arm_candidates import summarize


def qz(degrees):
    half = math.radians(degrees) / 2
    return [math.cos(half), 0.0, 0.0, math.sin(half)]


class CandidateLabelTests(unittest.TestCase):
    def make_report(self):
        names = ["bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"]
        samples = []
        for angle in (0.0, 55.0, 0.0):
            bones = {name: {"rest_relative_se3": {"rotation_quaternion": qz(angle if name in ("bone_22", "bone_23", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30") else 0.0)},
                            "armature_pose_rotation_quaternion": qz(angle if name in ("bone_23", "bone_25", "bone_26", "bone_29") else 0.0)} for name in names}
            samples.append({"bones": bones})
        return {"bones": names, "actions": [{"action": "synthetic", "split": "train",
            "provenance_confidence": "high_family_inference", "training_eligible": True,
            "samples": samples, "sample_times": [0.0, 4.0, 9.0]}]}

    def test_threshold_labels_and_action_holdout_field(self):
        report = summarize(self.make_report())
        row = report["candidates"][0]
        self.assertEqual(row["cluster"], "combined")
        self.assertIn("shoulder_raise_or_swing_candidate", row["heuristic_labels"])
        self.assertIn("cyclic_pose_return_candidate", row["heuristic_labels"])

    def test_quaternion_sign_equivalence_does_not_change_displacement(self):
        report = self.make_report()
        report["actions"][0]["samples"][1]["bones"]["bone_22"]["rest_relative_se3"]["rotation_quaternion"] = [
            -x for x in qz(55.0)]
        row = summarize(report)["candidates"][0]
        self.assertAlmostEqual(row["maximum_displacement_degrees"]["bone_22"], 55.0, places=7)


if __name__ == "__main__":
    unittest.main()
