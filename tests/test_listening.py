from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class ListeningAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.database_path = root / "test.db"
        cls.question_bank_dir = root / "question_banks"
        cls.question_bank_dir.mkdir()
        cls.patches = [
            patch("backend.app.database.DATABASE_PATH", cls.database_path),
            patch(
                "backend.app.services.listening.QUESTION_BANK_DIR",
                cls.question_bank_dir,
            ),
        ]
        for active_patch in cls.patches:
            active_patch.start()
        from backend.app.database import initialize_database

        initialize_database()

    @classmethod
    def tearDownClass(cls) -> None:
        for active_patch in reversed(cls.patches):
            active_patch.stop()
        try:
            cls.temp.cleanup()
        except PermissionError:
            time.sleep(0.1)
            try:
                cls.temp.cleanup()
            except PermissionError:
                pass

    def _paper_with_unit(self, connection, *, year: int) -> tuple[int, int]:
        paper = connection.execute(
            """
            INSERT INTO papers
                (profile_id, year, subject, title, source_file, status, external_key)
            VALUES (1, ?, '大学英语四级', ?, 'paper.docx', 'published', ?)
            """,
            (year, f"{year} 年四级真题", f"test:{year}"),
        )
        paper_id = int(paper.lastrowid)
        unit = connection.execute(
            """
            INSERT INTO units
                (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
            VALUES (?, 'listening', 'news_report', '听力 Section A', 1, '', '{}')
            """,
            (paper_id,),
        )
        return paper_id, int(unit.lastrowid)

    def test_audio_is_persisted_and_exposed_by_serialized_unit(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import attach_listening_assets
        from backend.app.services.questions import serialize_unit

        audio = Path(self.temp.name) / "cet-listening.mp3"
        audio.write_bytes(b"ID3-test-audio")
        with connect() as connection:
            paper_id, unit_id = self._paper_with_unit(connection, year=2091)
            tracks = attach_listening_assets(
                connection,
                paper_id,
                [audio],
                ["2091 年四级听力.mp3"],
            )
            connection.commit()
            unit = serialize_unit(
                connection,
                unit_id,
                shuffle_options=False,
            )
            asset = connection.execute(
                """
                SELECT stored_path, media_type
                FROM question_bank_assets
                WHERE package_id = ? AND asset_id = 'listening.track.1'
                """,
                (f"local.paper-{paper_id}",),
            ).fetchone()

        self.assertEqual(len(tracks), 1)
        self.assertEqual(asset["media_type"], "audio/mpeg")
        self.assertTrue(Path(asset["stored_path"]).is_file())
        self.assertEqual(
            unit["shared_data"]["audio_tracks"][0]["url"],
            f"/api/question-banks/assets/local.paper-{paper_id}/1.0.0/listening.track.1",
        )

    def test_older_published_import_can_recover_uploaded_audio(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import repair_published_listening_assets

        audio = Path(self.temp.name) / "legacy.wav"
        audio.write_bytes(b"RIFF-test-audio")
        with connect() as connection:
            paper_id, unit_id = self._paper_with_unit(connection, year=2092)
            context = {
                "published_paper_ids": [paper_id],
                "audio_paths": [str(audio)],
                "audio_names": ["旧版听力.wav"],
            }
            connection.execute(
                """
                INSERT INTO import_jobs
                    (profile_id, filename, stored_path, status, draft_data,
                     warnings, parse_context)
                VALUES (1, 'legacy.docx', 'legacy.docx', 'published', '{}', '[]', ?)
                """,
                (json.dumps(context, ensure_ascii=False),),
            )
            connection.commit()
            repaired = repair_published_listening_assets(connection)
            shared = json.loads(
                connection.execute(
                    "SELECT shared_data FROM units WHERE id = ?",
                    (unit_id,),
                ).fetchone()["shared_data"]
            )

        self.assertEqual(repaired, 1)
        self.assertEqual(shared["audio_tracks"][0]["media_type"], "audio/wav")


if __name__ == "__main__":
    unittest.main()
