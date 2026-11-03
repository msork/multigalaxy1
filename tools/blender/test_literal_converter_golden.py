"""Compare a profile-driven converter report against the approved V2 walk."""
import argparse
import json
import math
import sys


def args_cli():
    argv = sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--golden", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--output")
    p.add_argument("--tolerance", type=float, default=1e-7)
    return p.parse_args(argv)


def max_matrix_error(a, b):
    return max(abs(float(a[r][c]) - float(b[r][c]))
               for r in range(4) for c in range(4))


def main():
    args = args_cli()
    with open(args.golden, "r", encoding="utf-8") as stream:
        golden = json.load(stream)
    with open(args.candidate, "r", encoding="utf-8") as stream:
        candidate = json.load(stream)
    ga, ca = golden["expected_pose_basis"], candidate["expected_pose_basis"]
    golden_frames, candidate_frames = set(ga), set(ca)
    missing_frames = sorted(golden_frames - candidate_frames, key=float)
    extra_frames = sorted(candidate_frames - golden_frames, key=float)
    missing_channels, worst = [], {"error": 0.0, "frame": None, "bone": None}
    for frame in sorted(golden_frames & candidate_frames, key=float):
        for bone, expected in ga[frame].items():
            actual = ca[frame].get(bone)
            if actual is None:
                missing_channels.append({"frame": float(frame), "bone": bone})
                continue
            error = max_matrix_error(expected, actual)
            if error > worst["error"]:
                worst = {"error": error, "frame": float(frame), "bone": bone}
    location_error = 0.0
    location_missing = []
    expected_locations = golden.get("expected_object_location", {})
    actual_locations = candidate.get("expected_object_location", {})
    for frame, expected in expected_locations.items():
        actual = actual_locations.get(frame)
        if actual is None:
            location_missing.append(frame)
            continue
        location_error = max(location_error,
            max(abs(float(a)-float(b)) for a,b in zip(expected, actual)))
    result = {
        "schema": "mg1.literal-converter-golden-regression.v1",
        "golden": args.golden,
        "candidate": args.candidate,
        "golden_samples": len(ga),
        "candidate_samples": len(ca),
        "max_pose_matrix_error": worst["error"],
        "worst_pose": worst,
        "max_object_location_error": location_error,
        "missing_frames": missing_frames,
        "extra_frames": extra_frames,
        "missing_pose_channels": missing_channels,
        "missing_object_locations": location_missing,
        "tolerance": args.tolerance,
        "passed": (not missing_frames and not extra_frames and not missing_channels and
                   not location_missing and worst["error"] <= args.tolerance and
                   location_error <= args.tolerance),
    }
    rendered = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(rendered + "\n")
    print(rendered)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
