"""Tests for the persistent ANC coefficient injection tool."""

from __future__ import annotations

import pytest

from tools.anc_coeff_inject import parse_words


class TestWordParsing:
    def test_hex_and_decimal(self) -> None:
        words = parse_words(",".join(["0x10000000"] + ["0"] * 9))
        assert len(words) == 10
        assert words[0] == 0x10000000
        assert words[5] == 0

    def test_wrong_count_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_words("0x10000000,0")
