"""Pydantic envelope models for the JS↔Python bridge (CONTRACTS §5.3).

The wire format is fixed and small: an outer ``BridgeRequest`` carrying a
command name, request id and free-form payload; a matching ``BridgeResponse``
whose ``status`` discriminates ok / accepted / error. Payload size is capped
by the facade (CONTRACTS §5.4); field-level caps live in per-command payload
models. Extra fields are forbidden everywhere — SECURITY §3.

M1-T6 freezes the three envelope models as canonical contracts, so they are
imported from :mod:`data_profiling_engine.schemas.models` (validated against
``schemas/bridge_*.schema.json``) rather than redefined here. The per-command
payload models below are the **request-payload shapes** of the §5.2 command
set: the envelope schema keeps ``payload`` free-form (the command name picks
its model), so these pydantic classes are the single validation gate each
handler passes through (SECURITY §3). ``echo`` / worker-trio stand-ins are
spike-only and not part of §5.2.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Canonical §5.3 envelope models (single source of truth: schemas/models.py).
from data_profiling_engine.schemas.models import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
)

__all__ = [
    "BridgeRequest",
    "BridgeErrorObject",
    "BridgeResponse",
    "EchoPayload",
    "WorkerEchoJobPayload",
    "JobIdPayload",
    "ProfileStartPayload",
    "SOURCE_PATH_MAX_LENGTH",
    "PREVIEW_ROW_CAP",
    "PagedResultPayload",
    "DatasetInspectPayload",
    "DatasetPreviewPayload",
    "QualityGetResultsPayload",
    "FindingsGetPayload",
    "ReportSummaryPayload",
    "ArtifactOpenPayload",
]


class EchoPayload(BaseModel):
    """Payload schema for the ``echo`` spike command (M0-T2 only).

    ``echo`` is **not** part of the MVP command set in CONTRACTS §5.2 — it
    exists solely to prove the typed-facade pathway on both OSes.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=0)


class WorkerEchoJobPayload(BaseModel):
    """Payload for the ``worker_echo_job`` spike command (M0-T3 only).

    Submits a bounded echo job to the worker process; spike stand-in for the
    async ``profile.start`` shape of CONTRACTS §5.2 (accepted + ``job_id``).
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=0, max_length=8192)
    total_steps: int = Field(default=5, ge=1, le=200)


class JobIdPayload(BaseModel):
    """Payload for ``job.get_status`` / ``job.cancel`` spike commands (M0-T3),
    reused by the real ``profile.get_status`` / ``profile.cancel`` (§5.2)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)


class ProfileStartPayload(BaseModel):
    """Payload for the real §5.2 ``profile.start`` command (M5-T2).

    Carries everything the worker needs to run one job: the source path, the
    §9 config **document** (validated against ``dataset_config.schema.json`` +
    the pydantic model by the handler before dispatch — AGENTS §5), and the
    optional ``profile_mode`` override / ``run_id`` / path ``allowed_roots``
    allow-list. Only the config *document* (a bounded JSON object) crosses the
    bridge, never a result; the whole envelope still obeys the 1 MiB cap (§5.4)."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=128)
    source_path: str = Field(min_length=1, max_length=4096)
    config: Dict[str, Any]
    profile_mode: Optional[str] = Field(default=None, max_length=16)
    run_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    allowed_roots: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# §5.2 read commands (M5-T1). Every model is extra="forbid" (SECURITY §3) and
# carries its own bounded page size, so a well-formed request can never ask for
# more than §5.4 allows; the facade additionally caps every *response*.
# ---------------------------------------------------------------------------

#: Same path bound as :attr:`ProfileStartPayload.source_path` — one length
#: ceiling for every caller-supplied filesystem path.
SOURCE_PATH_MAX_LENGTH = 4096

#: CONTRACTS §5.4: "preview <= 200 rows" — a hard row cap, not a suggestion.
PREVIEW_ROW_CAP = 200


class PagedResultPayload(BaseModel):
    """Shared paging block for the paged result fetches (``quality.get_results``
    / ``findings.get``). ``limit`` bounds the page *length*; whether the serialized
    page fits §5.4 is verified per response by the facade, because one row's
    size is data-dependent (facts/hypotheses are free text)."""

    model_config = ConfigDict(extra="forbid")

    run_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    dataset_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _one_source(self) -> "PagedResultPayload":
        # A page must address exactly one thing: a run, or the latest run of a
        # dataset. Neither → nothing to fetch; both → an ambiguous request.
        if self.run_id is None and self.dataset_id is None:
            raise ValueError("run_id or dataset_id is required")
        return self


class DatasetInspectPayload(BaseModel):
    """§5.2 ``dataset.inspect`` — schema/types/size metadata for one source.

    ``source_path`` is always allow-list checked (SECURITY §3); ``dataset_id``
    labels the columns with a stored run's logical/semantic types when given
    (or when the dataset has run before)."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1, max_length=SOURCE_PATH_MAX_LENGTH)
    dataset_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    run_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    allowed_roots: Optional[List[str]] = None


class DatasetPreviewPayload(BaseModel):
    """§5.2 ``dataset.preview`` — a bounded page of raw rows.

    ``limit`` is clamped to :data:`PREVIEW_ROW_CAP` by the field constraint
    itself (§5.4), so the largest admissible request is 200 rows; ``mask_pii``
    defaults to ``True`` (SECURITY §4: full values only via the JSON artifact
    and ``artifact.open``, never through the bridge)."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(min_length=1, max_length=SOURCE_PATH_MAX_LENGTH)
    dataset_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    run_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    allowed_roots: Optional[List[str]] = None
    limit: int = Field(default=50, ge=1, le=PREVIEW_ROW_CAP)
    offset: int = Field(default=0, ge=0)
    mask_pii: bool = True


class QualityGetResultsPayload(PagedResultPayload):
    """§5.2 ``quality.get_results`` — paged §4.1 check results.

    ``status``/``dimension`` narrow the page; the handler checks both against
    the §4.1 vocabularies, so a typo'd filter is refused as an invalid request
    instead of silently matching nothing."""

    status: Optional[str] = Field(default=None, max_length=16)
    dimension: Optional[str] = Field(default=None, max_length=32)


class FindingsGetPayload(PagedResultPayload):
    """§5.2 ``findings.get`` — paged §4.2 findings."""

    severity: Optional[str] = Field(default=None, max_length=16)
    category: Optional[str] = Field(default=None, max_length=32)


class ReportSummaryPayload(BaseModel):
    """§5.2 ``report.get_summary`` — the §2 run record plus the §3.1 scalar
    rollup and tally counts. One run, addressed directly or by dataset."""

    model_config = ConfigDict(extra="forbid")

    run_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    dataset_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _one_source(self) -> "ReportSummaryPayload":
        if self.run_id is None and self.dataset_id is None:
            raise ValueError("run_id or dataset_id is required")
        return self


class ArtifactOpenPayload(BaseModel):
    """§5.2 ``artifact.open`` — an allow-listed artifact ref, never a path.

    SECURITY §3/§4: the ref is resolvable *only* through the run's
    ``run_artifacts`` rows (``kind``, or an exact store-relative ``ref`` the
    store already records), so this command can reach nothing the artifact
    store does not own — an arbitrary or crafted path is not a valid request.
    ``ref`` and ``kind`` are mutually exclusive."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    kind: Optional[str] = Field(default=None, min_length=1, max_length=64)
    ref: Optional[str] = Field(default=None, min_length=1, max_length=1024)
    reveal: bool = False

    @model_validator(mode="after")
    def _exactly_one_locator(self) -> "ArtifactOpenPayload":
        if (self.kind is None) == (self.ref is None):
            raise ValueError("exactly one of kind or ref is required")
        return self
