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

    def test_multiple_audio_tracks_are_mapped_to_listening_sections(self) -> None:
        from backend.app.database import connect
        from backend.app.services.listening import attach_listening_assets

        audio_files = []
        for index in range(1, 4):
            audio = Path(self.temp.name) / f"section-{index}.mp3"
            audio.write_bytes(f"ID3-section-{index}".encode())
            audio_files.append(audio)

        with connect() as connection:
            paper_id, _ = self._paper_with_unit(connection, year=2093)
            for sequence, title in ((2, "听力 Section B"), (3, "听力 Section C")):
                connection.execute(
                    """
                    INSERT INTO units
                        (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
                    VALUES (?, 'listening', 'passage', ?, ?, '', '{}')
                    """,
                    (paper_id, title, sequence),
                )
            attach_listening_assets(
                connection,
                paper_id,
                audio_files,
                [path.name for path in audio_files],
            )
            rows = connection.execute(
                """
                SELECT shared_data FROM units
                WHERE paper_id = ? AND unit_type = 'listening'
                ORDER BY sequence
                """,
                (paper_id,),
            ).fetchall()

        payloads = [json.loads(row["shared_data"]) for row in rows]
        self.assertEqual(
            [payload["audio_tracks"][0]["asset_id"] for payload in payloads],
            ["listening.track.1", "listening.track.2", "listening.track.3"],
        )
        self.assertTrue(
            all(payload["audio_mode"] == "per_unit" for payload in payloads)
        )

    def test_complete_listening_practice_selects_all_sections_of_one_paper(self) -> None:
        from backend.app.database import connect
        from backend.app.routers.dashboard import dashboard
        from backend.app.schemas import PracticeCreate
        from backend.app.services.practice import create_session

        with connect() as connection:
            paper_id, _ = self._paper_with_unit(connection, year=2094)
            for sequence, title in ((2, "听力 Section B"), (3, "听力 Section C")):
                connection.execute(
                    """
                    INSERT INTO units
                        (paper_id, unit_type, subtype, title, sequence, passage, shared_data)
                    VALUES (?, 'listening', 'passage', ?, ?, '', '{}')
                    """,
                    (paper_id, title, sequence),
                )
            for unit in connection.execute(
                "SELECT id FROM units WHERE paper_id = ? ORDER BY sequence",
                (paper_id,),
            ).fetchall():
                connection.execute(
                    """
                    INSERT INTO questions
                        (unit_id, number, stem, answer, score, sequence)
                    VALUES (?, 1, '听力题', 'A', 1, 1)
                    """,
                    (unit["id"],),
                )
            session = create_session(
                connection,
                PracticeCreate(
                    mode="random",
                    paper_id=paper_id,
                    unit_type="listening",
                    selection_scope="paper_unit_type",
                    count=1,
                    shuffle_options=True,
                ),
            )
            overview = dashboard(connection)

        self.assertEqual(session["paper_id"], paper_id)
        self.assertEqual(len(session["units"]), 3)
        self.assertTrue(
            all(unit["unit_type"] == "listening" for unit in session["units"])
        )
        self.assertGreaterEqual(overview["paper_type_counts"]["listening"], 1)
        self.assertGreaterEqual(overview["unit_type_counts"]["listening"], 3)


if __name__ == "__main__":
    unittest.main()
