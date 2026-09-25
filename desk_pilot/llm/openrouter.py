from __future__ import annotations

from typing import Any, Protocol

import httpx

from desk_pilot import APP_NAME, OPENROUTER_BASE_URL


class LLMError(RuntimeError):
    pass


class LLMClient(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Return OpenAI-style {content, tool_calls, raw}."""


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        reasoning_effort: str = "low",
        timeout: float = 90.0,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = model
        self.reasoning_effort = (reasoning_effort or "low").lower()
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("No OpenRouter API key. Paste one in Settings or set OPENROUTER_API_KEY.")
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": 1024,
        }
        if self.reasoning_effort in {"none", "minimal", "low"}:
            body["reasoning"] = {"effort": self.reasoning_effort}
        try:
            data = self._post(body)
        except LLMError as exc:
            if "reasoning" in str(exc).lower() and "reasoning" in body:
                body.pop("reasoning", None)
                data = self._post(body)
            else:
                raise
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "finish_reason": choice.get("finish_reason"),
            "model": data.get("model") or self.model,
            "usage": data.get("usage") or {},
        }

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://desk-pilot.local",
            "X-OpenRouter-Title": APP_NAME,
        }
        try:
            response = self._http.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenRouter request failed: {exc}") from exc
        if response.status_code == 401:
            raise LLMError("OpenRouter rejected the API key (401). Check Settings.")
        if response.status_code >= 400:
            detail = response.text[:500]
            raise LLMError(f"OpenRouter HTTP {response.status_code}: {detail}")
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError("OpenRouter returned non-JSON.") from exc
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            if isinstance(err, dict):
                raise LLMError(str(err.get("message") or err))
            raise LLMError(str(err))
        return data
