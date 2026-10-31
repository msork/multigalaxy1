import unittest

from client import validate_prototype_report


class PrototypeReportTests(unittest.TestCase):
    def test_preexisting_shoulder_gap_uses_matching_baseline(self):
        report = {"before": {"sample_frames": [
            {"frame": 0, "joint_gaps_world": {"shoulder": 6.1, "elbow": 0.0}},
            {"frame": 1, "joint_gaps_world": {"shoulder": 6.2, "elbow": 0.0}}]},
            "after": {"sample_frames": [
                {"frame": 0, "joint_gaps_world": {"shoulder": 6.1, "elbow": 0.0}},
                {"frame": 1, "joint_gaps_world": {"shoulder": 6.21, "elbow": 0.001}}]},
            "validation": {}}
        validate_prototype_report(report)
        self.assertTrue(report["validation"]["joint_continuity_within_tolerance"])
        self.assertAlmostEqual(report["validation"]["shoulder_continuity_baseline_gap_world"], 6.2)
        self.assertAlmostEqual(report["validation"]["max_joint_gap_change_world"]["elbow"], 0.001)


if __name__ == "__main__":
    unittest.main()
