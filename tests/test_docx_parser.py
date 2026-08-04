from __future__ import annotations

import unittest

from lxml import etree

from backend.app.services.docx_parser import (
    NS,
    _ensure_numbered_blanks,
    _extract_ooxml_text,
    _remove_duplicate_cloze_number_noise,
    clean_text,
)
from backend.app.services.passage_cleanup import repair_inline_blank_paragraph_breaks


class OoxmlBlankExtractionTests(unittest.TestCase):
    def test_underlined_question_number_becomes_visible_blank(self) -> None:
        paragraph = etree.fromstring(
            f"""
            <w:p xmlns:w="{NS['w']}">
              <w:r><w:t>The court cannot </w:t></w:r>
              <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t xml:space="preserve"> 1 </w:t></w:r>
              <w:r><w:t> its legitimacy.</w:t></w:r>
            </w:p>
            """
        )
        self.assertEqual(
            clean_text(_extract_ooxml_text(paragraph)),
            "The court cannot 1 ______ its legitimacy.",
        )

    def test_underlined_word_is_not_converted_to_a_blank(self) -> None:
        paragraph = etree.fromstring(
            f"""
            <w:p xmlns:w="{NS['w']}">
              <w:r><w:rPr><w:u w:val="single"/></w:rPr><w:t>Directions</w:t></w:r>
            </w:p>
            """
        )
        self.assertEqual(clean_text(_extract_ooxml_text(paragraph)), "Directions")

    def test_explicit_underscores_are_preserved(self) -> None:
        paragraph = etree.fromstring(
            f"""
            <w:p xmlns:w="{NS['w']}">
              <w:r><w:t>(42) _________</w:t></w:r>
            </w:p>
            """
        )
        self.assertEqual(
            clean_text(_extract_ooxml_text(paragraph)),
            "(42) _________",
        )

    def test_sequential_bare_numbers_become_blanks(self) -> None:
        passage = (
            "The site dates to 3500 B.C. It is 1 prone to earthquakes, "
            "which caused it to 2 sink. The rise 3 covered the city."
        )
        self.assertEqual(
            _ensure_numbered_blanks(passage, range(1, 4)),
            "The site dates to 3500 B.C. It is 1 ______ prone to earthquakes, "
            "which caused it to 2 ______ sink. The rise 3 ______ covered the city.",
        )

    def test_part_b_parenthesized_positions_are_normalized(self) -> None:
        self.assertEqual(
            _ensure_numbered_blanks(
                "First paragraph. (41) Second paragraph. (42) _________ Third.",
                range(41, 43),
            ),
            "First paragraph. 41 ______ Second paragraph. 42 ______ Third.",
        )

    def test_missing_number_at_broken_text_frame_is_recovered(self) -> None:
        passage = (
            "shifting 6 and climate change eroded a barrier that\n\n"
            "Pavlopetri. A survey was\n\n"
            "data to analyze sea levels 9 British researchers returned."
        )
        repaired = _ensure_numbered_blanks(passage, range(6, 10))
        self.assertIn("barrier that 7 ______ Pavlopetri", repaired)
        self.assertIn("survey was 8 ______ data", repaired)

    def test_duplicate_early_cloze_number_noise_is_removed(self) -> None:
        passage = (
            "Our lives. 1 ______ 9 AI also has the potential hazard of "
            "2 ______ changing experiences. Later they 9 ______ their preferences."
        )
        self.assertEqual(
            _remove_duplicate_cloze_number_noise(passage),
            "Our lives. 1 ______ AI also has the potential hazard of "
            "2 ______ changing experiences. Later they 9 ______ their preferences.",
        )

    def test_inline_blank_does_not_start_a_false_paragraph(self) -> None:
        passage = (
            "At first glance this might seem like a strength that\n\n"
            "1 ______ the ability to make judgments.\n\n"
            "A genuine new paragraph starts here."
        )
        self.assertEqual(
            repair_inline_blank_paragraph_breaks(passage),
            "At first glance this might seem like a strength that "
            "1 ______ the ability to make judgments.\n\n"
            "A genuine new paragraph starts here.",
        )

    def test_sentence_ending_before_blank_keeps_paragraph_break(self) -> None:
        passage = "Here are some tips:\n\n41 ______ First tip."
        self.assertEqual(
            repair_inline_blank_paragraph_breaks(passage),
            "Here are some tips: 41 ______ First tip.",
        )

        completed = "This is a complete sentence.\n\n41 ______ New paragraph."
        self.assertEqual(
            repair_inline_blank_paragraph_breaks(completed),
            completed,
        )


if __name__ == "__main__":
    unittest.main()
