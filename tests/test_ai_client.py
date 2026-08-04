from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from backend.app.services.ai_client import (
    _extract_message_content,
    _model_list_urls,
    _parse_model_list,
    list_available_models,
)


class ModelListTests(unittest.TestCase):
    def test_extracts_text_from_string_and_content_parts(self) -> None:
        self.assertEqual(_extract_message_content("  分析正文  "), "分析正文")
        self.assertEqual(
            _extract_message_content(
                [
                    {"type": "text", "text": "第一段"},
                    {"type": "text", "text": {"value": "第二段"}},
                ]
            ),
            "第一段\n第二段",
        )
        self.assertEqual(_extract_message_content(None), "")

    def test_openai_model_list_shape(self) -> None:
        models = _parse_model_list(
            {
                "object": "list",
                "data": [
                    {"id": "model-b", "owned_by": "provider"},
                    {"id": "model-a", "owned_by": "provider"},
                ],
            }
        )
        self.assertEqual([model["id"] for model in models], ["model-a", "model-b"])

    def test_ollama_model_list_shape(self) -> None:
        models = _parse_model_list(
            {"models": [{"name": "qwen3:8b"}, {"model": "gemma3:4b"}]}
        )
        self.assertEqual(
            [model["id"] for model in models],
            ["gemma3:4b", "qwen3:8b"],
        )

    def test_v1_url_falls_back_to_ollama_tags(self) -> None:
        self.assertEqual(
            _model_list_urls("http://127.0.0.1:11434/v1"),
            [
                ("openai", "http://127.0.0.1:11434/v1/models"),
                ("ollama", "http://127.0.0.1:11434/api/tags"),
            ],
        )

    def test_fetch_falls_back_to_ollama_native_endpoint(self) -> None:
        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                self.calls: list[str] = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url: str, **kwargs):
                self.calls.append(url)
                if url.endswith("/v1/models"):
                    return httpx.Response(
                        404,
                        request=httpx.Request("GET", url),
                    )
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3:8b"}]},
                    request=httpx.Request("GET", url),
                )

        with patch("backend.app.services.ai_client.httpx.Client", FakeClient):
            result = list_available_models(
                object(),
                base_url="http://127.0.0.1:11434/v1",
            )
        self.assertEqual(result["source"], "ollama")
        self.assertEqual(result["models"][0]["id"], "qwen3:8b")


if __name__ == "__main__":
    unittest.main()
