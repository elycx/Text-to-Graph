"""Input normalization before LLM extraction."""

from __future__ import annotations

import re


def preprocess_text(text: str) -> str:
    """Normalize text without doing semantic graph parsing."""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2192": " -> ",
        "\u21d2": " -> ",
        "\u2013": "-",
        "\u2014": "-",
    }
    normalized = str(text)
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()

