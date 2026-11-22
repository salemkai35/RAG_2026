"""Socle HTTP commun : contrôle des hôtes autorisés, TLS interne, reprises."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

from ..config import Endpoint, Settings

log = logging.getLogger(__name__)


class ModelEndpointError(RuntimeError):
    """Erreur d'appel à un endpoint de modèle interne."""


def host_is_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    """Vrai si l'hôte de ``url`` figure dans la liste blanche.

    ``"llm.interne.local"`` autorise exactement cet hôte ; ``".interne.local"``
    autorise tous ses sous-domaines. Une liste vide désactive le contrôle.
    """
    if not allowed_hosts:
        return True
    host = (urlparse(url).hostname or "").lower()
    for allowed in allowed_hosts:
        if allowed.startswith("."):
            if host.endswith(allowed) or host == allowed[1:]:
                return True
        elif host == allowed:
            return True
    return False


def ensure_configured(endpoint: Endpoint, settings: Settings) -> None:
    if not endpoint.base_url:
        raise ModelEndpointError(
            f"Endpoint '{endpoint.name}' non configuré ({endpoint.env_prefix}BASE_URL vide)."
        )
    if not host_is_allowed(endpoint.base_url, settings.allowed_hosts):
        raise ModelEndpointError(
            f"Endpoint '{endpoint.name}' refusé : l'hôte de {endpoint.base_url} n'est pas dans "
            f"CRAG_ALLOWED_HOSTS ({', '.join(settings.allowed_hosts)})."
        )


def build_client(endpoint: Endpoint, settings: Settings):
    import httpx

    ensure_configured(endpoint, settings)
    headers = {"Accept": "application/json"}
    if endpoint.api_key:
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
    verify: Any = settings.verify_tls
    if settings.verify_tls and settings.ca_bundle:
        verify = settings.ca_bundle
    return httpx.Client(
        base_url=endpoint.base_url,
        headers=headers,
        timeout=endpoint.timeout,
        verify=verify,
        # Par défaut on ignore HTTP(S)_PROXY : les appels restent sur le réseau interne.
        trust_env=settings.http_trust_env,
    )


def post_json(client, endpoint: Endpoint, path: str, payload: dict, retries: int = 3) -> Any:
    """POST JSON avec reprises sur erreurs réseau / 429 / 5xx."""
    import httpx

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = client.post(path, json=payload)
            if response.status_code == 429 or response.status_code >= 500:
                raise ModelEndpointError(
                    f"{endpoint.name}: HTTP {response.status_code} sur {path} : {response.text[:300]}"
                )
            if response.status_code >= 400:
                # Erreur client : inutile de réessayer.
                raise ModelEndpointError(
                    f"{endpoint.name}: HTTP {response.status_code} sur {path} : {response.text[:500]}"
                )
            return response.json()
        except ModelEndpointError as exc:
            last_error = exc
            if "HTTP 4" in str(exc) and "HTTP 429" not in str(exc):
                raise
        except (httpx.TransportError, ValueError) as exc:
            last_error = ModelEndpointError(f"{endpoint.name}: échec d'appel {path} : {exc}")
        if attempt < retries:
            delay = 2 ** (attempt - 1)
            log.warning("%s – nouvel essai dans %ss", last_error, delay)
            time.sleep(delay)
    assert last_error is not None
    raise last_error
