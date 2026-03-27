"""Async Ollama HTTP client with graceful degradation."""

import json
import logging

import httpx

from ailm.llm.prompts import (
    BRIEFING_SYSTEM,
    CLASSIFICATION_SYSTEM,
    build_briefing_prompt,
    build_classification_prompt,
)

logger = logging.getLogger(__name__)


_REQUIRED_CLASSIFICATION_KEYS = {"type", "severity", "summary"}


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: int) -> None:
        self._base_url = base_url
        self._model = model
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._timeout, connect=5.0),
        )
        self._available = await self.health_check()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._available = False

    async def __aenter__(self) -> "OllamaClient":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def health_check(self) -> bool:
        if self._http is None:
            return False
        try:
            resp = await self._http.get("/api/tags")
            self._available = resp.status_code == 200
        except httpx.HTTPError:
            self._available = False
        return self._available

    async def generate(self, prompt: str, system: str | None = None) -> str | None:
        """Raw generation. Returns None if unavailable or on error."""
        if self._http is None or not self._available:
            return None
        try:
            resp = await self._http.post(
                "/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "system": system or "",
                    "stream": False,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.warning("Ollama HTTP error, marking unavailable")
            self._available = False
            return None

        data = resp.json()
        if "response" not in data:
            logger.warning("Ollama response missing 'response' key")
            return None
        return data["response"]

    async def classify_log(self, log_line: str) -> dict | None:
        """Classify a log line. Returns parsed dict with type/severity/summary, or None."""
        prompt = build_classification_prompt(log_line)
        result = await self.generate(prompt, system=CLASSIFICATION_SYSTEM)
        if result is None:
            return None
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON for classification")
            return None
        if not isinstance(parsed, dict) or not _REQUIRED_CLASSIFICATION_KEYS <= parsed.keys():
            logger.warning("LLM classification missing required fields")
            return None
        return parsed

    async def generate_briefing(self, events_summary: str) -> str | None:
        """Generate a morning briefing from event summaries."""
        prompt = build_briefing_prompt(events_summary)
        return await self.generate(prompt, system=BRIEFING_SYSTEM)
