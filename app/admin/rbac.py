"""Role-based access control for the admin panel.

Identity comes from the existing JWT (subject = API-key client name = ``users.external_id``).
The role is read from the database on EVERY request, so revoking a role is immediate.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, status

from app.admin.repository import AdminStoreError, AdminStoreOpener, get_admin_store_opener
from app.auth.dependencies import client_from_token

logger = logging.getLogger(__name__)

Role = Literal["end_user", "operator", "admin"]
PANEL_ROLES: tuple[Role, ...] = ("operator", "admin")


@dataclass(frozen=True)
class AdminUser:
    id: str
    external_id: str
    email: str | None
    role: Role


def load_user(opener: AdminStoreOpener, external_id: str) -> AdminUser | None:
    with opener() as store:
        row = store.get_user_by_external_id(external_id)
    if row is None:
        return None
    return AdminUser(
        id=str(row["id"]),
        external_id=row["external_id"],
        email=row.get("email"),
        role=row["role"],
    )


def current_user(
    client_id: Annotated[str, Depends(client_from_token)],
    opener: Annotated[AdminStoreOpener, Depends(get_admin_store_opener)],
) -> AdminUser:
    """Authenticated panel user with the role currently stored in the database."""
    try:
        user = load_user(opener, client_id)
    except AdminStoreError:
        logger.exception("No se pudo resolver el rol del usuario")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de administración no disponible"
        ) from None
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permisos para esta acción")
    return user


def require_role(*roles: Role) -> Callable[..., AdminUser]:
    """Dependency factory: 401 without credentials, 403 unless the DB role is in ``roles``."""
    allowed = frozenset(roles)

    def dependency(user: Annotated[AdminUser, Depends(current_user)]) -> AdminUser:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permisos para esta acción")
        return user

    return dependency
