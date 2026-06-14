"""Value-checking tests for the guided tour content and step logic (apps/ui/tour.py).

Accessions are checked against the live atlas (Part 7 planning, Part 8 addendum):
P08100 (RHO, Receptors), P00533 (EGFR, Enzymes, 79 drugs and 503 STRING
interactions; revisited for its amino acid composition — leucine 9.17% of
1,210 residues), P04637 (TP53, Transcription factors, top 5 linked diseases are
all cancers), and Q63HN1 (SPATA31F2P, Unclassified, zero
annotation/interactions/diseases/drugs and an isolated UMAP position — the
"empty zone" stop).
"""

from apps.ui import tour

VALID_TAB_LABELS = {
    "Interactome topology",
    "Sequence neighborhood",
    "Clinical & therapeutic profile",
    "Amino acid composition",
}


def test_tour_has_five_steps() -> None:
    assert len(tour.TOUR_STEPS) == 5


def test_tour_step_accessions_are_the_roadmap_anchors() -> None:
    accessions = [step.accession for step in tour.TOUR_STEPS]
    assert accessions == ["P08100", "P00533", "P04637", "P00533", "Q63HN1"]


def test_tour_steps_have_content() -> None:
    for step in tour.TOUR_STEPS:
        assert step.title
        assert step.narration
        assert step.tab_label in VALID_TAB_LABELS
        assert step.tab_explanation


def test_empty_protein_does_not_point_at_clinical_tab() -> None:
    """Q63HN1 has zero diseases and zero drugs, so its tab can't be the clinical one."""
    q63hn1 = next(step for step in tour.TOUR_STEPS if step.accession == "Q63HN1")
    assert q63hn1.tab_label != "Clinical & therapeutic profile"


def test_progress_label() -> None:
    assert tour.progress_label(0) == "Step 1 of 5"
    assert tour.progress_label(4) == "Step 5 of 5"


def test_first_and_last_step() -> None:
    assert tour.is_first_step(0)
    assert not tour.is_first_step(1)
    assert not tour.is_last_step(3)
    assert tour.is_last_step(4)
