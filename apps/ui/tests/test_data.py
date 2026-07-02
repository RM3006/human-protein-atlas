"""Value-checking tests for the Streamlit data layer (apps/ui/data.py).

Centred on the ligand -> receptor -> drug navigation rule: insulin has partners but no
drug; its receptor INSR carries the drug.
"""

import duckdb

from apps.ui import data


def test_story_card_insulin_has_partners_but_no_drug(conn: duckdb.DuckDBPyConnection) -> None:
    card = data.fetch_story_card(conn, "P01308")
    assert card is not None
    assert card["gene_symbol"] == "INS"
    assert card["family_group"] == "Secreted"
    assert card["tissue_specificity"] == "Tissue enhanced (pancreas)"
    assert card["sequence"] == "MALWMRLLPLLALLALWGPDPAAA"

    partner_accessions = {p["accession"] for p in card["top_interaction_partners"]}
    assert "P06213" in partner_accessions  # INSR, the receptor drugs route through
    insr = next(p for p in card["top_interaction_partners"] if p["accession"] == "P06213")
    assert insr["combined_score"] == 0.999  # 999/1000

    assert card["approved_drugs"] == []  # ligand: no directly-targeting drug
    top_disease = card["top_diseases"][0]
    assert top_disease["disease_name"] == "type 1 diabetes mellitus"
    # NUMERIC overall_score (DuckDB Decimal) coerced to a native float by data.py.
    assert isinstance(top_disease["overall_score"], float)
    assert top_disease["overall_score"] == 0.9


def test_story_card_receptor_carries_the_drug(conn: duckdb.DuckDBPyConnection) -> None:
    card = data.fetch_story_card(conn, "P06213")
    assert card is not None
    assert card["gene_symbol"] == "INSR"
    assert "Insulin glargine" in {d["drug_name"] for d in card["approved_drugs"]}
    # INSR's top partner is the ligand INS (the link is navigable both ways).
    assert "P01308" in {p["accession"] for p in card["top_interaction_partners"]}


def test_story_card_unknown_accession_is_none(conn: duckdb.DuckDBPyConnection) -> None:
    assert data.fetch_story_card(conn, "X99999") is None


def test_search_by_name_and_accession(conn: duckdb.DuckDBPyConnection) -> None:
    by_name = {h["uniprot_accession"] for h in data.search_proteins(conn, "insulin")}
    assert "P01308" in by_name
    by_acc = data.search_proteins(conn, "P06213")
    assert by_acc[0]["gene_symbol"] == "INSR"


def test_list_proteins_returns_all(conn: duckdb.DuckDBPyConnection) -> None:
    proteins = data.list_proteins(conn)
    assert {p["uniprot_accession"] for p in proteins} == {"P01308", "P06213", "P08069"}
    assert {p["gene_symbol"] for p in proteins} == {"INS", "INSR", "IGF1R"}


def test_atlas_returns_all_points(conn: duckdb.DuckDBPyConnection) -> None:
    atlas = data.fetch_atlas(conn)
    assert len(atlas["accession"]) == 3
    assert len(atlas["umap_x"]) == len(atlas["accession"])
    assert "Secreted" in atlas["family_group"]

    counts = zip(atlas["disease_count"], atlas["drug_count"], strict=True)
    by_acc = dict(zip(atlas["accession"], counts, strict=True))
    assert by_acc["P01308"] == (1, 0)  # INS: one linked disease, no drug (ligand)
    assert by_acc["P06213"] == (1, 1)  # INSR: one linked disease, one approved drug
    assert by_acc["P08069"] == (0, 0)  # IGF1R: no disease or drug links in the fixture


def test_fetch_sequence_lengths(conn: duckdb.DuckDBPyConnection) -> None:
    lengths = data.fetch_sequence_lengths(conn, ["P01308", "P06213"])
    assert lengths == {"P01308": 110, "P06213": 1382}
    assert data.fetch_sequence_lengths(conn, []) == {}


def test_find_neighbors_returns_ranked_hits(conn: duckdb.DuckDBPyConnection) -> None:
    hits = data.find_neighbors(conn, "P01308", k=10)
    assert [h["accession"] for h in hits] == ["P06213", "P08069"]  # ordered by rank
    assert hits[0]["gene_symbol"] == "INSR"
    assert hits[0]["similarity"] == 0.96


def test_find_neighbors_respects_k(conn: duckdb.DuckDBPyConnection) -> None:
    hits = data.find_neighbors(conn, "P01308", k=1)
    assert [h["accession"] for h in hits] == ["P06213"]


def test_find_neighbors_unknown_accession_is_empty(conn: duckdb.DuckDBPyConnection) -> None:
    assert data.find_neighbors(conn, "X99999", k=10) == []


_STANDARD_AA_CODES = set("ARNDCEQGHILKMFPSTWYV")


def test_fetch_composition_returns_rows_sorted_by_pct(conn: duckdb.DuckDBPyConnection) -> None:
    composition = data.fetch_composition(conn, "P01308")
    assert len(composition) == 20
    assert {c["amino_acid_code"] for c in composition} == _STANDARD_AA_CODES
    pcts = [c["pct_of_sequence"] for c in composition]
    assert pcts == sorted(pcts, reverse=True)
    # The fixture gives Alanine ('A') the highest pct and Valine ('V') the lowest.
    assert composition[0]["amino_acid_code"] == "A"
    assert composition[0]["name"] == "Alanine"
    assert composition[0]["category"] == "Nonpolar aliphatic"
    assert composition[0]["produced_by_body"] is True
    assert composition[0]["deficiency_note"] is None
    assert composition[-1]["amino_acid_code"] == "V"
    assert composition[-1]["name"] == "Valine"
    assert composition[-1]["three_letter_code"] == "Val"
    assert composition[-1]["produced_by_body"] is False
    assert composition[-1]["deficiency_note"] == "Valine deficiency note."
