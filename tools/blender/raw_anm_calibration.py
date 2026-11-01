"""Blender-independent policy and metadata helpers for raw Sluggies ANM data."""
import re


WALUIGI_CONTEXT_BONES = tuple(
    ["bone_1", "bone_2", "bone_3", "bone_5", "bone_6", "bone_19", "bone_20", "bone_21"]
    + ["bone_22", "bone_23", "bone_24", "bone_25", "bone_26", "bone_27", "bone_29", "bone_30"]
    + [f"bone_{i}" for i in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42,
                              43, 44, 45, 46, 47, 48, 49, 50, 51, 52)]
)
WALUIGI_ARM_BONES = ("bone_22", "bone_23", "bone_24", "bone_25",
                     "bone_26", "bone_27", "bone_29", "bone_30")
WALUIGI_ARM_TRACK_TO_BONE = {
    16: "bone_22", 17: "bone_25", 18: "bone_29", 19: "bone_30",
    111: "bone_26", 112: "bone_27", 115: "bone_23", 116: "bone_24",
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
