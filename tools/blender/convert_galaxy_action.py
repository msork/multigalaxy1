"""Profile-driven Galaxy action to target-rig converter entry point.

The implementation remains in prototype_direct_luigi_walk_literal.py to keep
the approved V2 walk's audited code path available for regression comparison.
New callers should use this generic command and provide a character profile.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prototype_direct_luigi_walk_literal import main


if __name__ == "__main__":
    main()
