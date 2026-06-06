"""Tests for pure helpers in atlas.assets.llm.rewrites."""

from datetime import UTC, datetime

import polars as pl

from atlas.assets.llm.rewrites import build_prompt, build_rewrite_df, parse_rewrite


class TestBuildPrompt:
    def test_includes_all_fields(self) -> None:
        prompt = build_prompt("EGFR", "Epidermal growth factor receptor", "Mediates growth.")
        assert "EGFR" in prompt
        assert "Epidermal growth factor receptor" in prompt
        assert "Mediates growth." in prompt

    def test_none_function_raw_yields_placeholder(self) -> None:
        prompt = build_prompt("EGFR", "Epidermal growth factor receptor", None)
        assert "none available" in prompt.lower()

    def test_none_gene_symbol_omits_gene_line(self) -> None:
        prompt = build_prompt(None, "Some protein", "Does X.")
        assert "Gene:" not in prompt
        assert "Some protein" in prompt

    def test_all_none_returns_string(self) -> None:
        prompt = build_prompt(None, None, None)
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestParseRewrite:
    def test_valid_json_both_fields(self) -> None:
        text = '{"function_friendly": "It does X.", "tagline": "Short line."}'
        ff, tl = parse_rewrite(text)
        assert ff == "It does X."
        assert tl == "Short line."

    def test_invalid_json_returns_nones(self) -> None:
        ff, tl = parse_rewrite("not json at all")
        assert ff is None
        assert tl is None

    def test_null_json_values_return_nones(self) -> None:
        text = '{"function_friendly": null, "tagline": null}'
        ff, tl = parse_rewrite(text)
        assert ff is None
        assert tl is None

    def test_missing_tagline_returns_none_for_tagline(self) -> None:
        text = '{"function_friendly": "It does X."}'
        ff, tl = parse_rewrite(text)
        assert ff == "It does X."
        assert tl is None

    def test_surrounding_whitespace_handled(self) -> None:
        text = '\n  {"function_friendly": "It does X.", "tagline": "Short."}\n'
        ff, tl = parse_rewrite(text)
        assert ff == "It does X."
        assert tl == "Short."

    def test_non_dict_json_returns_nones(self) -> None:
        ff, tl = parse_rewrite('["not", "a", "dict"]')
        assert ff is None
        assert tl is None

    def test_non_string_field_values_are_rejected(self) -> None:
        text = '{"function_friendly": 42, "tagline": true}'
        ff, tl = parse_rewrite(text)
        assert ff is None
        assert tl is None

    def test_empty_string_input_returns_nones(self) -> None:
        ff, tl = parse_rewrite("")
        assert ff is None
        assert tl is None

    def test_markdown_fenced_json_is_parsed(self) -> None:
        text = '```json\n{"function_friendly": "It does X.", "tagline": "Short."}\n```'
        ff, tl = parse_rewrite(text)
        assert ff == "It does X."
        assert tl == "Short."


class TestBuildRewriteDf:
    def test_correct_columns(self) -> None:
        now = datetime.now(UTC)
        df = build_rewrite_df(
            accessions=["P00001", "P00002"],
            function_friendlies=["Does A.", None],
            taglines=["Tag A.", "Tag B."],
            generated_at=now,
        )
        assert set(df.columns) == {
            "uniprot_accession",
            "function_friendly",
            "tagline",
            "model_id",
            "generated_at",
        }

    def test_row_count_matches_input(self) -> None:
        now = datetime.now(UTC)
        df = build_rewrite_df(
            accessions=["P00001", "P00002", "P00003"],
            function_friendlies=["A.", "B.", None],
            taglines=["T1.", "T2.", "T3."],
            generated_at=now,
        )
        assert df.height == 3

    def test_values_preserved(self) -> None:
        now = datetime.now(UTC)
        df = build_rewrite_df(
            accessions=["P00001"],
            function_friendlies=["It does A."],
            taglines=["Tag A."],
            generated_at=now,
        )
        assert df["uniprot_accession"][0] == "P00001"
        assert df["function_friendly"][0] == "It does A."
        assert df["tagline"][0] == "Tag A."

    def test_null_function_friendly_preserved(self) -> None:
        now = datetime.now(UTC)
        df = build_rewrite_df(
            accessions=["P00001"],
            function_friendlies=[None],
            taglines=["Tag."],
            generated_at=now,
        )
        assert df["function_friendly"].dtype == pl.String
        assert df["function_friendly"][0] is None
