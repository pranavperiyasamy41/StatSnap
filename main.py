from __future__ import annotations
import asyncio
import datetime as dt
import re
import traceback
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

from database import Base, engine, get_db, SessionLocal
import models
import auth
from pdf_generator import generate_pdf
from scrapers.codechef import fetch_codechef
from scrapers.codeforces import fetch_codeforces
from scrapers.leetcode import fetch_leetcode

# Create DB tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="StatSnap")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print("--- GLOBAL ERROR TRACEBACK ---")
    traceback.print_exc()
    return HTMLResponse(
        content=f"<h1>Internal Server Error</h1><p>Something went wrong. Please try again or refresh the page.</p><pre style='background:#111;color:#f88;padding:10px;'>{str(exc)}</pre>", 
        status_code=500
    )

app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")
templates = Jinja2Templates(directory="templates")

# Helper to provide user to templates
@app.middleware("http")
async def add_user_to_request(request: Request, call_next):
    db = SessionLocal()
    try:
        # Extra Layer of Safety: If auth fails, just set user to None
        try:
            request.state.user = auth.get_current_user(request, db)
        except Exception:
            request.state.user = None
            
        response = await call_next(request)
        return response
    finally:
        db.close()

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

# --- AUTH ROUTES ---
@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})

@app.post("/signup")
async def signup(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    try:
        db_user = db.query(models.User).filter(models.User.email == email).first()
        if db_user:
            return templates.TemplateResponse("signup.html", {"request": request, "error": "Email already registered."})
        
        hashed_password = auth.get_password_hash(password)
        new_user = models.User(email=email, hashed_password=hashed_password)
        db.add(new_user)
        db.commit()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        db.rollback()
        return templates.TemplateResponse("signup.html", {"request": request, "error": f"Signup error: {str(e)}"})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not auth.verify_password(password, user.hashed_password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials."})
    
    access_token = auth.create_access_token(data={"sub": user.email})
    res = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    res.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    return res

@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login")
    res.delete_cookie("access_token")
    return res

# --- APP ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user: return RedirectResponse(url="/login")
    
    students = db.query(models.Student).filter(models.Student.owner_id == user.id).all()
    cards = []
    for s in students:
        cards.append({
            "student": s,
            "cf_rating": _latest_rating_for_platform(db, s.id, "codeforces"),
            "cc_rating": _latest_rating_for_platform(db, s.id, "codechef"),
            "lc_rating": _latest_rating_for_platform(db, s.id, "leetcode"),
        })
    return templates.TemplateResponse("index.html", {"request": request, "students": cards, "user": user})

@app.post("/student/add")
async def add_student(request: Request, name: str = Form(...), cf_handle: str = Form(""), cc_handle: str = Form(""), lc_handle: str = Form(""), db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user: return RedirectResponse(url="/login")
    
    student = models.Student(name=name, cf_handle=cf_handle, cc_handle=cc_handle, lc_handle=lc_handle, owner_id=user.id)
    db.add(student)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/student/{student_id}", response_class=HTMLResponse)
async def student_dashboard(request: Request, student_id: int, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user: return RedirectResponse(url="/login")
    
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == user.id).first()
    if not student: raise HTTPException(status_code=404)
    
    results = db.query(models.ContestResult).filter(models.ContestResult.student_id == student.id).order_by(models.ContestResult.contest_date).all()
    grouped = {"codeforces": [], "codechef": [], "leetcode": []}
    for r in results:
        key = r.platform.lower()
        if key in grouped: grouped[key].append(r)
        
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "student": student, 
        "results": grouped, 
        "user": user,
        "platform_status": {} 
    })

@app.post("/student/{student_id}/sync")
async def sync_student(request: Request, student_id: int, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if not user: return RedirectResponse(url="/login")
    
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == user.id).first()
    if not student: raise HTTPException(status_code=404)
    
    # Sync Logic
    tasks = []
    if student.cf_handle: tasks.append(("codeforces", fetch_codeforces(student.cf_handle)))
    if student.cc_handle: tasks.append(("codechef", fetch_codechef(student.cc_handle)))
    if student.lc_handle: tasks.append(("leetcode", fetch_leetcode(student.lc_handle)))

    if tasks:
        platforms = [t[0] for t in tasks]
        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)
        
        for platform, data in zip(platforms, results):
            if isinstance(data, Exception): continue
            
            db.query(models.ContestResult).filter(
                models.ContestResult.student_id == student.id, 
                models.ContestResult.platform == platform
            ).delete()
            
            for row in data:
                db.add(models.ContestResult(
                    student_id=student.id,
                    platform=platform,
                    contest_name=row["contest_name"],
                    contest_date=row.get("contest_date"),
                    rating=row.get("rating"),
                    problems_solved=row.get("problems_solved")
                ))
        db.commit()
        
    return RedirectResponse(url=f"/student/{student.id}", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/student/{student_id}/edit", response_class=HTMLResponse)
async def edit_student(request: Request, student_id: int, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == user.id).first()
    return templates.TemplateResponse("edit_student.html", {"request": request, "student": student, "user": user})

@app.post("/student/{student_id}/edit")
async def edit_student_post(request: Request, student_id: int, name: str = Form(...), cf_handle: str = Form(""), cc_handle: str = Form(""), lc_handle: str = Form(""), db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == user.id).first()
    student.name, student.cf_handle, student.cc_handle, student.lc_handle = name, cf_handle, cc_handle, lc_handle
    db.commit()
    return RedirectResponse(url=f"/student/{student.id}", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/student/{student_id}/delete")
async def delete_student(request: Request, student_id: int, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    student = db.query(models.Student).filter(models.Student.id == student_id, models.Student.owner_id == user.id).first()
    db.delete(student)
    db.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/health")
async def health(): return {"status": "ok"}
