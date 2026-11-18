"""Permanent memory must work when the home directory is unusable (as in containers)."""

from __future__ import annotations

import os
import subprocess
import sys


def test_memory_works_without_a_writable_home(tmp_path):
    env = {**os.environ, "HOME": "/dev/null"}  # mkdir under it fails even for root
    env.pop("MEM0_DIR", None)
    code = (
        "from clipforge.memory.service import get_memory;"
        "m = get_memory();"
        "m.remember('Prefers bold yellow captions', user_id='home-test', infer=False);"
        "items = m.list(user_id='home-test');"
        "print(len(items));"
        "m.delete_all(user_id='home-test')"
    )
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120,
                          check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().splitlines()[-1] == "1"
