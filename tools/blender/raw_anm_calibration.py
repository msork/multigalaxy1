"""Blender-independent policy and metadata helpers for raw Sluggies ANM data."""
import re


WALUIGI_SHARED_CONTEXT_BONES = ("bone_1", "bone_2", "bone_3", "bone_5",
                                "bone_6", "bone_19", "bone_20", "bone_21")
WALUIGI_LEFT_ARM_BONES = ("bone_22", "bone_23", "bone_24", "bone_25",
                          "bone_26", "bone_27", "bone_29", "bone_30")
WALUIGI_RIGHT_ARM_BONES = ("bone_57", "bone_58", "bone_59", "bone_60",
                           "bone_61", "bone_62", "bone_64", "bone_65")
WALUIGI_LEFT_HAND_CONTEXT_BONES = tuple(f"bone_{i}" for i in range(31, 53))
WALUIGI_RIGHT_HAND_CONTEXT_BONES = tuple(f"bone_{i}" for i in range(66, 87))
WALUIGI_CONTEXT_BONES = (WALUIGI_SHARED_CONTEXT_BONES + WALUIGI_LEFT_ARM_BONES +
    WALUIGI_RIGHT_ARM_BONES + WALUIGI_LEFT_HAND_CONTEXT_BONES +
    WALUIGI_RIGHT_HAND_CONTEXT_BONES)
WALUIGI_LEFT_ARM_TRACK_TO_BONE = {
    16: "bone_22", 17: "bone_25", 18: "bone_29", 19: "bone_30",
    111: "bone_26", 112: "bone_27", 115: "bone_23", 116: "bone_24",
}
WALUIGI_RIGHT_ARM_TRACK_TO_BONE = {
    22: "bone_57", 23: "bone_60", 24: "bone_64", 25: "bone_65",
    113: "bone_61", 114: "bone_62", 117: "bone_58", 118: "bone_59",
}
WALUIGI_RIGHT_HAND_TRACK_TO_BONE = {
    26: "bone_66", 94: "bone_67", 95: "bone_68", 38: "bone_69",
    96: "bone_70", 97: "bone_71", 98: "bone_72", 99: "bone_74",
    100: "bone_75", 101: "bone_76", 102: "bone_78", 103: "bone_79",
    104: "bone_80", 105: "bone_82", 106: "bone_83", 107: "bone_84",
    3: "bone_86",
}
WALUIGI_ARM_BONES = WALUIGI_LEFT_ARM_BONES + WALUIGI_RIGHT_ARM_BONES
WALUIGI_ARM_TRACK_TO_BONE = {**WALUIGI_LEFT_ARM_TRACK_TO_BONE,
                             **WALUIGI_RIGHT_ARM_TRACK_TO_BONE}
WALUIGI_LEFT_PROFILE = {
    "torso": "bone_21", "shoulder": "bone_23", "upper": "bone_23",
    "elbow_a": "bone_26", "elbow_b": "bone_29", "forearm": "bone_27",
    "forearm_overlap": "bone_29", "wrist": "bone_30", "hand": "bone_30",
    "shoulder_point_bone": "bone_23", "shoulder_endpoint": "head",
    "elbow_endpoint_a": "head", "elbow_endpoint_b": "head",
    "forearm_endpoint": "tail", "overlap_endpoint": "tail",
    "wrist_endpoint": "head", "hand_endpoint": "tail",
}
WALUIGI_RIGHT_PROFILE = {
    "torso": "bone_21", "shoulder": "bone_58", "upper": "bone_58",
    "elbow_a": "bone_61", "elbow_b": "bone_64", "forearm": "bone_62",
    "forearm_overlap": "bone_64", "wrist": "bone_65", "hand": "bone_65",
    "shoulder_point_bone": "bone_58", "shoulder_endpoint": "head",
    "elbow_endpoint_a": "head", "elbow_endpoint_b": "head",
    "forearm_endpoint": "tail", "overlap_endpoint": "tail",
    "wrist_endpoint": "head", "hand_endpoint": "tail",
}
WALUIGI_LEGACY_HOLDOUTS = frozenset((11, 15, 20, 24, 26))


def sequence_index(action_name):
    """Return the trailing sequence index from an imported ANM action name."""
    match = re.search(r"_seq(\d+)$", str(action_name))
    if not match:
        raise ValueError(f"not a Sluggies sequence action: {action_name!r}")
    return int(match.group(1))


def bank_index(bank_name):
    """Return the stock bank number in a name such as Waluigi_7_Batting."""
    match = re.search(r"_(\d+)(?:_|$)", str(bank_name))
    if not match:
        raise ValueError(f"cannot find bank number in {bank_name!r}")
    return int(match.group(1))


def split_for_action(bank, sequence, character="Waluigi"):
    """Stable complete-action split; preserve the old 23/5 Batting split."""
    bank = int(bank)
    sequence = int(sequence)
    if character.lower() == "waluigi" and bank == 7:
        return "holdout" if sequence in WALUIGI_LEGACY_HOLDOUTS else "train"
    # Stratify by bank while rotating the held-out residue across bank numbers.
    return "holdout" if (sequence + bank) % 5 == 0 else "train"


def validate_profile(bones, arm_bones):
    bones = tuple(bones)
    arm_bones = tuple(arm_bones)
    if len(bones) != len(set(bones)):
        raise ValueError("profile contains duplicate bone names")
    missing = sorted(set(arm_bones) - set(bones))
    if missing:
        raise ValueError("arm bones are absent from context profile: " + ", ".join(missing))
    return bones, arm_bones
