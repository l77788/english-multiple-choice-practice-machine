from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from backend.app.database import SCHEMA
from backend.app.services.vocabulary import (
    add_vocabulary,
    clean_machine_meanings,
    clean_meaning,
    model_text,
    normalize_term,
    translate_queued_vocabulary,
    vocabulary_key,
)


class VocabularyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def tearDown(self) -> None:
        self.connection.close()

    def test_duplicate_add_increments_count_and_becomes_frequent(self) -> None:
        first = add_vocabulary(
            self.connection,
            {
                "term": "Claims",
                "context_sentence": "The author claims that the policy is effective.",
            },
        )
        second = add_vocabulary(
            self.connection,
            {
                "term": "claims",
                "context_sentence": "The company claims a different result.",
            },
        )
        self.assertTrue(first["is_new"])
        self.assertFalse(first["is_frequent"])
        self.assertFalse(second["is_new"])
        self.assertEqual(second["encounter_count"], 2)
        self.assertTrue(second["is_frequent"])
        occurrence_count = self.connection.execute(
            "SELECT COUNT(*) FROM vocabulary_occurrences"
        ).fetchone()[0]
        self.assertEqual(occurrence_count, 2)

    def test_words_wait_in_queue_and_translate_in_one_batch(self) -> None:
        first = add_vocabulary(
            self.connection,
            {
                "term": "claims",
                "context_sentence": "The author claims that the policy is effective.",
            },
        )
        second = add_vocabulary(
            self.connection,
            {
                "term": "evidence",
                "context_sentence": "The evidence supports the conclusion.",
            },
        )
        self.assertEqual(first["translation_status"], "queued")
        self.assertEqual(second["translation_status"], "queued")
        self.connection.execute(
            """
            UPDATE ai_profiles
            SET enabled = 1, default_model = 'test-model'
            WHERE id = (SELECT id FROM ai_profiles ORDER BY id LIMIT 1)
            """
        )
        self.connection.commit()
        response = {
            "translations": [
                {
                    "entryId": first["entry_id"],
                    "lemma": "claim",
                    "phonetic": "",
                    "partOfSpeech": "v.",
                    "contextualMeaning": "声称",
                    "commonMeaning": "声称；主张",
                    "memoryHint": "",
                },
                {
                    "entryId": second["entry_id"],
                    "lemma": "evidence",
                    "phonetic": "",
                    "partOfSpeech": "n.",
                    "contextualMeaning": "证据",
                    "commonMeaning": "证据；证明",
                    "memoryHint": "",
                },
            ]
        }
        with (
            patch(
                "backend.app.services.vocabulary.connect",
                return_value=self.connection,
            ),
            patch(
                "backend.app.services.vocabulary.chat_completion",
                return_value=__import__("json").dumps(response, ensure_ascii=False),
            ) as completion,
        ):
            result = translate_queued_vocabulary()
        self.assertEqual(result["translated"], 2)
        self.assertEqual(completion.call_count, 1)
        rows = self.connection.execute(
            """
            SELECT translation_status, common_meaning
            FROM vocabulary_entries
            ORDER BY id
            """
        ).fetchall()
        self.assertEqual([row["translation_status"] for row in rows], ["ready", "ready"])
        self.assertEqual([row["common_meaning"] for row in rows], ["声称；主张", "证据；证明"])

    def test_normalization_is_case_and_spacing_insensitive(self) -> None:
        self.assertEqual(normalize_term("  Artificial   Intelligence "), "artificial intelligence")

    def test_common_inflections_share_a_vocabulary_key(self) -> None:
        self.assertEqual(vocabulary_key("claims"), "claim")
        self.assertEqual(vocabulary_key("claimed"), "claim")
        self.assertEqual(vocabulary_key("claiming"), "claim")

    def test_more_than_five_words_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            add_vocabulary(
                self.connection,
                {"term": "one two three four five six"},
            )

    def test_model_list_is_rendered_as_readable_text(self) -> None:
        self.assertEqual(model_text(["合法性", "正当性", "合理性"], 100), "合法性、正当性、合理性")

    def test_model_annotations_are_removed_from_meaning(self) -> None:
        self.assertEqual(
            clean_meaning("随机地，任意地（指面试者的选择方式）"),
            "随机地，任意地",
        )
        self.assertEqual(
            clean_meaning("当前语境释义：支持；拥护【本文政策语境】"),
            "支持；拥护",
        )
        self.assertEqual(
            clean_meaning("（因未提供原句，此处给出常见释义）目标；客观的"),
            "目标；客观的",
        )
        self.assertEqual(
            clean_meaning("波动，起伏；在句子中通常指价格或数量不稳定变化"),
            "波动，起伏",
        )

    def test_existing_machine_meanings_are_cleaned_but_user_edits_are_kept(self) -> None:
        self.connection.execute(
            """
            INSERT INTO vocabulary_entries
                (term, normalized_term, contextual_meaning, common_meaning,
                 translation_status, user_edited)
            VALUES
                ('randomly', 'randomly', '随机地（指选择方式）', '随机地【副词】', 'ready', 0),
                ('claim', 'claim', '用户保留（我的注释）', '声称', 'ready', 1)
            """
        )
        self.connection.commit()
        self.assertEqual(clean_machine_meanings(self.connection), 1)
        rows = self.connection.execute(
            "SELECT contextual_meaning, common_meaning FROM vocabulary_entries ORDER BY id"
        ).fetchall()
        self.assertEqual(tuple(rows[0]), ("随机地", "随机地"))
        self.assertEqual(tuple(rows[1]), ("用户保留（我的注释）", "声称"))


if __name__ == "__main__":
    unittest.main()
