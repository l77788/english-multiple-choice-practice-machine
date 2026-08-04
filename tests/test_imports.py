from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter


class ImportAnswerFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = TemporaryDirectory()
        temp_root = Path(cls.temp.name)
        cls.database_path = temp_root / "test.db"
        cls.upload_dir = temp_root / "uploads"
        cls.upload_dir.mkdir()
        cls.patches = [
            patch("backend.app.database.DATABASE_PATH", cls.database_path),
            patch("backend.app.config.UPLOAD_DIR", cls.upload_dir),
            patch("backend.app.routers.imports.UPLOAD_DIR", cls.upload_dir),
        ]
        for active_patch in cls.patches:
            active_patch.start()

        from backend.app.database import initialize_database
        from backend.app.main import app

        initialize_database()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        for active_patch in reversed(cls.patches):
            active_patch.stop()
        try:
            cls.temp.cleanup()
        except PermissionError:
            pass

    def test_answer_attachment_extension_is_validated(self) -> None:
        response = self.client.post(
            "/api/imports",
            files={
                "file": ("paper.docx", io.BytesIO(b"paper"), "application/octet-stream"),
                "answer_file": (
                    "answers.txt",
                    io.BytesIO(b"answers"),
                    "text/plain",
                ),
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("答案文件仅支持", response.json()["detail"])

    def test_scanned_pdf_answer_requires_manual_entry(self) -> None:
        from backend.app.services.docx_parser import extract_answer_attachment

        path = Path(self.temp.name) / "scan.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with path.open("wb") as stream:
            writer.write(stream)

        answers, status = extract_answer_attachment(path)
        self.assertEqual(answers, {})
        self.assertEqual(status["status"], "manual_required")

    def test_manual_answer_endpoint_updates_questions_and_confirms(self) -> None:
        from backend.app.database import connect

        draft = {
            "year": 2002,
            "answers": {"1": ""},
            "answer_sources": {"1": "answers.pdf"},
            "answer_status": {"status": "manual_required"},
            "answers_confirmed": False,
            "units": [
                {
                    "unit_type": "reading",
                    "subtype": "reading_a",
                    "title": "阅读 Text 1",
                    "sequence": 2,
                    "passage": "Passage",
                    "shared_data": {},
                    "questions": [
                        {
                            "number": 1,
                            "stem": "Question",
                            "options": [
                                {"key": key, "content": key}
                                for key in ("A", "B", "C", "D")
                            ],
                            "answer": "",
                            "score": 2.0,
                        }
                    ],
                }
            ],
            "warnings": [],
        }
        import json

        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_jobs
                    (filename, stored_path, detected_year, detected_format,
                     status, draft_data, warnings)
                VALUES (?, ?, ?, ?, 'draft', ?, '[]')
                """,
                (
                    "paper.docx",
                    "paper.docx",
                    2002,
                    "docx",
                    json.dumps(draft, ensure_ascii=False),
                ),
            )
            job_id = cursor.lastrowid
            connection.commit()

        response = self.client.patch(
            f"/api/imports/{job_id}/answers",
            json={"answers": {"1": "B"}},
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["draft"]
        self.assertEqual(updated["answers"]["1"], "B")
        self.assertEqual(updated["units"][0]["questions"][0]["answer"], "B")
        self.assertTrue(updated["answers_confirmed"])
        self.assertEqual(updated["answer_sources"]["1"], "人工录入")

        rejected = self.client.patch(
            f"/api/imports/{job_id}/answers",
            json={"answers": {"41": "A"}},
        )
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("不属于当前客观题草稿", rejected.json()["detail"])

    def test_json_draft_answer_edit_is_treated_as_manual_confirmation(self) -> None:
        from backend.app.database import connect
        import json

        draft = {
            "year": 2002,
            "answers": {"1": "A"},
            "answer_sources": {"1": "answers.pdf"},
            "answer_status": {"status": "parsed"},
            "answers_confirmed": False,
            "units": [
                {
                    "unit_type": "reading",
                    "subtype": "reading_a",
                    "title": "阅读 Text 1",
                    "sequence": 2,
                    "passage": "Passage",
                    "shared_data": {},
                    "questions": [
                        {
                            "number": 1,
                            "stem": "Question",
                            "options": [
                                {"key": key, "content": key}
                                for key in ("A", "B", "C", "D")
                            ],
                            "answer": "A",
                            "score": 2.0,
                        }
                    ],
                }
            ],
            "warnings": [],
        }
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_jobs
                    (filename, stored_path, detected_year, detected_format,
                     status, draft_data, warnings)
                VALUES (?, ?, ?, ?, 'draft', ?, '[]')
                """,
                (
                    "paper.docx",
                    "paper.docx",
                    2002,
                    "docx",
                    json.dumps(draft, ensure_ascii=False),
                ),
            )
            job_id = cursor.lastrowid
            connection.commit()

        draft["answers"]["1"] = "B"
        response = self.client.put(
            f"/api/imports/{job_id}",
            json={"draft_data": draft, "reason": "JSON 编辑"},
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["draft"]
        self.assertTrue(updated["answers_confirmed"])
        self.assertEqual(updated["answer_sources"]["1"], "人工录入")

    def _minimal_draft(self) -> dict:
        return {
            "year": 2020,
            "detected_format": "docx",
            "title": "2020年考研英语一真题",
            "source_file": "paper.docx",
            "answer_source": "未提供",
            "answer_status": {
                "status": "missing",
                "message": "试卷 Word 未检测到标准答案",
            },
            "answers_confirmed": False,
            "answer_sources": {},
            "answers": {},
            "units": [
                {
                    "unit_type": "reading",
                    "subtype": "reading_a",
                    "title": "阅读 Text 1",
                    "sequence": 2,
                    "passage": "Passage",
                    "shared_data": {},
                    "questions": [
                        {
                            "number": 21,
                            "stem": "Question",
                            "options": [
                                {"key": key, "content": key}
                                for key in ("A", "B", "C", "D")
                            ],
                            "answer": "",
                            "score": 2.0,
                        }
                    ],
                }
            ],
            "warnings": [],
        }

    def test_upload_model_assist_applies_answers_directly(self) -> None:
        from backend.app.routers import imports as imports_router

        draft = self._minimal_draft()
        with (
            patch.object(imports_router, "parse_exam", return_value=draft),
            patch.object(imports_router, "document_text", return_value="document"),
            patch.object(
                imports_router,
                "run_model_assist",
                return_value=(
                    {"answer_map": {"21": "B"}, "issues": ["第21题答案来自答案区"]},
                    "raw",
                ),
            ),
        ):
            response = self.client.post(
                "/api/imports",
                files={
                    "file": (
                        "paper.docx",
                        io.BytesIO(b"paper"),
                        "application/octet-stream",
                    )
                },
                data={"use_model_assist": "true"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model_assist"]["status"], "applied")
        self.assertEqual(body["draft"]["answers"]["21"], "B")
        self.assertEqual(body["draft"]["answer_sources"]["21"], "模型辅助")
        self.assertTrue(any("模型辅助" in warning for warning in body["warnings"]))

    def test_upload_model_assist_failure_falls_back_to_local(self) -> None:
        from backend.app.routers import imports as imports_router

        draft = self._minimal_draft()
        with (
            patch.object(imports_router, "parse_exam", return_value=draft),
            patch.object(imports_router, "document_text", return_value="document"),
            patch.object(
                imports_router,
                "run_model_assist",
                side_effect=ValueError("模型服务暂时不可用"),
            ),
        ):
            response = self.client.post(
                "/api/imports",
                files={
                    "file": (
                        "paper.docx",
                        io.BytesIO(b"paper"),
                        "application/octet-stream",
                    )
                },
                data={"use_model_assist": "true"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model_assist"]["status"], "failed")
        self.assertEqual(body["model_assist"]["fell_back_to_local"], True)
        self.assertEqual(body["draft"]["answers"], {})

    def test_model_assist_retry_with_other_model(self) -> None:
        from backend.app.database import connect
        from backend.app.routers import imports as imports_router

        draft = self._minimal_draft()
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_jobs
                    (filename, stored_path, detected_year, detected_format,
                     status, draft_data, warnings, parse_context)
                VALUES (?, ?, ?, ?, 'draft', ?, '[]', ?)
                """,
                (
                    "paper.docx",
                    "paper.docx",
                    2020,
                    "docx",
                    json.dumps(draft, ensure_ascii=False),
                    json.dumps({"answer_text": "参考答案 21-25 BACDC"}),
                ),
            )
            job_id = cursor.lastrowid
            connection.commit()
        with (
            patch.object(imports_router, "document_text", return_value="document"),
            patch.object(
                imports_router,
                "run_model_assist",
                return_value=(
                    {"answer_map": {"21": "C"}, "issues": []},
                    "raw",
                ),
            ) as mocked,
        ):
            response = self.client.post(
                f"/api/imports/{job_id}/model-assist",
                json={"profile_id": None, "model": "other-model"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model_assist"]["status"], "applied")
        self.assertEqual(body["draft"]["answers"]["21"], "C")
        self.assertEqual(body["draft"]["answer_sources"]["21"], "模型辅助")
        self.assertEqual(mocked.call_args.kwargs["model"], "other-model")
        with connect() as connection:
            saved = json.loads(
                connection.execute(
                    "SELECT draft_data FROM import_jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()["draft_data"]
            )
        self.assertEqual(saved["answers"]["21"], "C")


if __name__ == "__main__":
    unittest.main()
