from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..database import get_db


router = APIRouter(tags=["dashboard"])


@router.get("/startup")
@router.get("/overview", include_in_schema=False)
@router.get("/dashboard", include_in_schema=False)
def dashboard(connection: sqlite3.Connection = Depends(get_db)) -> dict:
    paper_count = connection.execute(
        "SELECT COUNT(*) AS count FROM papers WHERE status = 'published'"
    ).fetchone()["count"]
    unit_count = connection.execute("SELECT COUNT(*) AS count FROM units").fetchone()[
        "count"
    ]
    question_count = connection.execute(
        "SELECT COUNT(*) AS count FROM questions"
    ).fetchone()["count"]
    wrong_count = connection.execute(
        "SELECT COUNT(*) AS count FROM wrong_stats WHERE wrong_count > 0"
    ).fetchone()["count"]
    frequent_count = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM wrong_stats
        WHERE manually_frequent = 1
           OR wrong_count >= 3
           OR (
                json_array_length(recent_results) >= 5
                AND (
                    SELECT COUNT(*)
                    FROM json_each(recent_results)
                    WHERE value = 0
                ) >= 3
           )
        """
    ).fetchone()["count"]
    recent = connection.execute(
        """
        SELECT practice_sessions.id, practice_sessions.mode,
               practice_sessions.status, practice_sessions.started_at,
               practice_sessions.submitted_at, practice_sessions.score,
               practice_sessions.max_score, papers.year
        FROM practice_sessions
        LEFT JOIN papers ON papers.id = practice_sessions.paper_id
        ORDER BY practice_sessions.id DESC
        LIMIT 5
        """
    ).fetchall()
    return {
        "paper_count": paper_count,
        "unit_count": unit_count,
        "question_count": question_count,
        "wrong_count": wrong_count,
        "frequent_count": frequent_count,
        "recent_sessions": [dict(row) for row in recent],
    }
