"""AWS Lambda handler for the Dave.io chat API.

The API Gateway HTTP API sends v2 events to :func:`handler`. Chat exchanges are
stored as unique S3 objects so concurrent requests cannot overwrite each other.
The API key is retrieved from SSM at runtime and cached only in the warm Lambda
process; it is never placed in an environment variable or logged.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import heapq
import json
import logging
import os
import urllib.error
import urllib.request
import uuid


logger = logging.getLogger()
logger.setLevel(logging.INFO)

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_TIMEOUT_SECONDS = 15
MAX_COMPLETION_TOKENS = 512
MAX_PROMPT_CHARS = 4_000
MAX_RESPONSE_CHARS = 8_000
MAX_BODY_BYTES = 20_000
MAX_BODY_BASE64_CHARS = 28_000
MAX_HISTORY_ITEMS = 80
MAX_STORED_OBJECT_BYTES = 65_536

_s3 = None
_ssm = None
_cached_api_key: str | None = None


class InvalidRequest(ValueError):
    """A client request does not match the supported API contract."""


class LLMUnavailable(RuntimeError):
    """The model provider could not return a usable response."""


class ChatDisabled(RuntimeError):
    """Chat is not configured (for example, during the history-only milestone)."""


def _s3_client():
    global _s3
    if _s3 is None:
        import boto3

        _s3 = boto3.client("s3")
    return _s3


def _ssm_client():
    global _ssm
    if _ssm is None:
        import boto3

        _ssm = boto3.client("ssm")
    return _ssm


def _api_key() -> str:
    global _cached_api_key
    if _cached_api_key:
        return _cached_api_key

    parameter_name = os.environ.get("LLM_PARAMETER", "").strip()
    if not parameter_name:
        raise ChatDisabled

    result = _ssm_client().get_parameter(Name=parameter_name, WithDecryption=True)
    value = result.get("Parameter", {}).get("Value", "")
    if not isinstance(value, str) or not value:
        raise ChatDisabled
    _cached_api_key = value
    return value


def _route(event: dict) -> str:
    route_key = event.get("routeKey")
    if route_key in {"GET /history", "POST /chat"}:
        return route_key

    request_context = event.get("requestContext") or {}
    http = request_context.get("http") or {}
    method = http.get("method", "")
    path = event.get("rawPath", "")
    return f"{method} {path}"


def _response(status_code: int, payload) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        "isBase64Encoded": False,
    }


def _request_payload(event: dict) -> dict:
    body = event.get("body")
    if not isinstance(body, str) or not body:
        raise InvalidRequest("A JSON request body is required.")
    if len(body) > MAX_BODY_BASE64_CHARS:
        raise InvalidRequest("Request body is too large.")

    try:
        raw = (
            base64.b64decode(body, validate=True)
            if event.get("isBase64Encoded")
            else body.encode("utf-8")
        )
        if len(raw) > MAX_BODY_BYTES:
            raise InvalidRequest("Request body is too large.")
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequest("Request body must be valid JSON.") from exc

    if not isinstance(payload, dict):
        raise InvalidRequest("Request body must be a JSON object.")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise InvalidRequest("Field 'prompt' must be a string.")
    prompt = prompt.strip()
    if not prompt:
        raise InvalidRequest("Field 'prompt' must not be empty.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise InvalidRequest(f"Field 'prompt' must be {MAX_PROMPT_CHARS} characters or fewer.")
    return {"prompt": prompt}


def _call_llm(prompt: str) -> str:
    api_key = _api_key()
    payload = json.dumps(
        {
            "model": os.environ.get("LLM_MODEL", "gpt-4.1-mini"),
            "messages": [{"role": "user", "content": prompt}],
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        OPENAI_CHAT_COMPLETIONS_URL,
        data=payload,
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=OPENAI_TIMEOUT_SECONDS) as result:
            response_payload = json.loads(result.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise LLMUnavailable from exc

    try:
        content = response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMUnavailable

    content = content.strip()
    if len(content) > MAX_RESPONSE_CHARS:
        raise LLMUnavailable
    return content


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _save_exchange(prompt: str, response: str) -> dict:
    timestamp = _utc_timestamp()
    record = {"prompt": prompt, "response": response, "timestamp": timestamp}
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_STORED_OBJECT_BYTES:
        raise ValueError("Exchange exceeded the storage size limit.")

    bucket = os.environ["HISTORY_BUCKET"]
    object_key = f"chats/{timestamp.replace(':', '').replace('-', '')}_{uuid.uuid4().hex}.json"
    _s3_client().put_object(
        Bucket=bucket,
        Key=object_key,
        Body=encoded,
        ContentType="application/json; charset=utf-8",
    )
    return record


def _history() -> list[dict]:
    bucket = os.environ["HISTORY_BUCKET"]
    s3 = _s3_client()
    latest: list[tuple[float, str, int]] = []
    token = None

    # Walk every listing page to satisfy the history contract, while retaining
    # only the newest bounded set for the frontend response.
    while True:
        request = {"Bucket": bucket, "Prefix": "chats/"}
        if token:
            request["ContinuationToken"] = token
        page = s3.list_objects_v2(**request)

        for item in page.get("Contents", []):
            key = item.get("Key")
            modified = item.get("LastModified")
            size = item.get("Size")
            if (
                not isinstance(key, str)
                or not key.startswith("chats/")
                or modified is None
                or not isinstance(size, int)
                or size > MAX_STORED_OBJECT_BYTES
            ):
                continue
            item_rank = (modified.timestamp(), key, size)
            if len(latest) < MAX_HISTORY_ITEMS:
                heapq.heappush(latest, item_rank)
            elif item_rank > latest[0]:
                heapq.heapreplace(latest, item_rank)

        if not page.get("IsTruncated"):
            break
        next_token = page.get("NextContinuationToken")
        if not next_token or next_token == token:
            raise RuntimeError("S3 returned an invalid history continuation token.")
        token = next_token

    records = []
    for _, key, _ in sorted(latest, reverse=True):
        # The app writes small JSON records. Refuse unexpected oversized objects
        # rather than expanding the Lambda response without bound.
        result = s3.get_object(Bucket=bucket, Key=key)
        raw = result["Body"].read(MAX_STORED_OBJECT_BYTES + 1)
        if len(raw) > MAX_STORED_OBJECT_BYTES:
            logger.warning("Skipping oversized history object.")
            continue
        try:
            item = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Skipping invalid history object.")
            continue
        if (
            isinstance(item, dict)
            and isinstance(item.get("prompt"), str)
            and isinstance(item.get("response"), str)
            and isinstance(item.get("timestamp"), str)
        ):
            records.append(item)
        else:
            logger.warning("Skipping invalid history object.")
    return records


def handler(event, context):
    """Handle API Gateway HTTP API v2 requests."""
    route = _route(event if isinstance(event, dict) else {})
    if route == "GET /history":
        try:
            return _response(200, _history())
        except Exception:
            logger.error("Unable to load chat history.")
            return _response(502, {"error": "History is temporarily unavailable."})

    if route == "POST /chat":
        try:
            payload = _request_payload(event)
        except InvalidRequest as exc:
            return _response(400, {"error": str(exc)})

        try:
            answer = _call_llm(payload["prompt"])
        except ChatDisabled:
            return _response(503, {"error": "Chat is not configured."})
        except Exception:
            logger.error("The model provider request failed.")
            return _response(502, {"error": "The model provider is temporarily unavailable."})

        try:
            record = _save_exchange(payload["prompt"], answer)
        except Exception:
            logger.error("Unable to persist chat exchange.")
            return _response(502, {"error": "The response could not be saved; please try again."})
        return _response(200, record)

    return _response(404, {"error": "Route not found."})
