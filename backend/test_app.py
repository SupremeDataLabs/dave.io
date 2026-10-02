"""Offline unit tests for the Lambda API contract."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch

import app


class FakeBody:
    def __init__(self, value: bytes):
        self.value = value

    def read(self, size=-1):
        return self.value[:size]


class FakeS3:
    def __init__(self):
        self.writes = []
        self.pages = []
        self.objects = {}
        self.list_calls = []

    def put_object(self, **kwargs):
        self.writes.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        if kwargs.get("ContinuationToken"):
            return self.pages[1]
        return self.pages[0]

    def get_object(self, **kwargs):
        return {"Body": FakeBody(self.objects[kwargs["Key"]])}


def api_response(result):
    return result["statusCode"], json.loads(result["body"])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.s3_patch = patch.object(app, "_s3_client", return_value=self.s3)
        self.s3_patch.start()
        self.env_patch = patch.dict(os.environ, {"HISTORY_BUCKET": "ask-dave-history"})
        self.env_patch.start()
        self.addCleanup(self.s3_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def test_empty_history_returns_array(self):
        self.s3.pages = [{"Contents": [], "IsTruncated": False}]
        status, body = api_response(app.handler({"routeKey": "GET /history"}, None))
        self.assertEqual(status, 200)
        self.assertEqual(body, [])

    def test_invalid_prompt_is_rejected_without_provider_call(self):
        event = {"routeKey": "POST /chat", "body": json.dumps({"prompt": "   "})}
        with patch.object(app, "_call_llm") as call_llm:
            status, body = api_response(app.handler(event, None))
        self.assertEqual(status, 400)
        self.assertIn("prompt", body["error"])
        call_llm.assert_not_called()

    def test_oversized_prompt_is_rejected(self):
        event = {
            "routeKey": "POST /chat",
            "body": json.dumps({"prompt": "x" * (app.MAX_PROMPT_CHARS + 1)}),
        }
        status, _ = api_response(app.handler(event, None))
        self.assertEqual(status, 400)

    def test_chat_is_persisted_before_success_is_returned(self):
        event = {"routeKey": "POST /chat", "body": json.dumps({"prompt": "  hello  "})}
        with patch.object(app, "_call_llm", return_value="world"):
            status, body = api_response(app.handler(event, None))

        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"prompt", "response", "timestamp"})
        self.assertEqual(body["prompt"], "hello")
        self.assertEqual(body["response"], "world")
        self.assertEqual(len(self.s3.writes), 1)
        stored = json.loads(self.s3.writes[0]["Body"])
        self.assertEqual(stored, body)
        self.assertTrue(self.s3.writes[0]["Key"].startswith("chats/"))

    def test_chat_objects_use_unique_keys_for_concurrent_safe_writes(self):
        event = {"routeKey": "POST /chat", "body": json.dumps({"prompt": "hello"})}
        with patch.object(app, "_call_llm", return_value="world"):
            first = api_response(app.handler(event, None))
            second = api_response(app.handler(event, None))
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200)
        self.assertNotEqual(self.s3.writes[0]["Key"], self.s3.writes[1]["Key"])

    def test_storage_failure_does_not_return_success(self):
        event = {"routeKey": "POST /chat", "body": json.dumps({"prompt": "hello"})}
        with patch.object(app, "_call_llm", return_value="world"), patch.object(
            self.s3, "put_object", side_effect=RuntimeError("storage unavailable")
        ):
            status, body = api_response(app.handler(event, None))
        self.assertEqual(status, 502)
        self.assertIn("could not be saved", body["error"])

    def test_chat_disabled_returns_service_unavailable(self):
        event = {"routeKey": "POST /chat", "body": json.dumps({"prompt": "hello"})}
        with patch.dict(os.environ, {"LLM_PARAMETER": ""}, clear=False):
            os.environ.pop("LLM_PARAMETER", None)
            status, body = api_response(app.handler(event, None))
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "Chat is not configured.")

    def test_history_reads_all_pages_and_returns_newest_first(self):
        now = datetime.now(timezone.utc)
        entries = []
        for index in range(3):
            key = f"chats/{index}.json"
            record = {
                "prompt": f"prompt {index}",
                "response": f"response {index}",
                "timestamp": (now - timedelta(minutes=2 - index)).isoformat(),
            }
            self.s3.objects[key] = json.dumps(record).encode()
            entries.append({
                "Key": key,
                "LastModified": now - timedelta(minutes=2 - index),
                "Size": len(self.s3.objects[key]),
            })
        self.s3.pages = [
            {"Contents": entries[:2], "IsTruncated": True, "NextContinuationToken": "next"},
            {"Contents": entries[2:], "IsTruncated": False},
        ]

        status, body = api_response(app.handler({"routeKey": "GET /history"}, None))

        self.assertEqual(status, 200)
        self.assertEqual([record["prompt"] for record in body], ["prompt 2", "prompt 1", "prompt 0"])
        self.assertEqual(len(self.s3.list_calls), 2)
        self.assertEqual(self.s3.list_calls[1]["ContinuationToken"], "next")

    def test_history_response_is_capped_to_newest_items(self):
        now = datetime.now(timezone.utc)
        entries = []
        self.s3.objects = {}
        for index in range(app.MAX_HISTORY_ITEMS + 5):
            key = f"chats/{index:03d}.json"
            record = {"prompt": str(index), "response": "ok", "timestamp": str(index)}
            encoded = json.dumps(record).encode()
            self.s3.objects[key] = encoded
            entries.append({
                "Key": key,
                "LastModified": now + timedelta(seconds=index),
                "Size": len(encoded),
            })
        split_at = 80
        self.s3.pages = [
            {"Contents": entries[:split_at], "IsTruncated": True, "NextContinuationToken": "next"},
            {"Contents": entries[split_at:], "IsTruncated": False},
        ]

        records = app._history()

        self.assertEqual(len(records), app.MAX_HISTORY_ITEMS)
        self.assertEqual(records[0]["prompt"], str(app.MAX_HISTORY_ITEMS + 4))
        self.assertEqual(records[-1]["prompt"], "5")

    def test_unknown_route_returns_not_found(self):
        status, body = api_response(app.handler({"routeKey": "GET /unknown"}, None))
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "Route not found.")

    def test_base64_json_body_is_supported(self):
        encoded = base64.b64encode(json.dumps({"prompt": "hello"}).encode()).decode()
        event = {"routeKey": "POST /chat", "body": encoded, "isBase64Encoded": True}
        with patch.object(app, "_call_llm", return_value="world"):
            status, _ = api_response(app.handler(event, None))
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
