from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db


router = APIRouter(prefix="/papers", tags=["papers"])


@router.get("")
def list_papers(connection: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = connection.execute(
        """
        SELECT papers.*,
               COUNT(DISTINCT units.id) AS unit_count,
               COUNT(questions.id) AS question_count
        FROM papers
        LEFT JOIN units ON units.paper_id = papers.id
        LEFT JOIN questions ON questions.unit_id = units.id
        GROUP BY papers.id
        ORDER BY papers.year DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


@router.get("/{paper_id}")
def get_paper(
    paper_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    paper = connection.execute(
        "SELECT * FROM papers WHERE id = ?", (paper_id,)
    ).fetchone()
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    units = connection.execute(
        """
        SELECT units.*,
               COUNT(questions.id) AS question_count,
               COALESCE(SUM(questions.score), 0) AS max_score
        FROM units
        LEFT JOIN questions ON questions.unit_id = units.id
        WHERE units.paper_id = ?
        GROUP BY units.id
        ORDER BY units.sequence
        """,
        (paper_id,),
    ).fetchall()
    return {**dict(paper), "units": [dict(row) for row in units]}

