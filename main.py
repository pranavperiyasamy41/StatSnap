from __future__ import annotations
import asyncio
import datetime as dt
import re
from typing import Any, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    status,
    Response
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, desc, select
from sqlalchemy.orm import Session

from database import Base, engine, get_db
import models
import auth
from pdf_generator import generate_pdf
from scrapers.codechef import fetch_codechef
from scrapers.codeforces import fetch_codeforces
from scrapers.common import PlatformFetchError
from scrapers.leetcode import fetch_leetcode

# Create DB tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="StatSnap")
app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")
templates = Jinja2Templates(directory="templates")

# Helper to provide user to templates
@app.middleware("http")
async def add_user_to_request(request: Request, call_next):
    db = next(get_db())
    request.state.user = auth.get_current_user(request, db)
    response = await call_next(request)
    return response

def _latest_rating_for_platform(db: Session, student_id: int, platform: str) -> int | None:
    stmt = (
        select(models.ContestResult.rating)
        .where(
            and_(
                models.ContestResult.student_id == student_id,
                models.ContestResult.platform == platform,
                models.ContestResult.rating.isnot(None),
            )
        )
        .order_by(desc(models.ContestResult.contest_date), desc(models.ContestResult.id))
        .limit(1)
    )
    row = db.execute(stmt).first()
    return int(row[0]) if row and row[0] is not None else None

def _sanitize_filename(name: str) -> str:
    s = re.sub(r"[^\w\-\. ]+", "_", name).strip()
    return s or "report"

# --- AUTH ROUTES ---
@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})

@app.post("/signup")
async def signup(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(models.User).filter(models.User.email == email).first()
    if db_user:
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "error": "Email already registered. Try logging in."
        })
    
    # Minimal validation
    if len(password) < 6:
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "error": "Password must be at least 6 characters."
        })

    try:
        hashed_password = auth.get_password_hash(password)
        new_user = models.User(email=email, hashed_password=hashed_password)
        db.add(new_user)
        db.commit()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        db.rollback()
        return templates.TemplateResponse("signup.html", {
            "request": request,
            "error": "An error occurred during signup. Please try again."
        })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == email).first()
    
    if not user or not auth.verify_password(password, user.hashed_password):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid email or password."
        })
    
    access_token = auth.create_access_token(data={"sub": user.email})
    res = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    res.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    return res

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login")
    res.delete_cookie("access_token")
    return res

# --- APP ROUTES (PROTECTED) ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    if not current_user:
        return RedirectResponse(url="/login")
    
    students = db.query(models.Student).filter(models.Student.owner_id == current_user.id).order_by(models.Student.created_at).all()

    cards = []
    for s in students:
        cards.append({
            "student": s,
            "cf_rating": _latest_rating_for_platform(db, s.id, "codeforces"),
            "cc_rating": _latest_rating_for_platform(db, s.id, "codechef"),
            "lc_rating": _latest_rating_for_platform(db, s.id, "leetcode"),
        })

    return templates.TemplateResponse("index.html", {"request": request, "students": cards, "user": current_user})

@app.post("/student/add")
async def add_student(
    name: str = Form(...),
    cf_handle: str = Form(...),
    cc_handle: str = Form(...),
    lc_handle: str = Form(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if not current_user: raise HTTPException(status_code=401)
    
    student = models.Student(
        name=name.strip(),
        cf_handle=cf_handle.strip(),
        cc_handle=cc_handle.strip(),
        lc_handle=lc_handle.strip(),
        owner_id=current_user.id
    )
    db.add(student)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/student/{student_id}/delete")
async def delete_student(student_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == current_user.id).first()
    if not student: raise HTTPException(status_code=404)
    db.delete(student)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/student/{student_id}", response_class=HTMLResponse)
async def student_dashboard(request: Request, student_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == current_user.id).first()
    if not student: raise HTTPException(status_code=404)

    results = db.query(models.ContestResult).filter(models.ContestResult.student_id == student.id).order_by(models.ContestResult.contest_date).all()
    grouped = {"codeforces": [], "codechef": [], "leetcode": []}
    for r in results:
        key = r.platform.lower()
        if key in grouped: grouped[key].append(r)

    return templates.TemplateResponse("dashboard.html", {"request": request, "student": student, "results": grouped, "user": current_user})

@app.get("/health")
async def health():
    return {"status": "ok"}
