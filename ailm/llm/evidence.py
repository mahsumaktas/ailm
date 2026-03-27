"""Evidence format validation for LLM outputs.

Every LLM claim must cite its source: `[DATA] -> [Source: X]`.
This module validates post-generation output and reformats
lines that are missing source tags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Matches [Source: ...] anywhere in a line.
# Captures the content inside the brackets.
_SOURCE_PATTERN = re.compile(r"\[Source:\s*([^\]]+)\]", re.IGNORECASE)

# Lines that look like section headers: markdown headers, separator lines,
# bullet-only lines, or lines that are all punctuation/whitespace.
_HEADER_PATTERN = re.compile(
    r"^("
    r"\s*#{1,6}\s+.*"       # markdown headers
    r"|\s*[-=]{3,}\s*"      # separator lines (---, ===)
    r"|\s*\*{3,}\s*"        # *** separators
    r"|\s*$"                 # empty / whitespace-only
    r")$"
)

DEFAULT_FALLBACK_SOURCE = "LLM analysis"


@dataclass(frozen=True)
class EvidenceLine:
    """A single line from LLM output with its extracted source."""

    text: str
    source: str | None  # None = no source tag found


@dataclass
class ValidatedOutput:
    """Result of validating an LLM output block."""

    lines: list[EvidenceLine] = field(default_factory=list)
    all_sourced: bool = True  # True if every content line has a source


def _is_header_or_empty(line: str) -> bool:
    """Return True if line is a section header, separator, or empty."""
    return _HEADER_PATTERN.match(line) is not None


def _extract_source(line: str) -> str | None:
    """Extract source tag content from a line, or None if absent."""
    match = _SOURCE_PATTERN.search(line)
    return match.group(1).strip() if match else None


class EvidenceValidator:
    """Validates and reformats LLM output for evidence sourcing."""

    def __init__(self, fallback_source: str = DEFAULT_FALLBACK_SOURCE) -> None:
        self._fallback_source = fallback_source

    def validate(self, llm_output: str) -> ValidatedOutput:
        """Parse LLM output, check every claim has a [Source: ...] tag.

        Header lines and empty lines are included in the result but
        do not affect the all_sourced flag.
        """
        result = ValidatedOutput()

        for raw_line in llm_output.splitlines():
            line = raw_line.rstrip()

            if _is_header_or_empty(line):
                result.lines.append(EvidenceLine(text=line, source=None))
                continue

            source = _extract_source(line)
            result.lines.append(EvidenceLine(text=line, source=source))

            if source is None:
                result.all_sourced = False

        return result

    def reformat(self, raw_output: str) -> str:
        """Add [Source: <fallback>] to content lines missing source tags.

        Header and empty lines are left untouched.
        Lines that already have a source tag are left untouched.
        """
        reformatted: list[str] = []

        for raw_line in raw_output.splitlines():
            line = raw_line.rstrip()

            if _is_header_or_empty(line):
                reformatted.append(line)
                continue

            if _extract_source(line) is not None:
                reformatted.append(line)
                continue

            reformatted.append(f"{line} [Source: {self._fallback_source}]")

        return "\n".join(reformatted)
