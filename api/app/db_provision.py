"""Helpers for auto-provisioning PostgreSQL roles/databases in dev."""

from __future__ import annotations

import logging
import os
import re
from typing import Final

import asyncpg
from asyncpg.exceptions import InvalidAuthorizationSpecificationError, InvalidCatalogNameError
from sqlalchemy.engine.url import make_url

logger = logging.getLogger(__name__)

_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DBProvisioningError(RuntimeError):
    """Raised when automatic database provisioning fails."""


def _ensure_identifier(value: str, kind: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise DBProvisioningError(f"Invalid {kind} identifier: {value!r}")
    return value


def _escape_literal(value: str) -> str:
    return value.replace("'", "''")


async def provision_role_and_database(database_url: str) -> None:
    """Ensure that the role/database described by ``database_url`` exists.

    This helper is intended for local development scenarios where the
    application has enough privileges (via a superuser connection stored in
    env vars) to create the missing role/database on the fly. It is a no-op if
    both objects already exist.
    """

    url = make_url(database_url)
    driver = url.drivername.split("+", 1)[0]
    if driver != "postgresql":
        raise DBProvisioningError(
            "Automatic provisioning is only available for PostgreSQL URLs."
        )

    role = url.username
    if not role:
        raise DBProvisioningError(
            "DATABASE_URL must include username for auto provisioning."
        )
    password = url.password or ""
    database = url.database or role
    host = url.host or "127.0.0.1"
    port = url.port or 5432

    env_superuser = os.getenv("FINNEWS_DB_SUPERUSER")
    env_superpass = os.getenv("FINNEWS_DB_SUPERPASS")
    env_superdb = os.getenv("FINNEWS_DB_SUPERDB")

    # Build candidate sets for user/password/db to maximize success in dev
    user_candidates = []
    if env_superuser:
        user_candidates.append(env_superuser)
    user_candidates += ["postgres", role, "news"]
    # unique preserve order
    seen = set()
    user_candidates = [u for u in user_candidates if not (u in seen or seen.add(u))]

    password_candidates = []
    if env_superpass is not None:
        password_candidates.append(env_superpass)
    # try URL password then no password (ident/trust)
    password_candidates += [password or "", None]
    # unique preserving order
    seen = set()
    tmp = []
    for p in password_candidates:
        key = (p if p is not None else "__NONE__")
        if key in seen:
            continue
        seen.add(key)
        tmp.append(p)
    password_candidates = tmp

    database_candidates = []
    if env_superdb:
        database_candidates.append(env_superdb)
    # connect to a known always-present db first if possible
    database_candidates += ["postgres", database or "postgres"]
    # unique preserve order
    seen = set()
    database_candidates = [d for d in database_candidates if not (d in seen or seen.add(d))]

    last_error: Exception | None = None
    conn = None
    for user_candidate in user_candidates:
        for pwd_candidate in password_candidates:
            for db_candidate in database_candidates:
                try:
                    logger.info(
                        "Auto-provision: trying connect as '%s' to %s:%s/%s",
                        user_candidate,
                        host,
                        port,
                        db_candidate,
                    )
                    conn = await asyncpg.connect(
                        user=user_candidate,
                        password=(pwd_candidate or None),
                        host=host,
                        port=port,
                        database=db_candidate,
                    )
                    superuser = user_candidate  # for messages below
                    break
                except (InvalidAuthorizationSpecificationError, InvalidCatalogNameError, asyncpg.PostgresError, OSError) as exc:  # pragma: no cover
                    last_error = exc
                    continue
            if conn:
                break
        if conn:
            break
    if not conn:
        raise DBProvisioningError(
            f"Failed to connect with any provisioning user on {host}:{port}: {last_error}"
        )

    role_ident = _ensure_identifier(role, "role")
    database_ident = _ensure_identifier(database, "database")

    try:
        role_exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname=$1", role
        )
        if not role_exists:
            password_clause = (
                f" PASSWORD '{_escape_literal(password)}'" if password else ""
            )
            await conn.execute(
                f"CREATE ROLE {role_ident} WITH LOGIN{password_clause}"
            )
            logger.info("Created role '%s'", role)

        database_exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1", database
        )
        if not database_exists:
            await conn.execute(
                f"CREATE DATABASE {database_ident} OWNER {role_ident}"
            )
            logger.info(
                "Created database '%s' owned by '%s'", database, role
            )
    except asyncpg.PostgresError as exc:  # pragma: no cover - depends on server state
        raise DBProvisioningError(
            f"Failed to provision database/role automatically: {exc}"
        ) from exc
    finally:
        await conn.close()


__all__ = ["DBProvisioningError", "provision_role_and_database"]
