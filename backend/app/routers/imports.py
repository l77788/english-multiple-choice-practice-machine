from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..config import UPLOAD_DIR
from ..database import get_db
from ..schemas import DraftUpdate, ImportAnswersUpdate, ModelAssistRequest
from ..services.docx_parser import (
    apply_answers_to_draft,
    find_companion_answer_pdf,
    objective_question_numbers,
    parse_exam,
    publish_draft,
    validate_draft,
)
from ..services.import_assist import (
    apply_model_assist,
    document_text,
    extract_attachment_text,
    run_model_assist,
)


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
    answer_file: UploadFile | None = File(default=None),
    use_model_assist: bool = Form(False),
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.lower().endswith((".docx", ".doc")):
        raise HTTPException(400, "请选择 Word 文件")
    if answer_file and (
        not answer_file.filename
        or not answer_file.filename.lower().endswith((".docx", ".doc", ".pdf"))
    ):
        raise HTTPException(400, "答案文件仅支持 DOC、DOCX 或 PDF")
    suffix = Path(file.filename).suffix or ".docx"
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = UPLOAD_DIR / stored_name
    stored_answer_path: Path | None = None
    with stored_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    if answer_file and answer_file.filename:
        answer_suffix = Path(answer_file.filename).suffix or ".docx"
        stored_answer_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{answer_suffix}"
        with stored_answer_path.open("wb") as target:
            shutil.copyfileobj(answer_file.file, target)
    parse_context: dict[str, str] = {}
    try:
        draft = parse_exam(
            stored_path,
            answer_path=stored_answer_path,
            source_name=file.filename,
            answer_name=answer_file.filename if answer_file else None,
        )
        if use_model_assist:
            answer_text = ""
            if stored_answer_path:
                answer_text = extract_attachment_text(stored_answer_path)
            elif not draft.get("answers"):
                companion = find_companion_answer_pdf(stored_path, draft.get("year"))
                if companion:
                    answer_text = extract_attachment_text(companion)
            parse_context["answer_text"] = answer_text[:20000]
            try:
                result, _ = run_model_assist(
                    connection,
                    draft,
                    document_text(stored_path),
                    answer_text=answer_text,
                )
                draft = apply_model_assist(draft, result)
                draft["model_assist"]["phase"] = "upload"
                if not draft.get("answers_confirmed"):
                    draft["answer_status"] = {
                        "status": "parsed",
                        "message": (
                            f"模型辅助解析识别 {draft['model_assist']['answer_total']} 道答案，"
                            "发布前请人工核对"
                        ),
                    }
            except Exception as error:
                draft["model_assist"] = {
                    "status": "failed",
                    "phase": "upload",
                    "error": str(error)[:400],
                    "fell_back_to_local": True,
                }
    except Exception as error:
        stored_path.unlink(missing_ok=True)
        if stored_answer_path:
            stored_answer_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Word解析失败：{error}") from error
    finally:
        if stored_answer_path:
            stored_answer_path.unlink(missing_ok=True)

    cursor = connection.execute(
        """
        INSERT INTO import_jobs
            (filename, stored_path, detected_year, detected_format,
             status, draft_data, warnings, parse_context)
        VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)
        """,
        (
            file.filename,
            str(stored_path),
            draft.get("year"),
            draft.get("detected_format"),
            json.dumps(draft, ensure_ascii=False),
            json.dumps(draft["warnings"], ensure_ascii=False),
            json.dumps(parse_context, ensure_ascii=False),
        ),
    )
    connection.commit()
    return {
        "id": cursor.lastrowid,
        "filename": file.filename,
        "draft": draft,
        "warnings": draft["warnings"],
        "model_assist": draft.get("model_assist"),
    }


@router.post("/{job_id}/model-assist")
def model_assist_retry(
    job_id: int,
    request: ModelAssistRequest,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT * FROM import_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "导入任务不存在")
    if row["status"] == "published":
        raise HTTPException(409, "已发布题库不能重新解析")
    draft = json.loads(row["draft_data"])
    try:
        parse_context = json.loads(row["parse_context"] or "{}")
    except json.JSONDecodeError:
        parse_context = {}
    answer_text = str(parse_context.get("answer_text", ""))
    try:
        result, _ = run_model_assist(
            connection,
            draft,
            document_text(Path(row["stored_path"])),
            answer_text=answer_text,
            profile_id=request.profile_id,
            model=request.model.strip() or None,
        )
        draft = apply_model_assist(
            draft,
            result,
            model_name=request.model.strip(),
        )
        draft["model_assist"]["phase"] = "retry"
        if not draft.get("answers_confirmed"):
            draft["answer_status"] = {
                "status": "parsed",
                "message": (
                    f"模型辅助解析识别 {draft['model_assist']['answer_total']} 道答案，"
                    "发布前请人工核对"
                ),
            }
    except Exception as error:
        return {
            "draft": draft,
            "warnings": draft.get("warnings", []),
            "model_assist": {
                "status": "failed",
                "phase": "retry",
                "error": str(error)[:400],
                "fell_back_to_local": True,
            },
        }
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
             old_value, new_value, source, model_name, approved)
        VALUES (?, 'draft', ?, 'answers', ?, ?, 'model-assist', ?, 1)
        """,
        (
            job_id,
            str(job_id),
            json.dumps(
                {str(key): value for key, value in draft.get("answers", {}).items()},
                ensure_ascii=False,
            ),
            json.dumps(draft.get("answer_sources", {}), ensure_ascii=False),
            request.model.strip(),
        ),
    )
    connection.commit()
    return {
        "draft": draft,
        "warnings": draft["warnings"],
        "model_assist": draft["model_assist"],
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
    if old_data.get("answers") != draft.get("answers"):
        old_sources = old_data.get("answer_sources", {})
        new_sources = draft.setdefault("answer_sources", {})
        old_answers = old_data.get("answers", {})
        new_answers = draft.get("answers", {})
        for number, answer in new_answers.items():
            if answer and old_answers.get(number) != answer:
                new_sources[number] = "人工录入"
            elif answer and number not in new_sources and number in old_sources:
                new_sources[number] = old_sources[number]
            elif not answer:
                new_sources.pop(number, None)
        draft["answers_confirmed"] = True
        draft["answer_status"] = {
            "status": "confirmed",
            "message": "人工编辑的标准答案已确认",
        }
    apply_answers_to_draft(draft)
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


@router.patch("/{job_id}/answers")
def update_answers(
    job_id: int,
    request: ImportAnswersUpdate,
    connection: sqlite3.Connection = Depends(get_db),
) -> dict:
    row = connection.execute(
        "SELECT draft_data FROM import_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "导入任务不存在")
    draft = json.loads(row["draft_data"])
    old_answers = dict(draft.get("answers", {}))
    answer_sources = draft.setdefault("answer_sources", {})
    allowed_numbers = {
        str(number) for number in objective_question_numbers(draft)
    }
    for number, answer in request.answers.items():
        normalized_number = str(number).strip()
        normalized_answer = str(answer).strip().upper()
        if not normalized_number.isdigit():
            raise HTTPException(422, f"无效题号：{number}")
        if normalized_number not in allowed_numbers:
            raise HTTPException(422, f"第 {normalized_number} 题不属于当前客观题草稿")
        if normalized_answer and normalized_answer not in "ABCDEFGH":
            raise HTTPException(422, f"第 {normalized_number} 题答案无效")
        draft.setdefault("answers", {})[normalized_number] = normalized_answer
        if normalized_answer:
            if old_answers.get(normalized_number) != normalized_answer:
                answer_sources[normalized_number] = "人工录入"
            elif normalized_number not in answer_sources:
                answer_sources[normalized_number] = "人工录入"
        else:
            answer_sources.pop(normalized_number, None)
    draft["answers_confirmed"] = True
    source_kinds = {
        source
        for number, source in answer_sources.items()
        if draft.get("answers", {}).get(number)
    }
    if source_kinds == {"人工录入"}:
        draft["answer_source"] = "人工录入"
        draft["answer_status"] = {
            "status": "confirmed",
            "message": "人工录入答案已确认",
        }
    else:
        draft["answer_source"] = (
            "、".join(sorted(source_kinds)) if source_kinds else "人工录入"
        )
        draft["answer_status"] = {
            "status": "confirmed",
            "message": "自动识别与人工校对的答案已确认",
        }
    apply_answers_to_draft(draft)
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
        VALUES (?, 'draft', ?, 'answers', ?, ?, 'user', 1)
        """,
        (
            job_id,
            str(job_id),
            json.dumps(old_answers, ensure_ascii=False),
            json.dumps(draft.get("answers", {}), ensure_ascii=False),
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
