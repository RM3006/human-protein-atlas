"""Unit tests for the pure presentation helpers (apps/ui/render.py)."""

from apps.ui import render


def test_confidence_pct_scales_and_clamps() -> None:
    assert render.confidence_pct(0.999) == 100
    assert render.confidence_pct(0.5) == 50
    assert render.confidence_pct(1.5) == 100  # clamped
    assert render.confidence_pct(-0.2) == 0  # clamped


def test_phase_label() -> None:
    assert render.phase_label(4) == "Approved"
    assert render.phase_label(3) == "Late-stage trials"
    assert render.phase_label(None) == "In development"
    assert render.phase_label(2) == "Phase 2"


def test_similarity_pct() -> None:
    assert render.similarity_pct(0.977) == "98%"
    assert render.similarity_pct(None) == "—"


def test_neighbor_metric_label() -> None:
    assert render.neighbor_metric_label(0.977, 110) == "similarity 98% · 110 aa"
    assert render.neighbor_metric_label(0.5, None) == "similarity 50%"
    assert render.neighbor_metric_label(None, 110) == "similarity — · 110 aa"


def test_hover_counts_label_pluralizes() -> None:
    assert render.hover_counts_label(0, 0) == "0 linked pathologies · 0 targeting drugs"
    assert render.hover_counts_label(1, 1) == "1 linked pathology · 1 targeting drug"
    assert render.hover_counts_label(3, 2) == "3 linked pathologies · 2 targeting drugs"


def test_display_label_gene_with_full_name() -> None:
    partner = {"accession": "P06213", "gene_symbol": "INSR", "protein_name": "Insulin receptor"}
    assert render.display_label(partner) == "INSR (Insulin receptor)"
    # search-hit shape (uniprot_accession key) and fallbacks
    assert render.display_label({"uniprot_accession": "P01308", "gene_symbol": "INS"}) == "INS"
    assert render.display_label({"accession": "Q9Y478", "gene_symbol": None}) == "Q9Y478"


def test_identity_line_skips_missing_parts() -> None:
    full = {
        "gene_symbol": "INS",
        "uniprot_accession": "P01308",
        "pfam_id": "PF00049",
        "sequence_length": 110,
    }
    assert render.identity_line(full) == "INS · P01308 · Pfam PF00049 · 110 aa"

    sparse = {
        "gene_symbol": None,
        "uniprot_accession": "Q9Y478",
        "pfam_id": None,
        "sequence_length": None,
    }
    assert render.identity_line(sparse) == "Q9Y478"


def test_drugs_empty_message_uses_gene_or_fallback() -> None:
    assert "INS" in render.drugs_empty_message("INS")
    assert "This protein" in render.drugs_empty_message(None)


def test_chips_splits_and_trims() -> None:
    assert render.chips("Enzymes, Plasma proteins") == ["Enzymes", "Plasma proteins"]
    assert render.chips("Vesicles,Plasma membrane") == ["Vesicles", "Plasma membrane"]
    assert render.chips(None) == []
    assert render.chips("") == []


def test_strength_color_interpolates_grey_to_violet() -> None:
    assert render.strength_color(0.0) == "rgb(212,212,212)"
    assert render.strength_color(1.0) == "rgb(91,58,115)"
    # Midpoint sits strictly between the two endpoints on every channel.
    mid = render.strength_color(0.5)
    assert mid == "rgb(152,135,164)"


def test_strength_color_clamps_out_of_range_values() -> None:
    assert render.strength_color(-1.0) == render.strength_color(0.0)
    assert render.strength_color(2.0) == render.strength_color(1.0)


def test_ring_positions_spaces_nodes_evenly_on_a_circle() -> None:
    assert render.ring_positions(0, radius=1.0) == []

    one = render.ring_positions(1, radius=2.0)
    assert len(one) == 1
    x, y = one[0]
    assert round(x, 6) == 0.0
    assert round(y, 6) == -2.0  # start_angle=-90 -> straight up

    four = render.ring_positions(4, radius=1.0)
    assert len(four) == 4
    for x, y in four:
        assert round(x * x + y * y, 6) == 1.0  # every node sits on the circle


def test_every_family_group_has_a_color() -> None:
    # The dbt family_group buckets must all be colorable, else points fall to default.
    expected = {
        "Receptors",
        "Ion channels",
        "Transporters",
        "Transcription factors",
        "Immune",
        "Ribosomal / translation",
        "Enzymes",
        "Secreted",
        "Membrane",
        "Intracellular",
        "Unclassified",
    }
    assert expected <= set(render.FAMILY_COLORS)
