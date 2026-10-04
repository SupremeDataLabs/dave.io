import unittest
from unittest.mock import patch
import release


class ReleaseTests(unittest.TestCase):
    def plan(self, resource_type="aws_lambda_function", actions=None):
        return {"resource_changes": [{"type": resource_type, "change": {"actions": actions or ["update"]}}],
                "planned_values": {"outputs": {"chat_enabled": {"value": True}}}}

    def test_allows_code_update(self):
        release.validate_plan(self.plan())

    def test_allows_unchanged_secret(self):
        release.validate_plan(self.plan("aws_ssm_parameter", ["no-op"]))

    def test_refuses_secret_update(self):
        with self.assertRaises(ValueError):
            release.validate_plan(self.plan("aws_ssm_parameter"))

    def test_refuses_replacement(self):
        with self.assertRaises(ValueError):
            release.validate_plan(self.plan(actions=["delete", "create"]))

    def test_refuses_disabled_chat(self):
        plan = self.plan()
        plan["planned_values"]["outputs"]["chat_enabled"]["value"] = False
        with self.assertRaises(ValueError):
            release.validate_plan(plan)

    def test_requires_explicit_key_version(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            release.environment()

    def test_no_secret_supplied_to_release(self):
        with patch.dict("os.environ", {"TF_VAR_llm_key_version": "2", "TF_VAR_llm_api_key": "not-for-release"}):
            env = release.environment()
            self.assertEqual(env["TF_VAR_llm_api_key"], release.PRESERVE_KEY)
            self.assertEqual(env["TF_VAR_enable_chat"], "true")
