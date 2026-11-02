import unittest

from raw_anm_calibration import (
    WALUIGI_ARM_BONES, WALUIGI_ARM_TRACK_TO_BONE, WALUIGI_CONTEXT_BONES,
    WALUIGI_LEFT_ARM_BONES, WALUIGI_RIGHT_ARM_BONES,
    WALUIGI_RIGHT_ARM_TRACK_TO_BONE, WALUIGI_SHARED_CONTEXT_BONES,
    WALUIGI_RIGHT_HAND_TRACK_TO_BONE,
    bank_index, sequence_index,
    split_for_action, validate_profile,
)


class RawAnmCalibrationTests(unittest.TestCase):
    def test_waluigi_context_profile(self):
        bones, arms = validate_profile(WALUIGI_CONTEXT_BONES, WALUIGI_ARM_BONES)
        self.assertEqual(len(bones), 67)
        self.assertEqual(len(arms), 16)
        self.assertEqual(len(WALUIGI_SHARED_CONTEXT_BONES), 8)
        self.assertTrue(set(WALUIGI_LEFT_ARM_BONES).issubset(bones))
        self.assertTrue(set(WALUIGI_RIGHT_ARM_BONES).issubset(bones))

    def test_waluigi_arm_track_map(self):
        self.assertEqual(set(WALUIGI_ARM_TRACK_TO_BONE.values()), set(WALUIGI_ARM_BONES))
        self.assertEqual(len(WALUIGI_ARM_TRACK_TO_BONE), 16)
        self.assertEqual(WALUIGI_RIGHT_ARM_TRACK_TO_BONE, {
            22: "bone_57", 23: "bone_60", 24: "bone_64", 25: "bone_65",
            113: "bone_61", 114: "bone_62", 117: "bone_58", 118: "bone_59",
        })
        self.assertEqual(set(WALUIGI_RIGHT_HAND_TRACK_TO_BONE.values()), {
            "bone_66", "bone_67", "bone_68", "bone_69", "bone_70", "bone_71",
            "bone_72", "bone_74", "bone_75", "bone_76", "bone_78", "bone_79",
            "bone_80", "bone_82", "bone_83", "bone_84", "bone_86",
        })

    def test_bank_and_sequence_metadata(self):
        self.assertEqual(bank_index("Waluigi_7_Batting"), 7)
        self.assertEqual(sequence_index("Waluigi_7_Batting_seq10"), 10)

    def test_legacy_batting_split_is_preserved(self):
        holdouts = [i for i in range(28) if split_for_action(7, i) == "holdout"]
        self.assertEqual(holdouts, [11, 15, 20, 24, 26])
        self.assertEqual(28 - len(holdouts), 23)

    def test_other_banks_have_stable_whole_action_splits(self):
        first = [split_for_action(6, i) for i in range(44)]
        second = [split_for_action(6, i) for i in range(44)]
        self.assertEqual(first, second)
        self.assertEqual(first.count("holdout"), 8)

    def test_bad_action_names_fail_closed(self):
        with self.assertRaises(ValueError):
            sequence_index("sequence_10.003")


if __name__ == "__main__":
    unittest.main()
