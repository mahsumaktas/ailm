"""Tests for ailm.llm.evidence — evidence format validation."""

from ailm.llm.evidence import EvidenceLine, EvidenceValidator, ValidatedOutput


class TestValidateWellFormed:
    """Well-formed output (every content line has a source) passes."""

    def test_all_lines_sourced(self) -> None:
        output = (
            "Disk usage at 82% [Source: psutil, 14:23:01]\n"
            "Journal size 2.3GB [Source: du /var/log/journal]"
        )
        v = EvidenceValidator()
        result = v.validate(output)

        assert result.all_sourced is True
        assert len(result.lines) == 2
        assert result.lines[0].source == "psutil, 14:23:01"
        assert result.lines[1].source == "du /var/log/journal"

    def test_single_sourced_line(self) -> None:
        result = EvidenceValidator().validate(
            "CPU at 45% [Source: psutil]"
        )
        assert result.all_sourced is True
        assert result.lines[0].source == "psutil"


class TestValidateMissingSource:
    """Lines missing [Source: ...] are detected."""

    def test_single_untagged_line(self) -> None:
        result = EvidenceValidator().validate("Consider vacuuming journal logs")
        assert result.all_sourced is False
        assert result.lines[0].source is None

    def test_mixed_tagged_and_untagged(self) -> None:
        output = (
            "Disk usage at 82% [Source: psutil, 14:23:01]\n"
            "Consider vacuuming journal logs\n"
            "Journal size 2.3GB [Source: du /var/log/journal]"
        )
        result = EvidenceValidator().validate(output)

        assert result.all_sourced is False
        assert result.lines[0].source == "psutil, 14:23:01"
        assert result.lines[1].source is None
        assert result.lines[2].source == "du /var/log/journal"


class TestReformat:
    """reformat() adds [Source: LLM analysis] to untagged content lines."""

    def test_adds_source_to_untagged(self) -> None:
        raw = "Consider vacuuming journal logs"
        reformatted = EvidenceValidator().reformat(raw)
        assert reformatted == "Consider vacuuming journal logs [Source: LLM analysis]"

    def test_preserves_existing_source(self) -> None:
        raw = "Disk usage at 82% [Source: psutil, 14:23:01]"
        reformatted = EvidenceValidator().reformat(raw)
        assert reformatted == raw

    def test_mixed_output(self) -> None:
        raw = (
            "Disk usage at 82% [Source: psutil, 14:23:01]\n"
            "Consider vacuuming journal logs\n"
            "Journal size 2.3GB [Source: du /var/log/journal]"
        )
        reformatted = EvidenceValidator().reformat(raw)
        lines = reformatted.split("\n")

        assert lines[0] == "Disk usage at 82% [Source: psutil, 14:23:01]"
        assert lines[1] == "Consider vacuuming journal logs [Source: LLM analysis]"
        assert lines[2] == "Journal size 2.3GB [Source: du /var/log/journal]"

    def test_reformatted_output_validates_clean(self) -> None:
        """After reformat, validate should report all_sourced=True."""
        raw = (
            "Disk at 82% [Source: psutil]\n"
            "Suggestion: vacuum logs\n"
            "RAM ok [Source: psutil]"
        )
        v = EvidenceValidator()
        reformatted = v.reformat(raw)
        result = v.validate(reformatted)
        assert result.all_sourced is True

    def test_custom_fallback_source(self) -> None:
        v = EvidenceValidator(fallback_source="ailm inference")
        reformatted = v.reformat("Some untagged claim")
        assert "[Source: ailm inference]" in reformatted


class TestHeadersAndEmpty:
    """Empty lines and section headers are not flagged as missing."""

    def test_empty_lines_not_flagged(self) -> None:
        output = (
            "Disk at 82% [Source: psutil]\n"
            "\n"
            "RAM at 60% [Source: psutil]"
        )
        result = EvidenceValidator().validate(output)
        assert result.all_sourced is True

    def test_markdown_header_not_flagged(self) -> None:
        output = (
            "## System Status\n"
            "Disk at 82% [Source: psutil]"
        )
        result = EvidenceValidator().validate(output)
        assert result.all_sourced is True

    def test_separator_not_flagged(self) -> None:
        output = (
            "Disk at 82% [Source: psutil]\n"
            "---\n"
            "RAM at 60% [Source: psutil]"
        )
        result = EvidenceValidator().validate(output)
        assert result.all_sourced is True

    def test_reformat_leaves_headers_untouched(self) -> None:
        raw = (
            "## Summary\n"
            "\n"
            "---\n"
            "Disk at 82% [Source: psutil]"
        )
        reformatted = EvidenceValidator().reformat(raw)
        lines = reformatted.split("\n")
        assert lines[0] == "## Summary"
        assert lines[1] == ""
        assert lines[2] == "---"
        assert lines[3] == "Disk at 82% [Source: psutil]"

    def test_whitespace_only_line_not_flagged(self) -> None:
        output = "Disk at 82% [Source: psutil]\n   \nRAM at 60% [Source: psutil]"
        result = EvidenceValidator().validate(output)
        assert result.all_sourced is True

    def test_various_header_levels(self) -> None:
        output = (
            "# Top level\n"
            "## Second level\n"
            "### Third level\n"
            "CPU at 10% [Source: psutil]"
        )
        result = EvidenceValidator().validate(output)
        assert result.all_sourced is True


class TestMalformedOutput:
    """Completely malformed or unexpected output is handled gracefully."""

    def test_empty_string(self) -> None:
        result = EvidenceValidator().validate("")
        assert result.all_sourced is True
        assert len(result.lines) == 0  # "".splitlines() returns []

    def test_only_whitespace(self) -> None:
        result = EvidenceValidator().validate("   \n  \n   ")
        assert result.all_sourced is True

    def test_broken_source_tag(self) -> None:
        """A malformed [Source tag without closing bracket is not recognized."""
        result = EvidenceValidator().validate("Disk at 82% [Source: psutil")
        assert result.all_sourced is False
        assert result.lines[0].source is None

    def test_no_content_just_headers(self) -> None:
        output = "## Header\n---\n\n==="
        result = EvidenceValidator().validate(output)
        assert result.all_sourced is True

    def test_very_long_single_line(self) -> None:
        long_text = "A" * 10000 + " [Source: test]"
        result = EvidenceValidator().validate(long_text)
        assert result.all_sourced is True
        assert result.lines[0].source == "test"

    def test_source_tag_case_insensitive(self) -> None:
        result = EvidenceValidator().validate("Data point [SOURCE: psutil]")
        assert result.all_sourced is True
        assert result.lines[0].source == "psutil"

    def test_reformat_empty_string(self) -> None:
        reformatted = EvidenceValidator().reformat("")
        assert reformatted == ""

    def test_multiple_source_tags_picks_first(self) -> None:
        """If a line has multiple [Source: ...] tags, the first one is extracted."""
        line = "Data [Source: psutil] and more [Source: other]"
        result = EvidenceValidator().validate(line)
        assert result.lines[0].source == "psutil"


class TestMixedOutput:
    """Realistic mixed output with headers, sourced lines, and unsourced lines."""

    def test_realistic_briefing(self) -> None:
        output = (
            "## Morning Briefing\n"
            "\n"
            "Disk usage at 82% [Source: psutil, 06:00:01]\n"
            "Journal size 2.3GB [Source: du /var/log/journal]\n"
            "Consider vacuuming journal logs\n"
            "\n"
            "---\n"
            "3 packages updated overnight [Source: pacman.log]\n"
            "No failed services detected [Source: systemctl]\n"
            "System looks healthy overall"
        )
        v = EvidenceValidator()
        result = v.validate(output)

        assert result.all_sourced is False

        # Count content lines without source
        unsourced = [
            ln for ln in result.lines
            if ln.source is None and ln.text.strip()
            and not ln.text.strip().startswith("#")
            and ln.text.strip() not in ("---", "===")
        ]
        assert len(unsourced) == 2  # "Consider vacuuming" + "System looks healthy"

        # Reformat and re-validate
        reformatted = v.reformat(output)
        result2 = v.validate(reformatted)
        assert result2.all_sourced is True

    def test_all_unsourced_content(self) -> None:
        output = (
            "Something happened\n"
            "Another thing\n"
            "And one more"
        )
        v = EvidenceValidator()
        result = v.validate(output)
        assert result.all_sourced is False

        reformatted = v.reformat(output)
        for line in reformatted.split("\n"):
            assert "[Source: LLM analysis]" in line
