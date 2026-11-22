"""Publication des Tools / Functions versionnés dans git vers Open WebUI, via son API REST.

Les endpoints utilisés (/api/v1/tools/..., /api/v1/functions/...) sont ceux de
l'API Open WebUI ; ils sont à valider sur la version épinglée (docs/POINTS_A_VERIFIER.md).
En cas d'échec, le code peut toujours être collé manuellement dans
Espace de travail > Outils (ou Admin > Fonctions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONTMATTER = re.compile(r'^\s*"""(.*?)"""', re.DOTALL)


@dataclass
class DeployResult:
    kind: str
    id: str
    action: str
    ok: bool
    message: str = ""


def parse_frontmatter(code: str) -> dict[str, str]:
    match = _FRONTMATTER.match(code)
    meta: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip()
    return meta


def deploy(base_url: str, api_key: str, directory: Path, timeout: float = 30.0) -> list[DeployResult]:
    import httpx

    results: list[DeployResult] = []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, trust_env=False) as client:
        for kind in ("tools", "functions"):
            folder = directory / kind
            if not folder.is_dir():
                continue
            for file in sorted(folder.glob("*.py")):
                code = file.read_text(encoding="utf-8")
                meta = parse_frontmatter(code)
                ident = file.stem
                payload = {
                    "id": ident,
                    "name": meta.get("title", ident),
                    "content": code,
                    "meta": {"description": meta.get("description", ""), "manifest": meta},
                }
                try:
                    exists = client.get(f"/api/v1/{kind}/id/{ident}").status_code == 200
                    path = f"/api/v1/{kind}/id/{ident}/update" if exists else f"/api/v1/{kind}/create"
                    response = client.post(path, json=payload)
                    ok = response.status_code < 400
                    message = "" if ok else f"HTTP {response.status_code}: {response.text[:300]}"
                    if ok and kind == "functions":
                        # Une Function est créée inactive : on l'active (l'association au modèle
                        # ou l'activation globale reste un choix de l'administrateur).
                        info = client.get(f"/api/v1/functions/id/{ident}").json()
                        if not info.get("is_active"):
                            client.post(f"/api/v1/functions/id/{ident}/toggle")
                        message = "activée"
                    results.append(DeployResult(kind, ident, "mise à jour" if exists else "création", ok, message))
                except httpx.HTTPError as exc:
                    results.append(DeployResult(kind, ident, "?", False, str(exc)))
    return results
