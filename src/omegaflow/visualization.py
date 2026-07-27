"""Safe syntax-token generation for authored visualization panes."""

from __future__ import annotations

from dataclasses import dataclass

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token
from pygments.util import ClassNotFound


class VisualizationError(ValueError):
    """Raised when authored visualization content cannot be tokenized."""


@dataclass(frozen=True)
class VisualizationToken:
    start: int
    end: int
    kind: str


def syntax_tokens(language: str, text: str) -> tuple[VisualizationToken, ...]:
    """Return bounded code-point ranges without interpreting text as markup."""

    try:
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except ClassNotFound as exc:
        raise VisualizationError(
            f"visualization language {language!r} is not supported"
        ) from exc
    result: list[VisualizationToken] = []
    offset = 0
    for token_type, value in lex(text, lexer):
        end = offset + len(value)
        kind = _token_kind(token_type)
        if kind is not None and end > offset:
            if result and result[-1].kind == kind and result[-1].end == offset:
                previous = result[-1]
                result[-1] = VisualizationToken(
                    start=previous.start,
                    end=end,
                    kind=kind,
                )
            else:
                result.append(
                    VisualizationToken(start=offset, end=end, kind=kind)
                )
        offset = end
    if offset != len(text):  # pragma: no cover - Pygments contract
        raise VisualizationError("visualization tokenizer did not consume all text")
    if len(result) > 10_000:
        raise VisualizationError(
            "visualization syntax produced more than 10000 token ranges"
        )
    return tuple(result)


def _token_kind(token_type: object) -> str | None:
    if token_type in Token.Comment:
        return "comment"
    if token_type in Token.Keyword.Constant:
        return "boolean"
    if token_type in Token.Keyword:
        return "keyword"
    if token_type in Token.Name.Tag or token_type in Token.Name.Attribute:
        return "key"
    if token_type in Token.Literal.String:
        return "string"
    if token_type in Token.Literal.Number:
        return "number"
    if token_type in Token.Operator:
        return "operator"
    if token_type in Token.Punctuation:
        return "punctuation"
    return None
