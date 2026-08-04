from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree
from pypdf import PdfReader

from .passage_cleanup import repair_inline_blank_paragraph_breaks


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
PAGE_FOOTER_RE = re.compile(r"英语.*试题.*共\s*1?\s*5\s*页", re.I)
OPTION_MARK_RE = re.compile(
    r"(?:\[|\(|（|【|(?<![A-Z]))\s*([A-Ha-h])\s*(?:\]|\)|）|】|[\.．、,])\s*",
)
QUESTION_NUMBER_RE = re.compile(r"^\s*([1-5]?\d)\s*[\.．、)]\s*(.+)$", re.S)
TEXT_MARK_RE = re.compile(r"^\s*Text\s*([1-4lI])\s*$", re.I)


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u3000", " ")
    value = value.replace("〇", "0").replace("○", "0")
    value = value.replace("［", "[").replace("］", "]")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _convert_legacy(source: Path) -> Path:
    output_dir = Path(tempfile.mkdtemp(prefix="linjian-word-"))
    destination = output_dir / f"{source.stem}-converted.docx"
    script = Path(__file__).with_name("convert_word.ps1")
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Source",
            str(source),
            "-Destination",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return destination


def detect_format(path: Path) -> str:
    signature = path.read_bytes()[:8]
    if signature == bytes.fromhex("D0CF11E0A1B11AE1"):
        return "legacy_doc"
    with zipfile.ZipFile(path) as archive:
        content_types = archive.read("[Content_Types].xml").decode(
            "utf-8", errors="replace"
        )
    if "macroEnabled" in content_types:
        return "macro_ooxml"
    return "docx"


def _extract_ooxml_text(element: etree._Element) -> str:
    """Read visible OOXML text while preserving formatted exam blanks.

    Some source papers draw a cloze/Part B blank by underlining only the
    question-number run. Reading ``w:t`` nodes alone keeps the number but loses
    the visual line, so normalize that run to a portable ``N ______`` marker.
    """
    parts: list[str] = []
    for run in element.xpath(".//w:r", namespaces=NS):
        text = "".join(run.xpath(".//w:t/text()", namespaces=NS))
        if not text:
            continue
        underline = run.xpath("./w:rPr/w:u/@w:val", namespaces=NS)
        is_underlined = bool(underline) and underline[0].lower() not in {
            "none",
            "0",
            "false",
        }
        stripped = text.strip()
        if is_underlined and re.fullmatch(r"(?:[1-9]|[1-4]\d)", stripped):
            parts.append(f" {stripped} ______ ")
        else:
            parts.append(text)
    return "".join(parts)


def _ensure_numbered_blanks(passage: str, numbers: range) -> str:
    """Normalize sequential exam placeholders to ``N ______``.

    A few Word exports preserve only some underline formatting. The question
    numbers themselves remain in reading order, which lets us safely repair the
    remaining placeholders without treating years or measurements as blanks.
    """
    expected = list(numbers)
    cursor = 0
    for number in numbers:
        pattern = re.compile(
            rf"(?<![\d_])(?:\(\s*)?{number}(?:\s*\))?(?:\s*_{2,})?(?![\d_])"
        )
        match = pattern.search(passage, cursor)
        if not match:
            continue
        end = match.end()
        trailing_blank = re.match(r"\s*_{2,}", passage[end:])
        if trailing_blank:
            end += trailing_blank.end()
        replacement = f"{number} ______"
        passage = passage[: match.start()] + replacement + passage[end:]
        cursor = match.start() + len(replacement)

    present = {
        int(number)
        for number in re.findall(r"(?<!\d)([1-4]?\d)\s+_{2,}", passage)
    }
    missing = [number for number in expected if number not in present]
    for number in missing:
        previous = max((value for value in present if value < number), default=None)
        following = min((value for value in present if value > number), default=None)
        if previous is None or following is None:
            continue
        previous_match = re.search(
            rf"(?<!\d){previous}\s+_{{2,}}",
            passage,
        )
        following_match = re.search(
            rf"(?<!\d){following}\s+_{{2,}}",
            passage,
            re.S,
        )
        if not previous_match or not following_match:
            continue
        gap_start = previous_match.end()
        gap_end = following_match.start()
        gap = passage[gap_start:gap_end]
        # Broken text frames occasionally drop the placeholder at a hard line
        # boundary, leaving fragments such as "barrier that\n\nPavlopetri".
        boundary = re.search(r"(?<=[A-Za-z])\n\n(?=[A-Za-z])", gap)
        if not boundary:
            continue
        insertion = gap_start + boundary.start()
        replacement = f" {number} ______ "
        passage = passage[:insertion] + replacement + passage[insertion + 2 :]
        present.add(number)
    return passage


def _remove_duplicate_cloze_number_noise(passage: str) -> str:
    """Remove a stray early copy of a question number when its real blank exists.

    The 2026 source contains ``1 [blank] 9 AI also...`` and then the genuine
    ninth placeholder later in the article. Restrict cleanup to a bare number
    immediately following another blank and followed by a capitalized word.
    """
    for number in range(1, 21):
        marked = re.search(rf"(?<!\d){number}\s+_{{2,}}", passage)
        if not marked:
            continue
        noise = re.search(
            rf"((?:[1-9]|1\d|20)\s+_{{2,}})\s+{number}(?=\s+[A-Z])",
            passage[: marked.start()],
        )
        if noise:
            start = noise.start() + len(noise.group(1))
            passage = passage[:start] + passage[noise.end() :]
    return passage


def extract_blocks(path: Path) -> tuple[list[str], str, Path | None]:
    detected = detect_format(path)
    converted: Path | None = None
    parse_path = path
    if detected == "legacy_doc":
        converted = _convert_legacy(path)
        parse_path = converted

    with zipfile.ZipFile(parse_path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    blocks: list[str] = []
    for child in root.xpath("./w:body/*", namespaces=NS):
        text = _extract_ooxml_text(child)
        text = clean_text(text)
        if text:
            blocks.append(text)
    return blocks, detected, converted


def _find_index(blocks: list[str], pattern: str, start: int = 0) -> int:
    rx = re.compile(pattern, re.I)
    for index in range(start, len(blocks)):
        if rx.search(blocks[index]):
            return index
    return -1


def _is_noise(text: str) -> bool:
    return bool(
        PAGE_FOOTER_RE.search(text)
        or re.match(r"^(Directions:|Part [ABC]|Section [ⅠⅡⅢIVU ]+)", text, re.I)
        or "ANSWER SHEET" in text
    )


def _split_option_text(text: str) -> list[tuple[str, str]]:
    normalized = clean_text(text)
    # OCR frequently turns "[A]" into "A." or joins all options into one paragraph.
    matches = list(OPTION_MARK_RE.finditer(normalized))
    if len(matches) < 2:
        return []
    options: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        label = match.group(1).upper()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        content = clean_text(normalized[start:end])
        if content:
            options.append((label, content))
    return options


def _extract_flat_option_groups(text: str, expected_groups: int) -> list[list[dict[str, str]]]:
    normalized = clean_text(text)
    matches = list(OPTION_MARK_RE.finditer(normalized))
    if len(matches) < expected_groups * 4:
        return []
    flat: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        label = match.group(1).upper()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        content = clean_text(normalized[start:end])
        # Joined Word paragraphs often leave the next question number after option D.
        content = re.sub(r"\s*(?:[1-9]|1\d|20)\s*[\.．、]\s*$", "", content)
        flat.append((label, content))
    groups: list[list[dict[str, str]]] = []
    for offset in range(0, expected_groups * 4, 4):
        chunk = flat[offset:offset + 4]
        if [label for label, _ in chunk] != ["A", "B", "C", "D"]:
            return []
        groups.append(
            [{"key": label, "content": content} for label, content in chunk]
        )
    return groups


def _options_from_segment(text: str) -> list[dict[str, str]]:
    return [
        {"key": label, "content": content}
        for label, content in _split_option_text(text)
    ]


def _parse_compact_cloze_options(text: str) -> list[list[dict[str, str]]]:
    """Parse Word exports that flatten an option table into one paragraph.

    Some older files serialize the first table row by columns (A1, A2, B1,
    B2...), while modern files serialize all 20 rows as `1. A...B...`.
    Question-number anchors let us safely recover both layouts.
    """
    normalized = clean_text(text)
    starts = list(
        re.finditer(r"(?<!\d)([1-9]|1\d|20)\s*[\.．、]\s*", normalized)
    )
    by_number: dict[int, list[dict[str, str]]] = {}
    for index, start in enumerate(starts):
        number = int(start.group(1))
        if not 1 <= number <= 20:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(normalized)
        options = _options_from_segment(normalized[start.end() : end])
        if len(options) == 4 and [option["key"] for option in options] == [
            "A",
            "B",
            "C",
            "D",
        ]:
            by_number[number] = options

    run_start = next(
        (
            number
            for number in range(1, 21)
            if all(candidate in by_number for candidate in range(number, 21))
        ),
        21,
    )
    if run_start > 1:
        anchor = next(
            (
                match.start()
                for match in starts
                if int(match.group(1)) == run_start
            ),
            len(normalized),
        )
        prefix = normalized[:anchor]
        prefix_options = _split_option_text(prefix)
        missing_count = run_start - 1
        if len(prefix_options) == missing_count * 4:
            labels = [label for label, _ in prefix_options]
            expected_column_major = (
                ["A"] * missing_count
                + ["B"] * missing_count
                + ["C"] * missing_count
                + ["D"] * missing_count
            )
            if labels == expected_column_major:
                for question_index in range(missing_count):
                    by_number[question_index + 1] = [
                        {
                            "key": chr(ord("A") + column),
                            "content": prefix_options[column * missing_count + question_index][1],
                        }
                        for column in range(4)
                    ]
            else:
                for question_index in range(missing_count):
                    offset = question_index * 4
                    chunk = prefix_options[offset : offset + 4]
                    if [label for label, _ in chunk] == ["A", "B", "C", "D"]:
                        by_number[question_index + 1] = [
                            {"key": label, "content": content}
                            for label, content in chunk
                        ]

    if len(by_number) == 20:
        return [by_number[number] for number in range(1, 21)]

    # Older clean exports contain exactly 80 row-major markers and no reliable
    # question-number anchors.
    flat = _extract_flat_option_groups(normalized, 20)
    return flat if len(flat) == 20 else []


def _parse_numbered_options(text: str) -> list[dict[str, Any]]:
    normalized = clean_text(text)
    starts = list(re.finditer(r"(?<!\d)([1-9]|1\d|20)\s*[\.．、]\s*", normalized))
    if len(starts) < 5:
        return []
    result = []
    for index, start in enumerate(starts):
        number = int(start.group(1))
        end = starts[index + 1].start() if index + 1 < len(starts) else len(normalized)
        section = normalized[start.end():end]
        options = _split_option_text(section)
        if len(options) == 4:
            result.append(
                {
                    "number": number,
                    "stem": "",
                    "options": [{"key": label, "content": content} for label, content in options],
                }
            )
    return result


def extract_answer_key(blocks: list[str]) -> dict[int, str]:
    tail = " ".join(blocks[-100:])
    answer_start = max(
        tail.lower().rfind("答案"),
        tail.lower().rfind("answer key"),
        tail.lower().rfind("答案速查"),
    )
    if answer_start >= 0:
        tail = tail[answer_start:]

    answers: dict[int, str] = {}
    for number, letter in re.findall(
        r"(?<!\d)([1-5]?\d)\s*[\.．、:]\s*([A-H])", tail, re.I
    ):
        numeric = int(number)
        if 1 <= numeric <= 45:
            answers[numeric] = letter.upper()

    # A small number of source files drop the number before one answer, e.g.
    # "... 9.D10.C C 12.A ...". Infer only the immediately missing number.
    for letter, following_number in re.findall(
        r"(?:^|\s)([A-H])\s+(?=([1-5]?\d)\s*[\.．、:])", tail, re.I
    ):
        inferred = int(following_number) - 1
        if 1 <= inferred <= 45 and inferred not in answers:
            answers[inferred] = letter.upper()

    compact = re.sub(r"\s+", "", tail).upper()
    for start, end, letters in re.findall(
        r"([1-4]?\d)\s*[-~～至]\s*([1-4]?\d)\s*([A-H]{5,20})", compact
    ):
        first, last = int(start), int(end)
        expected = last - first + 1
        if expected > 0 and len(letters) >= expected:
            for offset, letter in enumerate(letters[:expected]):
                answers[first + offset] = letter

    # Compressed modern keys can omit spaces between ranges.
    for first in range(1, 46, 5):
        last = first + 4
        match = re.search(
            rf"{first}(?:-|~|～|至){last}([A-H]{{5}})", compact
        )
        if match:
            for offset, letter in enumerate(match.group(1)):
                answers[first + offset] = letter
    return answers


def extract_pdf_answer_key(path: Path) -> dict[int, str]:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    answers: dict[int, str] = {}
    for number, letter in re.findall(
        r"(?<!\d)([1-4]?\d)\s*[\.．、:]?\s*([A-H])(?=\s|$|\d|[.,，。])",
        text,
        re.I,
    ):
        numeric = int(number)
        if 1 <= numeric <= 45:
            answers[numeric] = letter.upper()
    return answers


def find_companion_answer_pdf(path: Path, year: int | None) -> Path | None:
    if year is None:
        return None
    matches = sorted(
        candidate
        for candidate in path.parent.iterdir()
        if candidate.suffix.lower() == ".pdf"
        and str(year) in candidate.name
        and ("答案" in candidate.name or "answer" in candidate.name.lower())
    )
    return matches[0] if matches else None


def _parse_cloze(blocks: list[str], answers: dict[int, str]) -> dict[str, Any]:
    start = _find_index(blocks, r"Section\s*[ⅠI1]\s*Use\s+of\s+English")
    reading = _find_index(blocks, r"Reading\s+Comprehension", max(start, 0))
    section = blocks[start + 1:reading] if start >= 0 and reading > start else []
    content_blocks = [
        text
        for text in section
        if not _is_noise(text)
        and not re.search(r"Choose the best word", text, re.I)
    ]
    option_candidates = sorted(
        content_blocks,
        key=lambda text: len(OPTION_MARK_RE.findall(text)),
        reverse=True,
    )
    option_block = option_candidates[0] if option_candidates else ""
    flat_groups = _parse_compact_cloze_options(option_block)
    if flat_groups:
        option_block_indices = {
            index
            for index, text in enumerate(content_blocks)
            if text == option_block
        }
        final_questions = []
        for number, options in enumerate(flat_groups, 1):
            final_questions.append(
                {
                    "number": number,
                    "stem": "",
                    "options": options,
                    "answer": answers.get(number, ""),
                    "score": 0.5,
                }
            )
        return {
            "unit_type": "cloze",
            "subtype": "cloze",
            "title": "完型填空",
            "sequence": 1,
            "passage": "\n\n".join(
                text
                for index, text in enumerate(content_blocks)
                if index not in option_block_indices
            ),
            "shared_data": {},
            "questions": final_questions,
        }

    standalone_options = [
        _options_from_segment(text)
        for text in content_blocks
        if len(_options_from_segment(text)) == 4
    ]
    if len(standalone_options) == 20:
        option_texts = {
            text
            for text in content_blocks
            if len(_options_from_segment(text)) == 4
        }
        return {
            "unit_type": "cloze",
            "subtype": "cloze",
            "title": "完型填空",
            "sequence": 1,
            "passage": "\n\n".join(
                text for text in content_blocks if text not in option_texts
            ),
            "shared_data": {},
            "questions": [
                {
                    "number": number,
                    "stem": "",
                    "options": standalone_options[number - 1],
                    "answer": answers.get(number, ""),
                    "score": 0.5,
                }
                for number in range(1, 21)
            ],
        }

    questions: list[dict[str, Any]] = []
    option_block_indices = set()
    for index, text in enumerate(content_blocks):
        parsed = _parse_numbered_options(text)
        if parsed:
            questions.extend(parsed)
            option_block_indices.add(index)
            continue
        match = QUESTION_NUMBER_RE.match(text)
        if match and 1 <= int(match.group(1)) <= 20:
            options = _split_option_text(text)
            if len(options) == 4:
                questions.append(
                    {
                        "number": int(match.group(1)),
                        "stem": "",
                        "options": [
                            {"key": label, "content": content}
                            for label, content in options
                        ],
                    }
                )
                option_block_indices.add(index)

    deduped = {question["number"]: question for question in questions}
    passage = "\n\n".join(
        text for index, text in enumerate(content_blocks) if index not in option_block_indices
    )
    final_questions = []
    for number in range(1, 21):
        question = deduped.get(
            number,
            {"number": number, "stem": "", "options": []},
        )
        question["answer"] = answers.get(number, "")
        question["score"] = 0.5
        final_questions.append(question)
    return {
        "unit_type": "cloze",
        "subtype": "cloze",
        "title": "完型填空",
        "sequence": 1,
        "passage": passage,
        "shared_data": {},
        "questions": final_questions,
    }


def _labeled_question_groups(segment: list[str], first_number: int) -> tuple[str, list[dict]]:
    passage_parts = []
    questions: list[dict] = []
    current: dict[str, Any] | None = None
    question_started = False
    expected = set(range(first_number, first_number + 5))

    for text in segment:
        if _is_noise(text):
            continue
        match = QUESTION_NUMBER_RE.match(text)
        if match and int(match.group(1)) in expected:
            question_started = True
            if current:
                questions.append(current)
            current = {
                "number": int(match.group(1)),
                "stem": clean_text(match.group(2)),
                "options": [],
            }
            inline = _split_option_text(current["stem"])
            if inline:
                current["stem"] = clean_text(
                    current["stem"][: OPTION_MARK_RE.search(current["stem"]).start()]
                )
                current["options"] = [
                    {"key": label, "content": content} for label, content in inline
                ]
            continue

        options = _split_option_text(text)
        if current and options:
            current["options"].extend(
                {"key": label, "content": content} for label, content in options
            )
        elif current and re.match(r"^\s*\[?[A-D]\]?", text, re.I):
            label_match = re.match(
                r"^\s*(?:\[|\(|（|【)?([A-D])(?:\]|\)|）|】|[\.．、])?\s*(.*)$",
                text,
                re.I | re.S,
            )
            if label_match:
                current["options"].append(
                    {
                        "key": label_match.group(1).upper(),
                        "content": clean_text(label_match.group(2)),
                    }
                )
        elif current and len(current["options"]) < 4 and question_started:
            current["options"].append(
                {
                    "key": chr(ord("A") + len(current["options"])),
                    "content": text,
                }
            )
        elif not question_started:
            passage_parts.append(text)
    if current:
        questions.append(current)
    return "\n\n".join(passage_parts), questions


def _unlabeled_question_groups(
    segment: list[str], first_number: int
) -> tuple[str, list[dict]]:
    clean = [text for text in segment if not _is_noise(text)]
    # Modern OCR exports produce exactly five stems followed by four bare options each.
    if len(clean) < 25:
        return "\n\n".join(clean), []
    tail = clean[-25:]
    questions = []
    for index in range(5):
        offset = index * 5
        stem = tail[offset]
        options = tail[offset + 1:offset + 5]
        questions.append(
            {
                "number": first_number + index,
                "stem": stem,
                "options": [
                    {"key": chr(ord("A") + option_index), "content": content}
                    for option_index, content in enumerate(options)
                ],
            }
        )
    return "\n\n".join(clean[:-25]), questions


def _parse_reading(blocks: list[str], answers: dict[int, str]) -> list[dict[str, Any]]:
    markers = [
        (
            index,
            1 if match.group(1).lower() in {"l", "i"} else int(match.group(1)),
        )
        for index, text in enumerate(blocks)
        if (match := TEXT_MARK_RE.match(text))
    ]
    part_b = _find_index(blocks, r"^\s*Part\s*B\s*$")
    units = []
    for marker_index, text_number in markers[:4]:
        following = [
            index for index, _ in markers if index > marker_index
        ]
        end = min(following) if following else (part_b if part_b > marker_index else len(blocks))
        segment = blocks[marker_index + 1:end]
        first_number = 21 + (text_number - 1) * 5
        passage, questions = _labeled_question_groups(segment, first_number)
        if len(questions) != 5 or any(len(question["options"]) != 4 for question in questions):
            passage, questions = _unlabeled_question_groups(segment, first_number)
        for question in questions:
            question["answer"] = answers.get(question["number"], "")
            question["score"] = 2.0
        units.append(
            {
                "unit_type": "reading",
                "subtype": "reading_a",
                "title": f"阅读 Text {text_number}",
                "sequence": 1 + text_number,
                "passage": passage,
                "shared_data": {},
                "questions": questions,
            }
        )
    return units


def _part_b_subtype(direction: str) -> str:
    low = direction.lower()
    if "wrong order" in low or "reorganize" in low:
        return "paragraph_reordering"
    if "paragraphs from the list" in low:
        return "paragraph_insertion"
    if "subheading" in low:
        return "heading_matching"
    if "people" in low or "person" in low or "comments" in low or "name" in low:
        return "opinion_matching"
    return "sentence_insertion"


def _part_b_candidate_count(direction: str, subtype: str) -> int:
    range_match = re.search(r"list\s+A\s*[-–—]\s*([GH])", direction, re.I)
    if range_match:
        return ord(range_match.group(1).upper()) - ord("A") + 1
    return 8 if subtype == "paragraph_reordering" else 7


def _parse_part_b(blocks: list[str], answers: dict[int, str]) -> dict[str, Any]:
    direction_index = next(
        (
            index
            for index, text in enumerate(blocks)
            if re.search(r"(?:for\s+)?questions?.*?41.*?45", text, re.I)
            and (
                re.search(r"list\s+A", text, re.I)
                or re.search(r"list\s+A", " ".join(blocks[index : index + 2]), re.I)
                or "numbered" in text.lower()
            )
        ),
        -1,
    )
    if direction_index < 0:
        direction_index = next(
            (
                index
                for index, text in enumerate(blocks)
                if re.search(r"numbered\s+(?:name|person|paragraph)", text, re.I)
                and re.search(r"list\s+A", text, re.I)
            ),
            -1,
        )
    reading_part_b = direction_index
    part_c_candidates = [
        index
        for index in range(max(reading_part_b, 0) + 1, len(blocks))
        if re.match(r"^\s*Part\s*C(?:\s+Directions:)?\s*$", blocks[index], re.I)
        or re.search(r"→\s*Part\s*C\s*$", blocks[index], re.I)
    ]
    part_c = part_c_candidates[0] if part_c_candidates else -1
    section = (
        blocks[reading_part_b:part_c]
        if reading_part_b >= 0 and part_c > reading_part_b
        else []
    )
    direction_parts: list[str] = []
    for text in section[:3]:
        if not direction_parts or re.search(
            r"list\s+A|numbered|extra choices|fit in|coherent text|ANSWER SHEET",
            text,
            re.I,
        ):
            direction_parts.append(text)
        else:
            break
    direction = clean_text(" ".join(direction_parts))
    candidate_map: dict[str, str] = {}
    material: list[str] = []
    for text in section[len(direction_parts) :]:
        if _is_noise(text) or text == direction:
            continue
        match = re.match(r"^\s*\[([A-H])\]\s*(.*)$", text, re.I | re.S)
        if match:
            candidate_map[match.group(1).upper()] = clean_text(match.group(2))
        else:
            material.append(text)

    subtype = _part_b_subtype(direction)
    if not candidate_map:
        usable = [
            text
            for text in material
            if not PAGE_FOOTER_RE.search(text)
            and not re.search(r"(?:41\.){2}|→|ANSWER SHEET", text, re.I)
        ]
        candidate_count = _part_b_candidate_count(direction, subtype)
        if len(usable) >= candidate_count:
            inferred = usable[-candidate_count:]
            candidate_map = {
                chr(ord("A") + index): text for index, text in enumerate(inferred)
            }
            material = material[: len(material) - candidate_count]

    questions = []
    for number in range(41, 46):
        questions.append(
            {
                "number": number,
                "stem": f"位置 {number}",
                "options": [
                    {"key": key, "content": value}
                    for key, value in sorted(candidate_map.items())
                ],
                "answer": answers.get(number, ""),
                "score": 2.0,
            }
        )
    return {
        "unit_type": "part_b",
        "subtype": subtype,
        "title": "阅读 Part B",
        "sequence": 6,
        "passage": "\n\n".join(material),
        "shared_data": {
            "directions": direction,
            "candidates": candidate_map,
        },
        "questions": questions,
    }


def validate_draft(draft: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    answers = draft["answers"]
    missing_answers = [number for number in range(1, 46) if not answers.get(str(number))]
    if missing_answers:
        warnings.append(f"缺少标准答案：{missing_answers}")
    units = draft["units"]
    if len(units) != 6:
        warnings.append(f"应识别6个练习单元，当前为{len(units)}个")
    cloze = next((unit for unit in units if unit["unit_type"] == "cloze"), None)
    if not cloze or len(cloze["questions"]) != 20:
        warnings.append("完型填空未识别为20题")
    elif any(len(question["options"]) != 4 for question in cloze["questions"]):
        bad = [
            question["number"]
            for question in cloze["questions"]
            if len(question["options"]) != 4
        ]
        warnings.append(f"完型填空选项数量异常：{bad}")
    readings = [unit for unit in units if unit["unit_type"] == "reading"]
    if len(readings) != 4:
        warnings.append(f"阅读文章应为4篇，当前为{len(readings)}篇")
    for unit in readings:
        if len(unit["questions"]) != 5:
            warnings.append(f"{unit['title']} 未识别为5题")
        for question in unit["questions"]:
            if len(question["options"]) != 4:
                warnings.append(f"第{question['number']}题选项数量不是4")
    part_b = next((unit for unit in units if unit["unit_type"] == "part_b"), None)
    if not part_b or len(part_b["questions"]) != 5:
        warnings.append("Part B 未识别为5题")
    elif not 7 <= len(part_b.get("shared_data", {}).get("candidates", {})) <= 8:
        warnings.append(
            "Part B 候选项数量异常："
            f"{len(part_b.get('shared_data', {}).get('candidates', {}))}"
        )
    elif any(
        question.get("answer")
        and question["answer"]
        not in part_b.get("shared_data", {}).get("candidates", {})
        for question in part_b["questions"]
    ):
        warnings.append("Part B 标准答案未能对应候选项")
    for unit in units:
        for question in unit["questions"]:
            if not question.get("answer"):
                warnings.append(f"第{question['number']}题没有答案")
    return list(dict.fromkeys(warnings))


def parse_exam(path: Path, answer_path: Path | None = None) -> dict[str, Any]:
    blocks, detected_format, converted = extract_blocks(path)
    try:
        year_match = re.search(r"(20\d{2})", path.name)
        if not year_match:
            year_match = re.search(r"(20\d{2})", " ".join(blocks[:10]))
        year = int(year_match.group(1)) if year_match else None
        answer_key = extract_answer_key(blocks)
        answer_source = "Word 文档内置答案"
        if len(answer_key) < 45:
            companion = answer_path or find_companion_answer_pdf(path, year)
            if companion:
                pdf_answers = extract_pdf_answer_key(companion)
                answer_key.update(pdf_answers)
                answer_source = companion.name
        units = [_parse_cloze(blocks, answer_key)]
        units.extend(_parse_reading(blocks, answer_key))
        units.append(_parse_part_b(blocks, answer_key))
        for unit in units:
            if unit["unit_type"] == "cloze":
                unit["passage"] = _ensure_numbered_blanks(
                    unit.get("passage", ""),
                    range(1, 21),
                )
                unit["passage"] = _remove_duplicate_cloze_number_noise(
                    unit["passage"]
                )
                unit["passage"] = repair_inline_blank_paragraph_breaks(
                    unit["passage"]
                )
            elif unit["unit_type"] == "part_b":
                unit["passage"] = _ensure_numbered_blanks(
                    unit.get("passage", ""),
                    range(41, 46),
                )
                unit["passage"] = repair_inline_blank_paragraph_breaks(
                    unit["passage"]
                )
        draft = {
            "year": year,
            "subject": "英语一",
            "title": f"{year}年考研英语一真题" if year else path.stem,
            "detected_format": detected_format,
            "source_file": path.name,
            "answer_source": answer_source,
            "answers": {str(key): value for key, value in answer_key.items()},
            "units": units,
        }
        draft["warnings"] = validate_draft(draft)
        return draft
    finally:
        if converted:
            shutil.rmtree(converted.parent, ignore_errors=True)


def discover_exam_files(folder: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and path.suffix.lower() in {".doc", ".docx"}
            and re.search(r"20\d{2}", path.name)
        ),
        key=lambda path: int(re.search(r"20\d{2}", path.name).group()),
    )


def import_exam_folder(
    connection: Any,
    folder: Path,
    *,
    publish_valid: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in discover_exam_files(folder):
        draft = parse_exam(path)
        warnings = validate_draft(draft)
        existing = connection.execute(
            "SELECT id FROM import_jobs WHERE filename = ? ORDER BY id DESC LIMIT 1",
            (path.name,),
        ).fetchone()
        if existing:
            job_id = existing["id"]
            connection.execute(
                """
                UPDATE import_jobs
                SET detected_year = ?, detected_format = ?, status = 'draft',
                    draft_data = ?, warnings = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    draft.get("year"),
                    draft.get("detected_format"),
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                    job_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO import_jobs
                    (filename, stored_path, detected_year, detected_format,
                     status, draft_data, warnings)
                VALUES (?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    path.name,
                    str(path),
                    draft.get("year"),
                    draft.get("detected_format"),
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                ),
            )
            job_id = cursor.lastrowid

        paper_id = None
        status = "draft"
        if publish_valid and not warnings:
            paper_id = publish_draft(connection, draft, path.name)
            status = "published"
            connection.execute(
                """
                UPDATE import_jobs
                SET status = 'published', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
        results.append(
            {
                "year": draft.get("year"),
                "filename": path.name,
                "job_id": job_id,
                "paper_id": paper_id,
                "status": status,
                "warnings": warnings,
                "answer_source": draft.get("answer_source"),
            }
        )
    connection.commit()
    return results


def publish_draft(connection: Any, draft: dict[str, Any], source_file: str) -> int:
    year = draft.get("year")
    if not year:
        raise ValueError("试卷年份不能为空")
    cursor = connection.execute(
        """
        INSERT INTO papers (year, subject, title, source_file, status)
        VALUES (?, ?, ?, ?, 'published')
        ON CONFLICT(year) DO UPDATE SET
            subject = excluded.subject,
            title = excluded.title,
            source_file = excluded.source_file,
            status = 'published',
            updated_at = CURRENT_TIMESTAMP
        """,
        (year, draft.get("subject", "英语一"), draft["title"], source_file),
    )
    paper = connection.execute("SELECT id FROM papers WHERE year = ?", (year,)).fetchone()
    paper_id = paper["id"]
    connection.execute("DELETE FROM units WHERE paper_id = ?", (paper_id,))
    for unit in draft["units"]:
        unit_cursor = connection.execute(
            """
            INSERT INTO units
                (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                unit["unit_type"],
                unit.get("subtype"),
                unit["title"],
                unit["sequence"],
                unit.get("passage", ""),
                json.dumps(unit.get("shared_data", {}), ensure_ascii=False),
            ),
        )
        unit_id = unit_cursor.lastrowid
        for sequence, question in enumerate(unit["questions"], 1):
            question_cursor = connection.execute(
                """
                INSERT INTO questions
                    (unit_id, number, stem, question_type, answer, score, sequence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    unit_id,
                    question["number"],
                    question.get("stem", ""),
                    "single_choice",
                    question["answer"],
                    question["score"],
                    sequence,
                    json.dumps(question.get("metadata", {}), ensure_ascii=False),
                ),
            )
            question_id = question_cursor.lastrowid
            for option_sequence, option in enumerate(question.get("options", []), 1):
                connection.execute(
                    """
                    INSERT INTO options
                        (question_id, stable_key, original_label, content, sequence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        question_id,
                        option["key"],
                        option["key"],
                        option["content"],
                        option_sequence,
                    ),
                )
    connection.commit()
    return paper_id
