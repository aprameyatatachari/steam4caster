"""File-backed model artifacts plus an in-process cache of the active model.

A deliberately small registry: artifacts are joblib files on disk (or a mounted
volume/object-store path), their identity, checksum, metrics and lifecycle live in the
``model_versions`` table. Artifacts are only deserialised after their SHA-256 matches
the registered checksum.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.metrics import MODEL_FALLBACKS
from app.forecasting.features.builder import FEATURE_NAMES
from app.forecasting.types import FEATURE_SCHEMA_VERSION, HORIZONS
from app.models import ModelVersion
from app.models.enums import ModelStatus

logger = get_logger(__name__)

ARTIFACT_FORMAT = 1
RETRY_FAILED_LOAD_SECONDS = 300


class ArtifactError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class LoadedModel:
    version: str
    version_id: uuid.UUID | None
    feature_names: list[str]
    sale_models: dict[int, Any]
    calibrators: dict[int, Any]
    tier_model: Any | None
    tier_classes: list[str]
    feature_ranges: dict[str, tuple[float, float]]
    ece: dict[int, float] = field(default_factory=dict)


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_artifact(directory: str | Path, version: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Write an artifact and return ``(path, sha256)``."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{version}.joblib"
    joblib.dump({**payload, "format": ARTIFACT_FORMAT, "version": version}, path, compress=3)
    return str(path), file_checksum(path)


def load_artifact(
    path: str | Path, expected_checksum: str, version_id: uuid.UUID | None = None
) -> LoadedModel:
    file = Path(path)
    if not file.is_file():
        raise ArtifactError("artifact_missing")
    if file_checksum(file) != expected_checksum:
        raise ArtifactError("checksum_mismatch")
    try:
        payload = joblib.load(file)
    except Exception as exc:
        raise ArtifactError("artifact_unreadable") from exc
    if not isinstance(payload, dict) or payload.get("format") != ARTIFACT_FORMAT:
        raise ArtifactError("unsupported_format")
    if payload.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ArtifactError("feature_schema_mismatch")
    if list(payload.get("feature_names", [])) != list(FEATURE_NAMES):
        raise ArtifactError("feature_names_mismatch")
    if set(payload.get("sale_models", {})) != set(HORIZONS):
        raise ArtifactError("missing_horizon_model")
    return LoadedModel(
        version=str(payload["version"]),
        version_id=version_id,
        feature_names=list(payload["feature_names"]),
        sale_models=payload["sale_models"],
        calibrators=payload["calibrators"],
        tier_model=payload.get("tier_model"),
        tier_classes=list(payload.get("tier_classes", [])),
        feature_ranges={
            k: (float(v[0]), float(v[1])) for k, v in payload["feature_ranges"].items()
        },
        ece={int(k): float(v) for k, v in payload.get("ece", {}).items()},
    )


class ModelStore:
    """Resolves and caches the active model once per process.

    The active version is re-read from the database (a cheap indexed lookup) so that
    activation and rollback take effect without restarts, but an artifact is only
    loaded from disk when the active version actually changes.
    """

    def __init__(self, *, enabled: bool = True, pinned_version: str | None = None) -> None:
        self._enabled = enabled
        self._pinned = pinned_version
        self._loaded: LoadedModel | None = None
        self._failed: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get_active(self, session: AsyncSession) -> LoadedModel | None:
        if not self._enabled:
            return None
        stmt = select(ModelVersion)
        if self._pinned:
            stmt = stmt.where(ModelVersion.version == self._pinned)
        else:
            stmt = stmt.where(ModelVersion.status == ModelStatus.ACTIVE)
        row = (
            (await session.execute(stmt.order_by(ModelVersion.activated_at.desc())))
            .scalars()
            .first()
        )
        if row is None:
            self._loaded = None
            return None
        if self._loaded is not None and self._loaded.version == row.version:
            return self._loaded
        failed_at = self._failed.get(row.version)
        if failed_at is not None and time.monotonic() - failed_at < RETRY_FAILED_LOAD_SECONDS:
            return None
        async with self._lock:
            if self._loaded is not None and self._loaded.version == row.version:
                return self._loaded
            try:
                self._loaded = await asyncio.to_thread(
                    load_artifact, row.artifact_uri, row.artifact_checksum, row.id
                )
                self._failed.pop(row.version, None)
                logger.info("loaded model artifact", extra={"model_version": row.version})
            except ArtifactError as exc:
                self._loaded = None
                self._failed[row.version] = time.monotonic()
                MODEL_FALLBACKS.labels(exc.reason).inc()
                logger.error(
                    "model artifact unusable; falling back to baseline",
                    extra={"model_version": row.version, "reason": exc.reason},
                )
            return self._loaded

    def invalidate(self) -> None:
        self._loaded = None
        self._failed.clear()
