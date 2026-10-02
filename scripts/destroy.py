#!/usr/bin/env python3
"""Destroy Dave.io Terraform-managed AWS resources."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"


def main() -> int:
    if not os.environ.get("AWS_PROFILE"):
        print("Set AWS_PROFILE to the SSO deployment profile before running destroy.py.", file=sys.stderr)
        return 2

    print("This destroys the Dave.io stack and permanently deletes chat history in its S3 bucket.")
    print("Terraform will show the destroy plan and require the usual confirmation.")
    try:
        subprocess.run(
            ["terraform", f"-chdir={INFRA}", "init", "-input=false"],
            cwd=ROOT,
            env=os.environ.copy(),
            check=True,
        )
        subprocess.run(
            ["terraform", f"-chdir={INFRA}", "destroy", "-input=false"],
            cwd=ROOT,
            env=os.environ.copy(),
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
