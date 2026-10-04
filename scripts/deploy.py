#!/usr/bin/env python3
"""Build and deploy Dave.io, then verify the HTTPS and history endpoints."""

from __future__ import annotations

import argparse
import hashlib
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
BUILD = ROOT / ".build"
LAMBDA_SOURCE = ROOT / "backend" / "app.py"
LAMBDA_ZIP = BUILD / "lambda.zip"
EXPECTED_INDEX_SHA256 = "375b79d19c55a9be913c8e51e2ec3b0a08f5a8cb75ed990507e68936c8a02f2a"
READINESS_TIMEOUT_SECONDS = 300


def terraform(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    command = ["terraform", f"-chdir={INFRA}", *args]
    return subprocess.run(command, cwd=ROOT, env=env, check=True)


def build_lambda() -> None:
    if not LAMBDA_SOURCE.is_file():
        raise FileNotFoundError(f"Lambda handler not found: {LAMBDA_SOURCE}")
    BUILD.mkdir(exist_ok=True)
    temp_path = BUILD / "lambda.zip.tmp"
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            entry = zipfile.ZipInfo("app.py", date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, LAMBDA_SOURCE.read_bytes())
        with zipfile.ZipFile(temp_path) as archive:
            if "app.py" not in archive.namelist():
                raise RuntimeError("Lambda package is missing app.py at its root.")
        temp_path.replace(LAMBDA_ZIP)
    finally:
        temp_path.unlink(missing_ok=True)


def read_output(name: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["terraform", f"-chdir={INFRA}", "output", "-raw", name],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def fetch(url: str, timeout: int = 15) -> bytes:
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"Readiness probe returned HTTP {response.status} for {url}")
        return response.read()


def verify_deployment(app_url: str, api_url: str) -> None:
    expected_index = (ROOT / "takehomeassignmentdave_io" / "index.html").read_bytes()
    if hashlib.sha256(expected_index).hexdigest() != EXPECTED_INDEX_SHA256:
        raise RuntimeError("The supplied index.html no longer matches its recorded checksum.")

    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            deployed_index = fetch(f"{app_url}/index.html")
            if deployed_index != expected_index:
                raise RuntimeError("HTTPS page does not match the supplied index.html byte-for-byte.")

            config = fetch(f"{app_url}/config.js").decode("utf-8")
            if api_url not in config:
                raise RuntimeError("Generated config.js does not contain the deployed API URL.")

            history = json.loads(fetch(f"{api_url}/history"))
            if not isinstance(history, list):
                raise RuntimeError("GET /history returned an unexpected response shape.")
            print(f"Ready: {app_url}")
            print(f"History API: {api_url}/history ({len(history)} records)")
            return
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            time.sleep(5)

    raise TimeoutError(f"Deployment did not pass HTTPS/API readiness checks: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--enable-chat",
        action="store_true",
        help="Enable POST /chat and prompt privately for the OpenAI API key.",
    )
    mode.add_argument("--history-only", action="store_true", help="Explicitly disable chat (removes its SSM parameter).")
    args = parser.parse_args()

    if not os.environ.get("AWS_PROFILE"):
        parser.error("Set AWS_PROFILE to the SSO deployment profile before running deploy.py.")

    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["TF_VAR_enable_chat"] = "true" if args.enable_chat else "false"
    env.pop("TF_VAR_llm_api_key", None)

    if args.enable_chat:
        api_key = getpass.getpass("OpenAI API key (hidden; not saved to a file): ").strip()
        if not api_key:
            parser.error("Chat was requested, but no API key was entered.")
        env["TF_VAR_llm_api_key"] = api_key

    plan_path = None
    try:
        build_lambda()
        terraform("init", "-input=false", env=env)

        fd, path = tempfile.mkstemp(prefix="deploy-", suffix=".tfplan", dir=BUILD)
        os.close(fd)
        plan_path = Path(path)
        terraform("plan", "-input=false", f"-out={plan_path}", env=env)

        print("\nReview the plan above. Only proceed if its resource changes are expected.")
        if input("Apply this plan? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Deployment cancelled; no AWS changes were applied.")
            return 1

        terraform("apply", "-input=false", "-auto-approve", str(plan_path), env=env)
        app_url = read_output("app_url", env)
        api_url = read_output("api_url", env)
        verify_deployment(app_url, api_url)
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode or 1
    except Exception as exc:
        print(f"Deployment verification failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if plan_path is not None:
            plan_path.unlink(missing_ok=True)
        if args.enable_chat:
            env.pop("TF_VAR_llm_api_key", None)


if __name__ == "__main__":
    raise SystemExit(main())
