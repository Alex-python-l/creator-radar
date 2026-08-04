from datetime import datetime

from sqlmodel import Session, select

from app.models import JobRun


def start(session: Session, job_type: str) -> int:
    obj = JobRun(job_type=job_type, status="running")
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj.id


def finish(session: Session, job_id: int, status: str, summary: str) -> None:
    obj = session.get(JobRun, job_id)
    if obj is None:
        return
    obj.finished_at = datetime.utcnow()
    obj.status = status
    obj.summary = summary
    session.add(obj)
    session.commit()


def recent(session: Session, limit: int = 20) -> list[dict]:
    stmt = select(JobRun).order_by(JobRun.id.desc()).limit(limit)
    rows = session.exec(stmt).all()
    return [r.model_dump() for r in rows]
