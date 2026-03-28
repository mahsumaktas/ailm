"""LLM client, prompts, and degradation queue tests."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from ailm.llm.client import OllamaClient
from ailm.llm.prompts import (
    BRIEFING_SYSTEM,
    CLASSIFICATION_SYSTEM,
    build_briefing_prompt,
    build_classification_prompt,
)
from ailm.llm.queue import LLMTask, LLMTaskQueue


# --- Mock transport ---


def _mock_transport(handler):
    """Create an httpx MockTransport from a handler function."""
    return httpx.MockTransport(handler)


def _ollama_handler(request: httpx.Request) -> httpx.Response:
    """Simulates a healthy Ollama server."""
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "qwen3.5:9b"}]})

    if request.url.path == "/api/generate":
        body = json.loads(request.content)
        prompt = body.get("prompt", "")

        # Classification requests get JSON back
        if "<log_content>" in prompt:
            result = json.dumps({
                "type": "log_anomaly",
                "severity": "warning",
                "summary": "Segfault detected in process 1234",
                "requires_action": False,
            })
        else:
            result = "System is healthy. No critical issues in the last 24 hours."

        return httpx.Response(200, json={"response": result})

    return httpx.Response(404)


def _failing_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused")


def _bad_json_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": []})
    if request.url.path == "/api/generate":
        return httpx.Response(200, json={"response": "not valid json {{"})
    return httpx.Response(404)


def _missing_key_handler(request: httpx.Request) -> httpx.Response:
    """Returns valid JSON but with wrong schema (missing required keys)."""
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": []})
    if request.url.path == "/api/generate":
        return httpx.Response(200, json={"response": json.dumps({"random": "data"})})
    return httpx.Response(404)


def _no_response_key_handler(request: httpx.Request) -> httpx.Response:
    """Returns JSON without 'response' key."""
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": []})
    if request.url.path == "/api/generate":
        return httpx.Response(200, json={"output": "wrong key"})
    return httpx.Response(404)


# --- Prompt templates ---


class TestPrompts:
    def test_classification_prompt_wraps_in_tags(self):
        prompt = build_classification_prompt("kernel: OOM killed process 42")
        assert "<log_content>" in prompt
        assert "kernel: OOM killed process 42" in prompt
        assert "</log_content>" in prompt

    def test_classification_system_warns_untrusted(self):
        assert "untrusted" in CLASSIFICATION_SYSTEM.lower()
        assert "ignore" in CLASSIFICATION_SYSTEM.lower()

    def test_briefing_prompt_includes_events(self):
        prompt = build_briefing_prompt("- 3 packages updated\n- disk at 82%")
        assert "3 packages updated" in prompt
        assert "disk at 82%" in prompt

    def test_briefing_system_defined(self):
        assert "morning" in BRIEFING_SYSTEM.lower()

    def test_classification_prompt_safe_with_braces(self):
        """Log lines with { } (JSON logs) must not crash the template."""
        log = '{"level":"error","msg":"disk full","ts":1234}'
        prompt = build_classification_prompt(log)
        assert log in prompt
        assert "<log_content>" in prompt

    def test_briefing_prompt_safe_with_braces(self):
        summary = 'Event: {"type":"disk"}'
        prompt = build_briefing_prompt(summary)
        assert summary in prompt


# --- OllamaClient ---


class TestOllamaClient:
    async def test_health_check_success(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        result = await client.health_check()
        assert result is True
        assert client.available is True
        await client.close()

    async def test_health_check_failure(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_failing_handler), base_url="http://test")
        result = await client.health_check()
        assert result is False
        assert client.available is False
        await client.close()

    async def test_generate_success(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        result = await client.generate("hello")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0
        await client.close()

    async def test_generate_when_unavailable_returns_none(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = False

        result = await client.generate("hello")
        assert result is None
        await client.close()

    async def test_generate_error_marks_unavailable(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_failing_handler), base_url="http://test")
        client._available = True

        result = await client.generate("hello")
        assert result is None
        assert client.available is False
        await client.close()

    async def test_classify_log_returns_dict(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        result = await client.classify_log("kernel: segfault at 0000")
        assert result is not None
        assert result["type"] == "log_anomaly"
        assert result["severity"] == "warning"
        assert "summary" in result
        await client.close()

    async def test_classify_log_invalid_json_returns_none(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_bad_json_handler), base_url="http://test")
        client._available = True

        result = await client.classify_log("some log line")
        assert result is None
        await client.close()

    async def test_generate_briefing_returns_string(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        result = await client.generate_briefing("- disk at 45%\n- 2 packages updated")
        assert result is not None
        assert isinstance(result, str)
        await client.close()

    async def test_context_manager(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client.health_check = AsyncMock(return_value=False)
        async with client:
            # start() called — http client created (health check may fail, that's fine)
            assert client._http is not None
        # __aexit__ calls close() — http client gone
        assert client._http is None
        assert client.available is False

    async def test_classify_log_missing_keys_returns_none(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_missing_key_handler), base_url="http://test")
        client._available = True

        result = await client.classify_log("some log line")
        assert result is None
        assert client.available is True  # server responded, should stay available
        await client.close()

    async def test_generate_missing_response_key(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_no_response_key_handler), base_url="http://test")
        client._available = True

        result = await client.generate("hello")
        assert result is None
        assert client.available is True  # server responded, stays available
        await client.close()

    async def test_close_idempotent(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        await client.close()
        await client.close()  # should not raise


# --- LLMTaskQueue ---


class TestLLMTaskQueue:
    async def test_enqueue_and_pending(self):
        q = LLMTaskQueue()
        assert q.pending == 0
        q.enqueue(LLMTask(prompt="hello"))
        assert q.pending == 1
        q.enqueue(LLMTask(prompt="world"))
        assert q.pending == 2

    async def test_drain_processes_tasks(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        results: list[str] = []

        async def capture(result: str) -> None:
            results.append(result)

        q = LLMTaskQueue()
        q.enqueue(LLMTask(prompt="task1", callback=capture))
        q.enqueue(LLMTask(prompt="task2", callback=capture))

        processed = await q.drain(client)
        assert processed == 2
        assert q.pending == 0
        assert len(results) == 2
        await client.close()

    async def test_drain_stops_when_client_unavailable(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_failing_handler), base_url="http://test")
        client._available = True  # starts available, first generate will fail

        q = LLMTaskQueue()
        q.enqueue(LLMTask(prompt="task1"))
        q.enqueue(LLMTask(prompt="task2"))

        processed = await q.drain(client)
        assert processed == 0
        assert q.pending == 2  # tasks remain in queue
        await client.close()

    async def test_drain_discards_stale_tasks(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        q = LLMTaskQueue()
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        q.enqueue(LLMTask(prompt="stale", created=old))
        q.enqueue(LLMTask(prompt="fresh"))

        processed = await q.drain(client)
        assert processed == 1  # only fresh task processed
        assert q.pending == 0  # stale was discarded
        await client.close()

    async def test_drain_empty_queue(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        q = LLMTaskQueue()
        processed = await q.drain(client)
        assert processed == 0
        await client.close()

    def test_clear(self):
        q = LLMTaskQueue()
        q.enqueue(LLMTask(prompt="a"))
        q.enqueue(LLMTask(prompt="b"))
        assert q.pending == 2
        q.clear()
        assert q.pending == 0

    async def test_task_without_callback(self):
        client = OllamaClient("http://localhost:11434", "qwen3.5:9b", timeout=30)
        client._http = httpx.AsyncClient(transport=_mock_transport(_ollama_handler), base_url="http://test")
        client._available = True

        q = LLMTaskQueue()
        q.enqueue(LLMTask(prompt="no callback"))

        processed = await q.drain(client)
        assert processed == 1
        assert q.pending == 0
        await client.close()

    def test_queue_maxlen_evicts_oldest(self):
        q = LLMTaskQueue(maxlen=3)
        q.enqueue(LLMTask(prompt="a"))
        q.enqueue(LLMTask(prompt="b"))
        q.enqueue(LLMTask(prompt="c"))
        q.enqueue(LLMTask(prompt="d"))  # evicts "a"
        assert q.pending == 3
        # Oldest surviving task should be "b"
        assert q._tasks[0].prompt == "b"
