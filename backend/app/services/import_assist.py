from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .ai_client import chat_completion, parse_json_response
from .docx_parser import (
    apply_answers_to_draft,
    extract_blocks,
    objective_question_numbers,
    validate_draft,
)

MAX_DOCUMENT_CHARS = 60000
MAX_ANSWER_CHARS = 20000
MODEL_ASSIST_MAX_TOKENS = 8000


def extract_attachment_text(path: Path) -> str:
    """Extract raw text from a DOC/DOCX/PDF answer attachment for the model."""
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    blocks, _, converted = extract_blocks(path)
    try:
        return "\n".join(blocks)
    finally:
        if converted:
            import shutil

            shutil.rmtree(converted.parent, ignore_errors=True)


def document_text(path: Path) -> str:
    blocks, _, converted = extract_blocks(path)
    try:
        return "\n".join(blocks)[:MAX_DOCUMENT_CHARS]
    finally:
        if converted:
            import shutil

            shutil.rmtree(converted.parent, ignore_errors=True)


def _draft_summary(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "year": draft.get("year"),
        "expected_numbers": [str(number) for number in objective_question_numbers(draft)],
        "units": [
            {
                "unit_type": unit.get("unit_type"),
                "title": unit.get("title"),
                "questions": [
                    {
                        "number": question.get("number"),
                        "stem": str(question.get("stem", ""))[:300],
                        "options": [
                            {
                                "key": option.get("key"),
                                "content": str(option.get("content", ""))[:200],
                            }
                            for option in question.get("options", [])
                        ],
                        "answer": question.get("answer", ""),
                    }
                    for question in unit.get("questions", [])
                ],
            }
            for unit in draft.get("units", [])
        ],
        "answers_found_locally": draft.get("answers", {}),
        "answer_sources": draft.get("answer_sources", {}),
    }


def run_model_assist(
    connection: sqlite3.Connection,
    draft: dict[str, Any],
    document_text: str,
    answer_text: str = "",
    *,
    profile_id: int | None = None,
    model: str | None = None,
    correct_structure: bool = False,
) -> tuple[dict[str, Any], str]:
    """Ask the model to locate questions and map answers, returning parsed JSON."""
    prompt = """
你是考研英语真题导入解析助手。用户上传了 Word 试卷（可能附带答案文件），程序已经用规则解析出一份草稿。
你的任务是提高导入精确度，只能依据提供的材料，绝对不能编造或推测答案、题干、选项或文章内容。

请完成三件事：
1. 答案对应：在 document_text 或 answer_text 中找到答案区（例如“参考答案”“答案速查”“1-5 BACDC”等），
   输出完整准确的 answer_map（题号 → 答案字母）。答案只能来自材料；材料中没有答案时 answer_map 保持空对象。
2. 题号核对：对照材料检查草稿中每道题的题号（完形 1-20、阅读 21-40、Part B 41-45）。
   只有材料能明确证明题号错位时，才在 number_map 中给出 old -> new；否则留空。
3. 结构问题：列出材料与草稿明显不一致的问题（选项归属错误、题干断行、答案数量与题目数不符、
   完形或 Part B 空位缺失等），每条一句话放入 issues。

注意：draft_summary 中列出的 expected_numbers 是本次实际导入的客观题题号。
如果材料中的 Part B 是“translate the underlined segments into Chinese”等翻译题，且草稿没有导入 41-45，
这是预期行为，不要把“缺少 Part B 41-45”列为 issues，也不要要求把翻译题强行转换成客观题。

"""
    if correct_structure:
        prompt += """
4. 结构修正（已启用）：如果题干或选项归属与材料明显不一致，可以在 question_fixes 中给出修正。
   question_fixes: [{"number": 5, "stem": "修正后的题干",
     "options": [{"key": "A", "content": "..."}, {"key": "B", "content": "..."},
                 {"key": "C", "content": "..."}, {"key": "D", "content": "..."}]}]
   只依据材料修正，不能凭空改写或补全；选项数量必须与原题一致；没有把握的题不要放入。

"""
    prompt += """
只输出 JSON，格式：
{"answer_map": {"1": "B"}, "number_map": {"12": "13"},
 "question_fixes": [{"number": 5, "stem": "...", "options": [{"key": "A", "content": "..."}]}],
 "issues": ["第5题选项疑似属于第6题"], "notes": "简要说明"}
answer_map 的题号使用材料核对后的最终题号；number_map 只描述草稿旧题号到最终题号的变化。
answer_map 的值只能是单个字母 A-H；没有把握的题不要填。不要输出逐题解析，不要翻译文章。
""".strip()
    payload = {
        "document_text": document_text,
        "answer_text": answer_text,
        "draft_summary": _draft_summary(draft),
    }
    raw = chat_completion(
        connection,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        profile_id=profile_id,
        model=model,
        # Reasoning models may spend most of a small budget inspecting a full
        # Word export.  2800 tokens was not enough for the 2002 paper to emit
        # any JSON; 8000 keeps the request bounded while allowing the complete
        # answer map and optional structure fixes to be returned.
        max_tokens=MODEL_ASSIST_MAX_TOKENS,
    )
    result = parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError("模型没有返回有效的 JSON 对象")
    return result, raw


def apply_model_assist(
    draft: dict[str, Any],
    result: dict[str, Any],
    model_name: str = "",
    *,
    correct_structure: bool = False,
) -> dict[str, Any]:
    """Apply a validated model result directly into the draft."""
    issues = result.get("issues")
    if not isinstance(issues, list):
        issues = []
    issue_texts = [str(item).strip()[:160] for item in issues if str(item).strip()]

    applied_number_fixes = 0
    normalized_number_map: dict[str, str] = {}
    number_map = result.get("number_map")
    if isinstance(number_map, dict) and number_map:
        question_rows = [
            (unit, question)
            for unit in draft.get("units", [])
            for question in unit.get("questions", [])
        ]
        questions = [question for _, question in question_rows]
        original_numbers = [str(question.get("number", "")).strip() for question in questions]
        for old_number, new_number in number_map.items():
            old = str(old_number).strip()
            new = str(new_number).strip()
            matching_row = next(
                (
                    (unit, question)
                    for unit, question in question_rows
                    if str(question.get("number", "")).strip() == old
                ),
                None,
            )
            if matching_row is None or not old.isdigit() or not new.isdigit():
                continue
            unit, _ = matching_row
            normalized_new = int(new)
            unit_type = str(unit.get("unit_type", ""))
            allowed = (
                range(1, 21)
                if unit_type == "cloze"
                else range(21, 41)
                if unit_type == "reading"
                else range(41, 46)
                if unit_type == "part_b"
                else range(1, 46)
            )
            if normalized_new in allowed:
                normalized_number_map[old] = str(normalized_new)
        remapped_numbers = [
            normalized_number_map.get(number, number) for number in original_numbers
        ]
        if normalized_number_map and len(set(remapped_numbers)) == len(remapped_numbers):
            for question, old_number, new_number in zip(
                questions, original_numbers, remapped_numbers
            ):
                if old_number != new_number:
                    question["number"] = int(new_number)
                    applied_number_fixes += 1
            old_answers = dict(draft.setdefault("answers", {}))
            old_sources = dict(draft.setdefault("answer_sources", {}))
            draft["answers"] = {
                normalized_number_map.get(str(number), str(number)): answer
                for number, answer in old_answers.items()
            }
            draft["answer_sources"] = {
                normalized_number_map.get(str(number), str(number)): source
                for number, source in old_sources.items()
            }
        elif normalized_number_map:
            issue_texts.append("题号修正会产生重复题号，已拒绝自动应用，请人工核对")

    answers = draft.setdefault("answers", {})
    answer_sources = draft.setdefault("answer_sources", {})
    expected_numbers = {str(number) for number in objective_question_numbers(draft)}
    applied_answers = 0
    answer_map = result.get("answer_map")
    if isinstance(answer_map, dict):
        for number, letter in answer_map.items():
            # answer_map and question_fixes refer to the corrected/material
            # question numbers; number_map has already renamed the draft.
            normalized_number = str(number).strip()
            normalized_letter = str(letter or "").strip().upper()
            if normalized_number not in expected_numbers:
                continue
            if len(normalized_letter) != 1 or normalized_letter not in "ABCDEFGH":
                continue
            answers[normalized_number] = normalized_letter
            answer_sources[normalized_number] = "模型辅助"
            applied_answers += 1

    applied_fixes = 0
    question_fixes = result.get("question_fixes")
    if correct_structure and isinstance(question_fixes, list):
        for fix in question_fixes:
            if not isinstance(fix, dict):
                continue
            number = str(fix.get("number", "")).strip()
            if number not in expected_numbers:
                continue
            target = None
            for unit in draft.get("units", []):
                for question in unit.get("questions", []):
                    if str(question.get("number")) == number:
                        target = question
                        break
                if target is not None:
                    break
            if target is None:
                continue
            changed = False
            stem = fix.get("stem")
            if isinstance(stem, str) and stem.strip() and stem.strip() != target.get("stem"):
                target["stem"] = stem.strip()[:2000]
                changed = True
            options = fix.get("options")
            if isinstance(options, list) and len(options) == len(target.get("options", [])):
                cleaned: list[dict[str, str]] = []
                valid = True
                for option in options:
                    if not isinstance(option, dict):
                        valid = False
                        break
                    key = str(option.get("key", "")).strip().upper()
                    content = str(option.get("content", "")).strip()
                    if not key or not content:
                        valid = False
                        break
                    cleaned.append({"key": key, "content": content[:1000]})
                old_keys = [
                    str(item.get("key", "")).strip().upper()
                    for item in target["options"]
                ]
                new_keys = [item["key"] for item in cleaned]
                if valid and new_keys == old_keys and len(set(new_keys)) == len(new_keys):
                    old_contents = [str(item.get("content", "")) for item in target["options"]]
                    new_contents = [item["content"] for item in cleaned]
                    if old_contents != new_contents:
                        target["options"] = cleaned
                        changed = True
            if changed:
                applied_fixes += 1

    issue_texts = list(dict.fromkeys(issue_texts))[:20]

    draft["model_assist"] = {
        "status": "applied",
        "applied_answers": applied_answers,
        "applied_fixes": applied_fixes,
        "applied_number_fixes": applied_number_fixes,
        "answer_total": sum(1 for value in answers.values() if value),
        "issue_count": len(issue_texts),
        "issues": issue_texts,
        "notes": str(result.get("notes", "") or "")[:300],
        "model_name": model_name,
        "applied_at": datetime.now().isoformat(timespec="seconds"),
    }
    # When a complete answer map is returned and there are no unresolved
    # structural issues, the model has performed the requested one-click
    # proofreading.  Mark the answer set confirmed so a complete Word+answer
    # import is publishable without a redundant manual save step.  Incomplete
    # or disputed results retain the existing manual-review gate.
    expected_answer_numbers = {
        str(number) for number in objective_question_numbers(draft)
    }
    answered_numbers = {
        str(number)
        for number, value in answers.items()
        if str(value or "").strip()
    }
    fully_verified = bool(expected_answer_numbers) and expected_answer_numbers <= answered_numbers and not issue_texts
    if fully_verified:
        draft["answers_confirmed"] = True
        draft["answer_source"] = "模型辅助"
        draft["answer_status"] = {
            "status": "confirmed",
            "message": "模型已完成答案与题目结构校对",
        }
        draft["model_assist"]["answers_confirmed_by_model"] = True
    else:
        draft["model_assist"]["answers_confirmed_by_model"] = False
    apply_answers_to_draft(draft)
    draft["warnings"] = validate_draft(draft)
    existing = set(draft["warnings"])
    for item in issue_texts:
        text = f"[模型辅助] {item}"
        if text not in existing and len(draft["warnings"]) < 25:
            draft["warnings"].append(text)
            existing.add(text)
    return draft
