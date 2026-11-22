"""Client LLM (POST {base}/chat/completions)."""

from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings
from .http import ModelEndpointError, build_client, post_json


class LLMClient:
    def __init__(self, settings: Settings | None = None, endpoint=None, retries: int = 3):
        self.settings = settings or get_settings()
        self.endpoint = endpoint or self.settings.llm
        self.retries = retries
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.endpoint, self.settings)
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_schema: dict | None = None,
    ) -> str:
        if not self.endpoint.model:
            raise ModelEndpointError(f"{self.endpoint.env_prefix}MODEL non défini.")
        payload: dict[str, Any] = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_schema:
            # Sortie structurée (supportée par vLLM / la plupart des serveurs OpenAI-compatibles).
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reponse", "schema": json_schema, "strict": True},
            }
        data = post_json(self.client, self.endpoint, "/chat/completions", payload, self.retries)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelEndpointError(f"Réponse LLM inattendue : {str(data)[:300]}") from exc

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
