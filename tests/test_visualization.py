from __future__ import annotations

import pytest

from omegaflow.visualization import VisualizationError, syntax_tokens


def test_syntax_tokens_use_unicode_code_point_offsets() -> None:
    text = 'title: "Sunset 🌅"\nready: true'

    tokens = syntax_tokens("yaml", text)

    assert tokens
    for token in tokens:
        assert 0 <= token.start < token.end <= len(text)
        assert text[token.start : token.end]
    string_tokens = [
        text[token.start : token.end] for token in tokens if token.kind == "string"
    ]
    assert any("🌅" in value for value in string_tokens)


def test_syntax_tokens_reject_unknown_language() -> None:
    with pytest.raises(
        VisualizationError,
        match="visualization language 'not-a-language' is not supported",
    ):
        syntax_tokens("not-a-language", "plain text")
