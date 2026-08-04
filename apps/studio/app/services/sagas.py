"""Saga view + explicit Saga driver classes.

Phase 1-6 landed five layers of long-flow plumbing (PhoneLease,
PlatformAdapter, CallbackInbox, Outbox, CommentSource). Each layer
updates state on its own slice — phone_sessions for the lease,
publication.result for the business state, callback_inbox for the
webhook lifecycle, etc. Two things were missing:

  Phase 7a (this file, read side): a unified view that walks
  task_runs and shapes each in-flight attempt into a SagaView
  dataclass with deadlines and stuck detection. Ops endpoints
  (/api/ops/sagas/*) consume it.

  Phase 7b (this file, driver side): explicit Saga classes that
  describe the step sequence each long flow walks through. The
  classes don't reimplement the heavy lifting (still in
  posting_service / warmup_service / engagement_service); they
  document the expected step sequence and give operators a single
  place to look up "what step is the publish saga supposed to be
  on now?" without grepping across three services.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session


# Terminal states a saga never advances out of. Used to compute
# is_terminal and to filter list_in_flight_sagas.
_TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE", "TIMEOUT", "CANCELLED"})


# Step-level budget table. A saga that's been at a given state longer
# than its budget is flagged is_step_stuck even when the overall lease
# deadline hasn't passed yet — overdue_session_watchdog already covers
# the lease-deadline case, this catches "one step took way longer than
# it should" silent stalls.
#
# Budgets are generous (3-5x the expected p99) so a normal pipeline
# never fires false positives. Tune from observed prod p99 over time.
STEP_BUDGETS_SECONDS = {
    "PHONE_STARTING":       2 * 60,     # GeeLark phone boot
    "UPLOADING_RESOURCE":   5 * 60,     # video upload to GeeLark
    "PREPARING_COMMENT":    3 * 60,     # Gemini call(s) for studio_ai
    "SUBMITTED_TO_GEELARK": 15 * 60,    # RPA actually runs on the phone
    "VERIFYING":            2 * 60,     # post-terminal screenshot fetch
}


@dataclass(frozen=True)
class SagaView:
    """A read-model row representing one in-flight or recently-finished
    saga attempt.

    `kind` mirrors task_runs.kind: 'warmup' | 'publish' | 'engagement'.
    `business_ref` is f'{ref_type}:{ref_id}', the same key the outbox
    and inbox layers use so operators can correlate everything in one
    grep.
    """
    saga_id: int
    kind: str
    business_ref: str
    account_id: int
    phone_session_id: Optional[int]
    geelark_task_id: Optional[str]
    current_step: str
    attempt: int
    planned_at: datetime
    started_at: Optional[datetime]
    updated_at: datetime
    finished_at: Optional[datetime]
    deadline_at: Optional[datetime]
    elapsed_seconds: int
    seconds_at_current_step: int
    step_budget_seconds: Optional[int]
    budget_remaining_seconds: Optional[int]
    # is_stuck:      total lease past deadline_at (PhoneLease level)
    # is_step_stuck: one step has run longer than STEP_BUDGETS_SECONDS
    #                (saga level). The two can disagree; either one is
    #                reason for ops to look.
    is_stuck: bool
    is_step_stuck: bool
    is_terminal: bool
    failure_reason: Optional[str]

    def to_jsonable(self) -> dict:
        out = asdict(self)
        for key in (
            "planned_at", "started_at", "updated_at",
            "finished_at", "deadline_at",
        ):
            val = out.get(key)
            if isinstance(val, datetime):
                out[key] = val.isoformat() + "Z"
        return out


_SELECT_SAGA = (
    "SELECT id, kind, ref_type, ref_id, account_id, phone_session_id, "
    "       geelark_task_id, state, attempt, planned_at, started_at, "
    "       updated_at, finished_at, deadline_at, failure_reason "
    "FROM task_runs "
)


def _build_view(row, *, now: datetime) -> SagaView:
    started_at = row.started_at or row.planned_at
    elapsed = int((now - started_at).total_seconds()) if started_at else 0
    if row.deadline_at is not None:
        budget_remaining = int((row.deadline_at - now).total_seconds())
    else:
        budget_remaining = None
    is_terminal = row.state in _TERMINAL_STATES or row.finished_at is not None
    is_stuck = (
        not is_terminal
        and row.deadline_at is not None
        and now > row.deadline_at
    )
    # Step-level stuck detection: how long has this saga been at its
    # current state (== task_runs.updated_at)? Compare against
    # STEP_BUDGETS_SECONDS for the state.
    step_budget = STEP_BUDGETS_SECONDS.get(row.state)
    seconds_at_step = int((now - row.updated_at).total_seconds()) if row.updated_at else 0
    seconds_at_step = max(seconds_at_step, 0)
    is_step_stuck = (
        not is_terminal
        and step_budget is not None
        and seconds_at_step > step_budget
    )
    return SagaView(
        saga_id=row.id,
        kind=row.kind,
        business_ref=f"{row.ref_type}:{row.ref_id}",
        account_id=row.account_id,
        phone_session_id=row.phone_session_id,
        geelark_task_id=row.geelark_task_id,
        current_step=row.state,
        attempt=row.attempt,
        planned_at=row.planned_at,
        started_at=row.started_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
        deadline_at=row.deadline_at,
        elapsed_seconds=max(elapsed, 0),
        seconds_at_current_step=seconds_at_step,
        step_budget_seconds=step_budget,
        budget_remaining_seconds=budget_remaining,
        is_stuck=is_stuck,
        is_step_stuck=is_step_stuck,
        is_terminal=is_terminal,
        failure_reason=row.failure_reason,
    )


def list_in_flight_sagas(
        session: Session, *,
        kind: Optional[str] = None,
        limit: int = 500,
        now: Optional[datetime] = None) -> list[SagaView]:
    """All sagas that have not finished yet (finished_at IS NULL).
    Sorted by deadline_at ASC so the closest-to-stuck appear first;
    NULL deadlines (manual holds, external sessions) sort last.
    """
    eff_now = now or datetime.utcnow()
    sql = _SELECT_SAGA + "WHERE finished_at IS NULL "
    params: dict = {"limit": limit}
    if kind is not None:
        sql += "AND kind = :kind "
        params["kind"] = kind
    sql += (
        "ORDER BY (deadline_at IS NULL), deadline_at, id "
        "LIMIT :limit"
    )
    rows = session.execute(text(sql), params).fetchall()
    return [_build_view(r, now=eff_now) for r in rows]


def describe_saga(
        session: Session, *,
        kind: str, business_ref: str,
        now: Optional[datetime] = None) -> Optional[SagaView]:
    """Lookup the most recent attempt for (kind, business_ref).
    Returns None when nothing matches."""
    eff_now = now or datetime.utcnow()
    try:
        ref_type, ref_id_str = business_ref.split(":", 1)
        ref_id = int(ref_id_str)
    except (ValueError, AttributeError):
        return None
    row = session.execute(
        text(
            _SELECT_SAGA
            + "WHERE kind = :kind AND ref_type = :ref_type "
            "  AND ref_id = :ref_id "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"kind": kind, "ref_type": ref_type, "ref_id": ref_id},
    ).first()
    if row is None:
        return None
    return _build_view(row, now=eff_now)


def count_in_flight_by_kind(session: Session) -> dict[str, int]:
    """Quick summary for the ops dashboard header."""
    rows = session.execute(
        text(
            "SELECT kind, count(*) AS n "
            "FROM task_runs WHERE finished_at IS NULL "
            "GROUP BY kind"
        ),
    ).fetchall()
    return {r.kind: int(r.n) for r in rows}


def count_stuck(session: Session, *,
                 now: Optional[datetime] = None) -> int:
    """In-flight sagas past their lease deadline. The PhoneLease-level
    "is anything wedged" signal."""
    eff_now = now or datetime.utcnow()
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM task_runs "
                "WHERE finished_at IS NULL "
                "  AND deadline_at IS NOT NULL "
                "  AND deadline_at < :now"
            ),
            {"now": eff_now},
        ).scalar() or 0
    )


def count_step_stuck(session: Session, *,
                     now: Optional[datetime] = None) -> int:
    """In-flight sagas whose current step has run longer than its
    STEP_BUDGETS_SECONDS allowance. Distinct from count_stuck — a
    saga can be step-stuck while its overall lease still has time."""
    eff_now = now or datetime.utcnow()
    if not STEP_BUDGETS_SECONDS:
        return 0
    # Build a CASE/WHERE that lights up when (now - updated_at) >
    # the budget for that state. Conservative: NULL states never count.
    conditions = []
    params: dict = {}
    for i, (state, budget) in enumerate(STEP_BUDGETS_SECONDS.items()):
        key_state = f"state_{i}"
        key_budget = f"budget_{i}"
        params[key_state] = state
        params[key_budget] = budget
        conditions.append(
            f"(state = :{key_state} AND "
            f"EXTRACT(EPOCH FROM (:now - updated_at)) > :{key_budget})"
        )
    params["now"] = eff_now
    sql = (
        "SELECT count(*) FROM task_runs "
        "WHERE finished_at IS NULL AND ("
        + " OR ".join(conditions) +
        ")"
    )
    return int(session.execute(text(sql), params).scalar() or 0)


def list_step_stuck_sagas(
        session: Session, *,
        limit: int = 100,
        now: Optional[datetime] = None) -> list[SagaView]:
    """The actual rows behind count_step_stuck. Used by the periodic
    logger to emit one warning per stuck saga."""
    views = list_in_flight_sagas(session, limit=limit, now=now)
    return [v for v in views if v.is_step_stuck]


# ===========================================================================
# Saga driver classes
# ===========================================================================
# Each driver class is a thin descriptor: it names the step sequence,
# exposes the current step from task_runs.state, and provides one
# helper (advance_step) so the per-service helpers can keep their
# imperative shape while the saga concept stays explicit.
#
# The heavy lifting (lease acquisition, GeeLark calls, finalize) stays
# in posting_service / warmup_service / engagement_service. These
# classes are the documentation + accessor layer that turns those
# functions into a saga story an operator can read off the ops dash.


@dataclass(frozen=True)
class SagaStepInfo:
    """One step in a saga's expected sequence."""
    name: str
    description: str
    is_terminal: bool = False


class PublishSaga:
    """The saga driven by publish_publication_task →
    submit_publication_to_geelark → check_publication_status_task chain.

    Expected step sequence:

      PLANNED
        (set when a publication is enqueued but lease not yet acquired;
         currently unused — start_session jumps straight to PHONE_STARTING)
      PHONE_STARTING
        (phone_runtime.start_session)
      UPLOADING_RESOURCE
        (client.upload_file — slowest single op, surfaced as own step)
      SUBMITTED_TO_GEELARK
        (PlatformAdapter.submit_publish returned a task_id; webhook /
         polling will report status)
      VERIFYING
        (got terminal-looking GeeLark status; running
         adapter.verify_real for platform-specific confirmation —
         notably YouTube audit_screenshot)
      SUCCESS / FAILURE / TIMEOUT
        (terminal)
    """

    kind = "publish"

    STEPS: tuple[SagaStepInfo, ...] = (
        SagaStepInfo("PLANNED",
                     "Publication enqueued, lease not yet acquired"),
        SagaStepInfo("PHONE_STARTING",
                     "Lease acquired, waiting on GeeLark phone start"),
        SagaStepInfo("UPLOADING_RESOURCE",
                     "Uploading the video file to GeeLark"),
        SagaStepInfo("SUBMITTED_TO_GEELARK",
                     "GeeLark RPA task created, awaiting completion"),
        SagaStepInfo("VERIFYING",
                     "Confirming platform reported success is real"),
        SagaStepInfo("SUCCESS", "Done", is_terminal=True),
        SagaStepInfo("FAILURE", "Done", is_terminal=True),
        SagaStepInfo("TIMEOUT", "Done", is_terminal=True),
        SagaStepInfo("CANCELLED", "Done", is_terminal=True),
    )

    def __init__(self, publication_id: int):
        self.publication_id = publication_id

    @property
    def business_ref(self) -> str:
        return f"publication:{self.publication_id}"

    def current_step(self, session: Session) -> Optional[str]:
        """The state of the most recent task_run attempt for this
        publication, or None if no attempt has been recorded yet."""
        view = describe_saga(
            session, kind=self.kind, business_ref=self.business_ref,
        )
        return view.current_step if view is not None else None

    def view(self, session: Session) -> Optional[SagaView]:
        """Convenience: full SagaView for ops dashboards / tests."""
        return describe_saga(
            session, kind=self.kind, business_ref=self.business_ref,
        )

    @classmethod
    def step_names(cls) -> tuple[str, ...]:
        return tuple(s.name for s in cls.STEPS)


class WarmupSaga:
    """The saga driven by start_warmup_task → check_warmup_status_task
    chain.

    Expected step sequence:

      PLANNED
        (reserved — start_session jumps straight to PHONE_STARTING)
      PHONE_STARTING
        (phone_runtime.start_session — lease acquired,
         client.start_phones called)
      SUBMITTED_TO_GEELARK
        (client.create_warmup_task returned a task_id;
         attach_geelark_task fired)
      SUCCESS / FAILURE / TIMEOUT
        (terminal)

    Warmup has no UPLOADING_RESOURCE (no asset to upload) and no
    VERIFYING (GeeLark status is trusted — no platform-side
    audit). The step list is intentionally shorter than publish.
    """

    kind = "warmup"

    STEPS: tuple[SagaStepInfo, ...] = (
        SagaStepInfo("PLANNED",
                     "Warmup enqueued, lease not yet acquired"),
        SagaStepInfo("PHONE_STARTING",
                     "Lease acquired, GeeLark phone starting"),
        SagaStepInfo("SUBMITTED_TO_GEELARK",
                     "GeeLark warmup RPA task created, awaiting completion"),
        SagaStepInfo("SUCCESS", "Done", is_terminal=True),
        SagaStepInfo("FAILURE", "Done", is_terminal=True),
        SagaStepInfo("TIMEOUT", "Done", is_terminal=True),
        SagaStepInfo("CANCELLED", "Done", is_terminal=True),
    )

    def __init__(self, account_id: int):
        self.account_id = account_id

    @property
    def business_ref(self) -> str:
        return f"account:{self.account_id}"

    def current_step(self, session: Session) -> Optional[str]:
        view = describe_saga(
            session, kind=self.kind, business_ref=self.business_ref,
        )
        return view.current_step if view is not None else None

    def view(self, session: Session) -> Optional[SagaView]:
        return describe_saga(
            session, kind=self.kind, business_ref=self.business_ref,
        )

    @classmethod
    def step_names(cls) -> tuple[str, ...]:
        return tuple(s.name for s in cls.STEPS)


class EngagementSaga:
    """The saga driven by submit_engagement_run_task →
    check_engagement_run_task chain.

    Expected step sequence:

      PLANNED
        (reserved)
      PHONE_STARTING
        (phone_runtime.start_session — lease acquired)
      PREPARING_COMMENT
        (Studio-AI comment generation OR Facebook candidate prep;
         Gemini calls happen here)
      SUBMITTED_TO_GEELARK
        (PlatformAdapter.submit_engagement returned a task_id)
      VERIFYING
        (terminal-looking GeeLark status; follow-up screenshot /
         detail fetch in progress)
      SUCCESS / FAILURE / TIMEOUT
        (terminal)

    Engagement has no UPLOADING_RESOURCE (no video upload). The
    PREPARING_COMMENT step is engagement-specific: a stuck Gemini
    call is the most common failure mode and warrants its own
    observable step.
    """

    kind = "engagement"

    STEPS: tuple[SagaStepInfo, ...] = (
        SagaStepInfo("PLANNED",
                     "Engagement run enqueued, lease not yet acquired"),
        SagaStepInfo("PHONE_STARTING",
                     "Lease acquired, GeeLark phone starting"),
        SagaStepInfo("PREPARING_COMMENT",
                     "Generating comment text / FB candidates via Gemini"),
        SagaStepInfo("SUBMITTED_TO_GEELARK",
                     "GeeLark engagement RPA task created, awaiting completion"),
        SagaStepInfo("VERIFYING",
                     "Fetching follow-up GeeLark task detail / screenshot"),
        SagaStepInfo("SUCCESS", "Done", is_terminal=True),
        SagaStepInfo("FAILURE", "Done", is_terminal=True),
        SagaStepInfo("TIMEOUT", "Done", is_terminal=True),
        SagaStepInfo("CANCELLED", "Done", is_terminal=True),
    )

    def __init__(self, run_id: int):
        self.run_id = run_id

    @property
    def business_ref(self) -> str:
        return f"engagement_run:{self.run_id}"

    def current_step(self, session: Session) -> Optional[str]:
        view = describe_saga(
            session, kind=self.kind, business_ref=self.business_ref,
        )
        return view.current_step if view is not None else None

    def view(self, session: Session) -> Optional[SagaView]:
        return describe_saga(
            session, kind=self.kind, business_ref=self.business_ref,
        )

    @classmethod
    def step_names(cls) -> tuple[str, ...]:
        return tuple(s.name for s in cls.STEPS)


# Registry of saga descriptors. New saga = add the class + register
# here.
_SAGA_DESCRIPTORS: dict[str, type] = {
    PublishSaga.kind: PublishSaga,
    WarmupSaga.kind: WarmupSaga,
    EngagementSaga.kind: EngagementSaga,
}


def get_saga_descriptor(kind: str) -> Optional[type]:
    return _SAGA_DESCRIPTORS.get(kind)


def all_saga_descriptors() -> dict[str, type]:
    return dict(_SAGA_DESCRIPTORS)
