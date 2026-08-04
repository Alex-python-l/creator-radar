"""AES-256-GCM encrypted credentials scoped to a workspace."""

import base64
import hashlib
import os
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlmodel import Session, select

from app.models import WorkspaceSecret

ALLOWED_SECRET_KEYS = frozenset({"GEELARK_TOKEN", "GEMINI_API_KEY"})


def _master_key() -> bytes:
    encoded = os.getenv("WORKSPACE_SECRETS_KEY", "")
    if not encoded:
        raise RuntimeError("WORKSPACE_SECRETS_KEY is required once workspace secrets exist")
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RuntimeError("WORKSPACE_SECRETS_KEY must be valid base64") from exc
    if len(key) != 32:
        raise RuntimeError("WORKSPACE_SECRETS_KEY must decode to exactly 32 bytes")
    return key


def _fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()


def _aad(workspace_id: int, name: str) -> bytes:
    return f"creator-radar:{workspace_id}:{name}".encode()


def _mask(value: str) -> str:
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "•" * 6 + value[-4:]


def decrypt(row: WorkspaceSecret) -> str:
    key = _master_key()
    if row.key_fingerprint != _fingerprint(key):
        raise RuntimeError("WORKSPACE_SECRETS_KEY does not match encrypted data")
    plaintext = AESGCM(key).decrypt(
        row.nonce, row.ciphertext, _aad(row.workspace_id, row.key),
    )
    return plaintext.decode()


def get_secret(session: Session, workspace_id: int, name: str) -> str | None:
    if name not in ALLOWED_SECRET_KEYS:
        raise ValueError("unsupported workspace secret")
    row = session.get(WorkspaceSecret, (workspace_id, name))
    return decrypt(row) if row else None


def get_status(session: Session, workspace_id: int) -> dict[str, dict]:
    rows = session.exec(
        select(WorkspaceSecret).where(WorkspaceSecret.workspace_id == workspace_id)
    ).all()
    values = {row.key: decrypt(row) for row in rows}
    return {
        name: {"set": name in values, "masked": _mask(values.get(name, ""))}
        for name in sorted(ALLOWED_SECRET_KEYS)
    }


def update(session: Session, workspace_id: int, values: dict[str, str]) -> list[str]:
    key = _master_key()
    fingerprint = _fingerprint(key)
    cipher = AESGCM(key)
    updated: list[str] = []
    for name, value in values.items():
        if name not in ALLOWED_SECRET_KEYS or not value:
            continue
        nonce = os.urandom(12)
        row = session.get(WorkspaceSecret, (workspace_id, name))
        if row is None:
            row = WorkspaceSecret(
                workspace_id=workspace_id,
                key=name,
                ciphertext=b"",
                nonce=nonce,
                key_fingerprint=fingerprint,
            )
        row.nonce = nonce
        row.ciphertext = cipher.encrypt(nonce, value.encode(), _aad(workspace_id, name))
        row.key_fingerprint = fingerprint
        row.updated_at = datetime.utcnow()
        session.add(row)
        updated.append(name)
    session.commit()
    return updated


def validate_secret_store(session: Session) -> None:
    """Fail startup when existing ciphertext cannot be decrypted."""
    rows = session.exec(select(WorkspaceSecret)).all()
    for row in rows:
        decrypt(row)


def list_configured_workspaces(session: Session, name: str) -> list[int]:
    if name not in ALLOWED_SECRET_KEYS:
        raise ValueError("unsupported workspace secret")
    workspace_ids = list(session.exec(
        select(WorkspaceSecret.workspace_id)
        .where(WorkspaceSecret.key == name)
        .distinct()
    ).all())
    default_id = os.getenv("STUDIO_DEFAULT_WORKSPACE_ID", "")
    if not workspace_ids and default_id:
        return [int(default_id)]
    return workspace_ids
