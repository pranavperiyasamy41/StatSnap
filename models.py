from __future__ import annotations
import datetime as dt
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base

def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    students: Mapped[list["Student"]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    cf_handle: Mapped[str | None] = mapped_column(String, nullable=True)
    cc_handle: Mapped[str | None] = mapped_column(String, nullable=True)
    lc_handle: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    owner: Mapped["User"] = relationship(back_populates="students")
    contest_results: Mapped[list["ContestResult"]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

class ContestResult(Base):
    __tablename__ = "contest_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String, nullable=False, index=True)
    contest_name: Mapped[str] = mapped_column(String, nullable=False)
    contest_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problems_solved: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    student: Mapped["Student"] = relationship(back_populates="contest_results")
