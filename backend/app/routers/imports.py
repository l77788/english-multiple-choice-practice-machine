from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..config import UPLOAD_DIR
from ..database import get_db
from ..schemas import DraftUpdate
from ..services.docx_parser import parse_exam, publish_draft, validate_draft


router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("")
def list_imports(connection: sqlite3.Connection = Depends(get_db)) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, filename, detected_year, detected_format, status,
               warnings, created_at, updated_at
        FROM import_jobs
        ORDER BY id DESC
        """
    ).fetchall()
    result = []
    for row in rows:
        payload = dict(row)
        payload["warnings"] = json.loads(payload["warnings"] or "[]")
        result.append(payload)
    return result


@router.post("")
async def upload_import(
    file: UploadFile = File(...),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.lower().endswith((".docx", ".doc")):
        raise HTTPException(400, "请选择 Word 文件")
    suffix = Path(file.filename).suffix or ".docx"
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = UPLOAD_DIR / stored_name
    with stored_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    try:
        draft = parse_exam(stored_path)
    except Exception as error:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Word解析失败：{error}") from error

    cursor = connection.execute(
        """
        INSERT INTO import_jobs
            (filename, stored_path, detected_year, detected_format,
             status, draft_data, warnings)
        VALUES (?, ?, ?, ?, 'draft', ?, ?)
        """,
        (
            file.filename,
            str(stored_path),
            draft.get("year"),
            draft.get("detected_format"),
            json.dumps(draft, ensure_ascii=False),
            json.dumps(draft["warnings"], ensure_ascii=False),
        ),
    )
    connection.commit()
    return {
        "id": cursor.lastrowid,
        "filename": file.filename,
        "draft": draft,
        "warnings": draft["warnings"],
    }


@router.get("/{job_id}")
def import_detail(
    job_id: int, connection: sqlite3.Connection = Depends(get_db)
) -> dict:
    row = connection.execute(
        "SELECT * FROM import_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "导入任务不存在")
    payload = dict(row)
    payload["draft_data"] = json.loads(payload["draft_data"])
    payload["warnings"] = json.loads(payload["warnings"])
    return payload


@router.put("/{job_id}")
def update_draft(
    job_id: int,
    request: DraftUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT draft_data FROM import_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "导入任务不存在")
    old_data = json.loads(row["draft_data"])
    draft = request.draft_data
    draft["warnings"] = validate_draft(draft)
    connection.execute(
        """
        UPDATE import_jobs
        SET draft_data = ?, warnings = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            json.dumps(draft, ensure_ascii=False),
            json.dumps(draft["warnings"], ensure_ascii=False),
            job_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO revision_log
            (import_job_id, entity_type, entity_ref, field_name,
             old_value, new_value, source, approved)
        VALUES (?, 'draft', ?, 'all', ?, ?, 'user', 1)
        """,
        (
            job_id,
            str(job_id),
            json.dumps(old_data, ensure_ascii=False),
            json.dumps(draft, ensure_ascii=False),
        ),
    )
    connection.commit()
    return {"draft": draft, "warnings": draft["warnings"]}


@router.post("/{job_id}/publish")
def publish(
    job_id: int,
    force: bool = False,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT * FROM import_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "导入任务不存在")
    draft = json.loads(row["draft_data"])
    warnings = validate_draft(draft)
    if warnings and not force:
        raise HTTPException(409, {"message": "仍有校验问题", "warnings": warnings})
    paper_id = publish_draft(connection, draft, row["filename"])
    connection.execute(
        """
        UPDATE import_jobs
        SET status = 'published', warnings = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (json.dumps(warnings, ensure_ascii=False), job_id),
    )
    connection.commit()
    return {"published": True, "paper_id": paper_id, "warnings": warnings}

