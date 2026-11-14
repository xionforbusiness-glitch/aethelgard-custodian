"""
sitecustomize.py - Automatic Python initialization hook for Aethelgard Custodian.
Ensures child processes (including `hermes gateway run`) automatically load and apply
the Hermes typing indicator lifecycle and proxy hardening patches via PYTHONPATH.
"""

import os
import sys

try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import gateway_typing_patch
    gateway_typing_patch.apply_typing_lifecycle_patches()
except Exception:
    pass
