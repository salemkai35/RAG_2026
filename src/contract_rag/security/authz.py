"""Autorisation métier : quels contrats un utilisateur peut-il consulter ?

Les Tools interrogent directement la base métier : ils ne bénéficient donc PAS
des droits d'accès d'Open WebUI sur les bases de connaissances. Ce module
applique le périmètre défini dans la table ``acces_contrat``.

Principaux : ``*`` (tout le monde), ``user:<email>``, ``group:<nom>``.
Une règle avec ``contrat_id`` NULL donne accès à tous les contrats.
Le rôle Open WebUI ``admin`` voit tout.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UserContext:
    id: str
    email: str
    name: str = ""
    role: str = "user"
    groups: tuple[str, ...] = field(default_factory=tuple)

    @property
    def principals(self) -> list[str]:
        values = ["*"]
        if self.email:
            values.append(f"user:{self.email.lower()}")
        values += [f"group:{g}" for g in self.groups]
        return values

    @property
    def label(self) -> str:
        return self.email or self.id or "inconnu"


SYSTEM_USER = UserContext(id="system", email="", name="CLI", role="admin")


@dataclass(frozen=True)
class AccessScope:
    all_contracts: bool
    contrat_ids: frozenset[int] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not self.all_contracts and not self.contrat_ids

    def allows(self, contrat_id: int | None) -> bool:
        return self.all_contracts or (contrat_id is not None and contrat_id in self.contrat_ids)


def resolve_scope(conn, user: UserContext, default_access: str = "none") -> AccessScope:
    if user.role == "admin":
        return AccessScope(all_contracts=True)
    rows = conn.execute(
        "SELECT contrat_id FROM acces_contrat WHERE lower(principal) = ANY(%s)",
        ([p.lower() for p in user.principals],),
    ).fetchall()
    if not rows:
        return AccessScope(all_contracts=default_access == "all")
    if any(r["contrat_id"] is None for r in rows):
        return AccessScope(all_contracts=True)
    return AccessScope(all_contracts=False, contrat_ids=frozenset(r["contrat_id"] for r in rows))
