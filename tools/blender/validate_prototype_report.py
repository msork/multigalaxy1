"""Recompute before/after continuity validation in a saved prototype report."""
import argparse
import json
from pathlib import Path

from client import validate_prototype_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    validate_prototype_report(report)
    args.report.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), "validation": report["validation"],
                      "prototype_object": report["after"]["prototype_object"],
                      "prototype_action": report["after"]["prototype_action"]}, indent=2))


if __name__ == "__main__":
    main()
