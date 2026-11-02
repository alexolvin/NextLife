"""Routes for background job indicators and batch reports."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from nextlife.api.routes import check_auth, templates
from nextlife.jobs.manager import get_active_jobs, get_active_jobs_count, get_batch_summary
from nextlife.storage.db import get_db_session
from nextlife.storage.models import Job

router = APIRouter()


@router.get("/ui/jobs/{batch_id}", response_class=HTMLResponse)
@router.get("/ui/jobs/batches/{batch_id}", response_class=HTMLResponse)
def view_job_batch(
    request: Request,
    batch_id: str,
    _auth: bool = Depends(check_auth),
) -> HTMLResponse:
    """Render batch report page."""
    with get_db_session() as session:
        summary = get_batch_summary(session, batch_id)
        if not summary.get("jobs"):
            raise HTTPException(status_code=404, detail="Batch not found")

        return templates.TemplateResponse(
            request=request,
            name="job_batch.html",
            context={
                "batch_id": batch_id,
                "summary": summary,
                "items": summary.get("items", []),
            },
        )


@router.get("/api/jobs/active")
def api_active_jobs(
    _auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Return count and list of active jobs for header indicator."""
    with get_db_session() as session:
        count = get_active_jobs_count(session)
        jobs = get_active_jobs(session, limit=10)
        return {"count": count, "active": count > 0, "jobs": jobs}


@router.get("/api/jobs/batches/{batch_id}")
def api_job_batch(
    batch_id: str,
    _auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Return JSON details for a batch (for drawer/live refresh)."""
    with get_db_session() as session:
        summary = get_batch_summary(session, batch_id)
        if not summary.get("jobs"):
            raise HTTPException(status_code=404, detail="Batch not found")
        return summary


@router.post("/api/jobs/{job_id}/retry")
def api_retry_job(
    job_id: str,
    _auth: bool = Depends(check_auth),
) -> dict[str, Any]:
    """Retry a failed job manually."""
    with get_db_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job.state = "QUEUED"
        job.attempts = 0
        job.last_error = None
        session.commit()
        return {"status": "ok", "job_id": job.id, "state": job.state}
