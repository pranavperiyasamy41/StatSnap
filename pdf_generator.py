from __future__ import annotations

import datetime as dt
from collections import defaultdict
from io import BytesIO
from typing import Iterable, Protocol, runtime_checkable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


@runtime_checkable
class _StudentLike(Protocol):
    id: int
    name: str


@runtime_checkable
class _ResultLike(Protocol):
    platform: str
    contest_name: str
    contest_date: dt.date | None
    rating: int | None
    problems_solved: int | None


PLATFORM_LABELS = {
    "codeforces": "CODEFORCES",
    "codechef": "CODECHEF",
    "leetcode": "LEETCODE",
}


def _page_footer(canvas, doc) -> None:  # type: ignore[override]
    canvas.saveState()
    width, _ = A4
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawRightString(width - 20 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def generate_pdf(student: _StudentLike, results: Iterable[_ResultLike]) -> bytes:
    """
    Build a CP Performance PDF for a single student.

    The layout follows the requested format:

    [Student Name]
    Generated on: [Date]

    SUMMARY: Total Contests: X  |  Problems Solved: Y  |  Peak Rating: Z

    --- CODEFORCES ---
    | Contest Name | Rating | Problems Solved |
    ...
    """
    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f"{student.name} - StatSnap Performance Report",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleDark",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=20,
        textColor=colors.black,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.grey,
        spaceAfter=12,
    )
    section_header_style = ParagraphStyle(
        "SectionHeader",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=colors.black,
        spaceBefore=12,
        spaceAfter=6,
    )
    summary_style = ParagraphStyle(
        "Summary",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        textColor=colors.black,
        spaceAfter=12,
    )

    story: list[object] = []

    today = dt.datetime.now().date()
    story.append(Paragraph(student.name, title_style))
    story.append(Paragraph(f"Generated on: {today.isoformat()}", subtitle_style))

    # Aggregate summary stats.
    results_list = list(results)
    total_contests = len(results_list)
    total_problems = sum(r.problems_solved or 0 for r in results_list)
    ratings = [r.rating for r in results_list if r.rating is not None]
    peak_rating = max(ratings) if ratings else 0

    summary_text = (
        f"SUMMARY: Total Contests: {total_contests}  |  "
        f"Problems Solved: {total_problems}  |  "
        f"Peak Rating: {peak_rating}"
    )
    story.append(Paragraph(summary_text, summary_style))
    story.append(Spacer(1, 4 * mm))

    # Group results by platform.
    grouped: dict[str, list[_ResultLike]] = defaultdict(list)
    for r in results_list:
        grouped[(r.platform or "").lower()].append(r)

    # Ensure deterministic section order.
    platform_order = ["codeforces", "codechef", "leetcode"]

    for idx, platform in enumerate(platform_order):
        label = PLATFORM_LABELS[platform]
        platform_results = grouped.get(platform, [])

        # Start a new page if the previous section was long.
        if idx > 0 and platform_results and len(platform_results) > 15:
            story.append(PageBreak())

        story.append(Paragraph(f"--- {label} ---", section_header_style))

        table_data: list[list[str]] = [
            ["Contest Name", "Rating", "Problems Solved"],
        ]
        for r in platform_results:
            date_suffix = ""
            if r.contest_date:
                date_suffix = f" ({r.contest_date.isoformat()})"

            contest_name = f"{r.contest_name}{date_suffix}"
            rating = "" if r.rating is None else str(r.rating)
            problems = "" if r.problems_solved is None else str(r.problems_solved)
            table_data.append([contest_name, rating, problems])

        table = Table(
            table_data,
            colWidths=[110 * mm, 30 * mm, 35 * mm],
            hAlign="LEFT",
        )

        table_style_commands = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.black),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
            ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ]
        table.setStyle(TableStyle(table_style_commands))

        story.append(table)
        story.append(Spacer(1, 6 * mm))

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)

    return buf.getvalue()

