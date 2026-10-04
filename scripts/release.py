"""Noninteractive release of an existing stack; never creates or rotates secrets.

Remote backend is generated only for this configured working directory. Existing
local state must be explicitly migrated before using this script in production.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

import deploy

PLAN = deploy.BUILD / "release.tfplan"
PRESERVE_KEY = "existing-key-managed-outside-release"


def validate_plan(plan):
    changes = plan.get("resource_changes", [])
    for resource in changes:
        actions = resource["change"]["actions"]
        if "delete" in actions:
            raise ValueError("Automated release refuses deletes or replacements.")
        if resource["type"] == "aws_ssm_parameter" and actions != ["no-op"]:
            raise ValueError("Secret changes require the manual rotation workflow.")
    enabled = plan.get("planned_values", {}).get("outputs", {}).get("chat_enabled", {}).get("value")
    if enabled is not True:
        raise ValueError("Automated release must preserve enabled chat.")


def environment():
    env = os.environ.copy()
    version = env.get("TF_VAR_llm_key_version", "")
    if not version.isdigit() or int(version) < 1:
        raise ValueError("Set TF_VAR_llm_key_version to the existing rotation version.")
    env.update(TF_IN_AUTOMATION="1", TF_INPUT="0", TF_VAR_enable_chat="true",
               TF_VAR_llm_api_key=PRESERVE_KEY)
    return env


def init(env):
    bucket = env["TF_STATE_BUCKET"]
    region = env["AWS_REGION"]
    # Credentials stay in the standard AWS credential chain, never backend config.
    config = {"terraform": {"backend": {"s3": {
        "bucket": bucket, "key": "application/terraform.tfstate", "region": region,
        "encrypt": True, "use_lockfile": True,
    }}}}
    path = deploy.INFRA / "remote-backend.tf.json"
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError("Existing backend differs; migrate explicitly before release.")
    path.write_text(json.dumps(config, indent=2) + "\n")
    deploy.terraform("init", "-input=false", "-lockfile=readonly", env=env)


def checked_plan(env):
    result = subprocess.run(
        ["terraform", f"-chdir={deploy.INFRA}", "show", "-json", str(PLAN)],
        env=env, check=True, capture_output=True, text=True,
    )
    plan = json.loads(result.stdout)  # Never print raw plan JSON or publish it.
    validate_plan(plan)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["plan", "apply"])
    args = parser.parse_args()
    env = environment()
    deploy.build_lambda()
    init(env)
    if args.phase == "plan":
        # Refuse empty remote state rather than accidentally provisioning a second app.
        enabled = deploy.read_output("chat_enabled", env)
        if enabled != "true":
            raise ValueError("Expected an existing, chat-enabled remote stack.")
        deploy.terraform("plan", "-input=false", "-lock-timeout=5m", f"-out={PLAN}", env=env)
        plan = checked_plan(env)
        lines = ["## Terraform plan", "Review this run's Terraform plan log before approving production."]
        for r in plan.get("resource_changes", []):
            actions = r["change"]["actions"]
            if actions != ["no-op"]:
                lines.append(f"- `{r['address']}`: {', '.join(actions)}")
        if len(lines) == 2:
            lines.append("No resource changes.")
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write("\n".join(lines) + "\n")
    else:
        checked_plan(env)
        deploy.terraform("apply", "-input=false", "-lock-timeout=5m", str(PLAN), env=env)
        deploy.verify_deployment(deploy.read_output("app_url", env), deploy.read_output("api_url", env))


if __name__ == "__main__":
    main()
