from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Any, Dict, List

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from database import Base, engine, get_db
from models import ContestResult, Student
from pdf_generator import generate_pdf
from scrapers.codechef import fetch_codechef
from scrapers.codeforces import fetch_codeforces
from scrapers.common import PlatformFetchError
from scrapers.leetcode import fetch_leetcode


# Create DB tables on startup (simple project, no migrations yet).
Base.metadata.create_all(bind=engine)

app = FastAPI(title="StatSnap")

app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")
templates = Jinja2Templates(directory="templates")


def _latest_rating_for_platform(db: Session, student_id: int, platform: str) -> int | None:
    stmt = (
        select(ContestResult.rating)
        .where(
            and_(
                ContestResult.student_id == student_id,
                ContestResult.platform == platform,
                ContestResult.rating.isnot(None),
            )
        )
        .order_by(desc(ContestResult.contest_date), desc(ContestResult.id))
        .limit(1)
    )
    row = db.execute(stmt).first()
    return int(row[0]) if row and row[0] is not None else None


def _sanitize_filename(name: str) -> str:
    s = re.sub(r"[^\w\-\. ]+", "_", name).strip()
    return s or "report"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    students: List[Student] = db.execute(select(Student).order_by(Student.created_at)).scalars().all()

    cards: List[Dict[str, Any]] = []
    for s in students:
        cards.append(
            {
                "student": s,
                "cf_rating": _latest_rating_for_platform(db, s.id, "codeforces"),
                "cc_rating": _latest_rating_for_platform(db, s.id, "codechef"),
                "lc_rating": _latest_rating_for_platform(db, s.id, "leetcode"),
            }
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "students": cards,
            "show_fab": True,
        },
    )


@app.post("/student/add")
async def add_student(
    name: str = Form(...),
    cf_handle: str = Form(...),
    cc_handle: str = Form(...),
    lc_handle: str = Form(...),
    db: Session = Depends(get_db),
):
    name = name.strip()
    cf_handle = cf_handle.strip()
    cc_handle = cc_handle.strip()
    lc_handle = lc_handle.strip()

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not cf_handle or not cc_handle or not lc_handle:
        raise HTTPException(status_code=400, detail="All handles are required")

    student = Student(
        name=name,
        cf_handle=cf_handle,
        cc_handle=cc_handle,
        lc_handle=lc_handle,
    )
    db.add(student)
    db.commit()
    db.refresh(student)

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/student/{student_id}/delete")
async def delete_student(student_id: int, db: Session = Depends(get_db)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    db.delete(student)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/student/{student_id}/edit", response_class=HTMLResponse)
async def edit_student_form(request: Request, student_id: int, db: Session = Depends(get_db)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    return templates.TemplateResponse(
        "edit_student.html",
        {
            "request": request,
            "student": student,
        },
    )


@app.post("/student/{student_id}/edit")
async def edit_student(
    student_id: int,
    name: str = Form(...),
    cf_handle: str = Form(...),
    cc_handle: str = Form(...),
    lc_handle: str = Form(...),
    db: Session = Depends(get_db),
):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    student.name = name.strip()
    student.cf_handle = cf_handle.strip()
    student.cc_handle = cc_handle.strip()
    student.lc_handle = lc_handle.strip()

    if not student.name:
        raise HTTPException(status_code=400, detail="Name is required")

    db.commit()

    return RedirectResponse(
        url=f"/student/{student.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/student/{student_id}", response_class=HTMLResponse)
async def student_dashboard(
    request: Request,
    student_id: int,
    db: Session = Depends(get_db),
    cf_status: str | None = None,
    cc_status: str | None = None,
    lc_status: str | None = None,
):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    results = (
        db.execute(
            select(ContestResult)
            .where(ContestResult.student_id == student.id)
            .order_by(ContestResult.platform, ContestResult.contest_date, ContestResult.id)
        )
        .scalars()
        .all()
    )

    grouped: Dict[str, List[ContestResult]] = {"codeforces": [], "codechef": [], "leetcode": []}
    for r in results:
        key = (r.platform or "").lower()
        if key in grouped:
            grouped[key].append(r)

    platform_status: Dict[str, str | None] = {
        "codeforces": cf_status,
        "codechef": cc_status,
        "leetcode": lc_status,
    }

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "student": student,
            "results": grouped,
            "platform_status": platform_status,
        },
    )


@app.get("/student/{student_id}/preview", response_class=HTMLResponse)
async def student_report_preview(
    request: Request,
    student_id: int,
    db: Session = Depends(get_db),
):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    results = (
        db.execute(
            select(ContestResult)
            .where(ContestResult.student_id == student.id)
        )
        .scalars()
        .all()
    )

    total_contests = len(results)
    total_problems = sum(r.problems_solved or 0 for r in results)
    peak_rating = max((r.rating for r in results if r.rating is not None), default=0)

    summary = {
        "total_contests": total_contests,
        "total_problems": total_problems,
        "peak_rating": peak_rating,
    }

    return templates.TemplateResponse(
        "report_preview.html",
        {
            "request": request,
            "student": student,
            "summary": summary,
            "today": dt.date.today().isoformat(),
        },
    )


@app.post("/student/{student_id}/sync")
async def sync_student(student_id: int, db: Session = Depends(get_db)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    cf_error: str | None = None
    cc_error: str | None = None
    lc_error: str | None = None

    tasks = []
    if student.cf_handle:
        tasks.append(("codeforces", fetch_codeforces(student.cf_handle)))
    if student.cc_handle:
        tasks.append(("codechef", fetch_codechef(student.cc_handle)))
    if student.lc_handle:
        tasks.append(("leetcode", fetch_leetcode(student.lc_handle)))

    results_by_platform: Dict[str, List[Dict[str, Any]]] = {}

    if tasks:
        coros = [coro for _, coro in tasks]
        platforms = [name for name, _ in tasks]
        fetched = await asyncio.gather(*coros, return_exceptions=True)

        for platform, value in zip(platforms, fetched, strict=False):
            if isinstance(value, PlatformFetchError):
                msg = value.message
            elif isinstance(value, Exception):
                msg = "Could not fetch data — try syncing again"
            else:
                msg = None

            if msg:
                if platform == "codeforces":
                    cf_error = msg
                elif platform == "codechef":
                    cc_error = msg
                elif platform == "leetcode":
                    lc_error = msg
            else:
                results_by_platform[platform] = value  # type: ignore[assignment]

    # Atomically replace existing results for platforms we successfully fetched.
    # This ensures we don't end up with partial data if one platform fails mid-sync.
    try:
        for platform, rows in results_by_platform.items():
            db.query(ContestResult).filter(
                ContestResult.student_id == student.id,
                ContestResult.platform == platform,
            ).delete(synchronize_session=False)

            for row in rows:
                db.add(
                    ContestResult(
                        student_id=student.id,
                        platform=platform,
                        contest_name=row["contest_name"],
                        contest_date=row.get("contest_date"),
                        rating=row.get("rating"),
                        problems_solved=row.get("problems_solved"),
                        fetched_at=dt.datetime.now(dt.timezone.utc),
                    )
                )
        db.commit()
    except Exception:
        db.rollback()
        # You could log the error here if you had a logger
        # For now, we'll just fall back to showing the status errors

    params = []
    if cf_error:
        params.append(f"cf_status={cf_error}")
    if cc_error:
        params.append(f"cc_status={cc_error}")
    if lc_error:
        params.append(f"lc_status={lc_error}")
    query = ("?" + "&".join(params)) if params else ""

    return RedirectResponse(
        url=f"/student/{student.id}{query}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/student/{student_id}/report")
async def download_report(student_id: int, db: Session = Depends(get_db)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    results = (
        db.execute(
            select(ContestResult)
            .where(ContestResult.student_id == student.id)
            .order_by(ContestResult.contest_date, ContestResult.id)
        )
        .scalars()
        .all()
    )

    pdf_bytes = generate_pdf(student, results)
    filename = f"{_sanitize_filename(student.name)}_cp_report.pdf"

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/api/student/{student_id}/data")
async def student_data(student_id: int, db: Session = Depends(get_db)):
    student = db.get(Student, student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    results = (
        db.execute(
            select(ContestResult)
            .where(ContestResult.student_id == student.id)
            .order_by(ContestResult.platform, ContestResult.contest_date, ContestResult.id)
        )
        .scalars()
        .all()
    )

    data: Dict[str, List[Dict[str, Any]]] = {"codeforces": [], "codechef": [], "leetcode": []}
    for r in results:
        platform = (r.platform or "").lower()
        if platform not in data:
            continue
        data[platform].append(
            {
                "contest_name": r.contest_name,
                "contest_date": r.contest_date.isoformat() if r.contest_date else None,
                "rating": r.rating,
                "problems_solved": r.problems_solved,
            }
        )

    return JSONResponse({"student_id": student.id, "platforms": data})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

