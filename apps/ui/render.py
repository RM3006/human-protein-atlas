"""Pure presentation helpers for the Streamlit app.

No Streamlit or Plotly imports here: these functions turn raw data values into the
strings, percentages, and colors the UI renders, so they can be unit-tested without
a running app. All Streamlit/Plotly calls live in app.py.
"""

import math
from typing import Any

# Semantic data-encoding colors, layered *on top of* the monochrome chrome
# (#ffffff/#e6e6e6/#888888/#111111 stay the structural language: cards, borders,
# labels, body text). These three appear only where a value's *type* matters —
# protein nodes, disease markers/bars, drug pills — never on chrome.
PROTEIN_COLOR = "#3a5a78"  # deep slate blue
DISEASE_COLOR = "#a8333f"  # muted crimson
DRUG_COLOR = "#2f7a52"  # emerald

_STRENGTH_LOW = (212, 212, 212)  # pale grey  — weak evidence
_STRENGTH_HIGH = (91, 58, 115)  # deep violet — strong evidence


def strength_color(value: float) -> str:
    """Map a 0-1 strength value onto a sequential grey -> violet scale.

    One shared scale for every kind of "how strong is this link" value
    (STRING confidence, Open Targets evidence, …) so edges and node fills in
    the interactome graph stay comparable across entity types.
    """
    t = max(0.0, min(1.0, value))
    r = round(_STRENGTH_LOW[0] + (_STRENGTH_HIGH[0] - _STRENGTH_LOW[0]) * t)
    g = round(_STRENGTH_LOW[1] + (_STRENGTH_HIGH[1] - _STRENGTH_LOW[1]) * t)
    b = round(_STRENGTH_LOW[2] + (_STRENGTH_HIGH[2] - _STRENGTH_LOW[2]) * t)
    return f"rgb({r},{g},{b})"


def ring_positions(n: int, radius: float, start_angle: float = -90.0) -> list[tuple[float, float]]:
    """Evenly-spaced (x, y) positions for n nodes on a circle of the given radius.

    `start_angle` is in degrees, measured clockwise from the positive x-axis;
    -90 starts at the top (12 o'clock) so rings read top-down. Used to lay out
    the radial ego-network without pulling in a graph-layout dependency.
    """
    if n <= 0:
        return []
    return [
        (
            radius * math.cos(math.radians(start_angle + 360.0 * i / n)),
            radius * math.sin(math.radians(start_angle + 360.0 * i / n)),
        )
        for i in range(n)
    ]


# Stable colors for the atlas legend. The dominant, low-information buckets
# (Intracellular, Unclassified) are muted greys so they don't visually swamp the map;
# the specific functional families get saturated colors that pop.
FAMILY_COLORS: dict[str, str] = {
    "Receptors": "#e6550d",
    "Ion channels": "#fd8d3c",
    "Transporters": "#31a354",
    "Transcription factors": "#756bb1",
    "Immune": "#d62728",
    "Ribosomal / translation": "#17becf",
    "Enzymes": "#3182bd",
    "Secreted": "#e377c2",
    "Membrane": "#8c6d31",
    "Intracellular": "#969696",
    "Unclassified": "#d9d9d9",
}

# Legend / draw order: specific families first, generic localization buckets last.
FAMILY_ORDER: list[str] = list(FAMILY_COLORS.keys())

DRUGS_HELP_CAPTION = (
    "Drugs attach to the protein they act *on* (the molecular target). A hormone's "
    "medicines therefore sit on its **receptor**, not the hormone itself — so if a "
    "protein shows no drugs, check its interaction partners above."
)

NEIGHBORS_HELP_CAPTION = (
    "“Similar” here means similar **sequence shape**, learned by the ESM-2 AI model — "
    "a different idea from “who it talks to” (physical interaction partners)."
)

ATLAS_INSIGHT = (
    "Each dot is one human protein. An AI model read all ~20,000 protein sequences and "
    "placed look-alikes near each other — nobody gave it the labels. Distance is "
    "relative: there are no units on the axes."
)

PARTNERS_EMPTY = (
    "No high-confidence partners known — STRING-DB has no interaction for this protein "
    "scoring at least 0.70."
)


def confidence_pct(score: float) -> int:
    """Convert a 0-1 score (STRING confidence, OT evidence) to a 0-100 integer."""
    return round(max(0.0, min(1.0, score)) * 100)


def phase_label(max_phase: int | None) -> str:
    """Human label for an Open Targets max clinical phase."""
    if max_phase == 4:
        return "Approved"
    if max_phase == 3:
        return "Late-stage trials"
    if max_phase is None:
        return "In development"
    return f"Phase {max_phase}"


def similarity_pct(similarity: float | None) -> str:
    """Format an ESM-2 cosine similarity as a percentage string."""
    if similarity is None:
        return "—"
    return f"{round(similarity * 100)}%"


def neighbor_metric_label(similarity: float | None, sequence_length: int | None) -> str:
    """Format the neighbor-list metric: 'similarity 94% · 110 aa'."""
    parts = [f"similarity {similarity_pct(similarity)}"]
    if sequence_length:
        parts.append(f"{sequence_length} aa")
    return " · ".join(parts)


def hover_counts_label(disease_count: int, drug_count: int) -> str:
    """Format the atlas-point hover line: 'N linked pathology/-ies · N targeting drug(s)'."""
    disease_word = "pathology" if disease_count == 1 else "pathologies"
    drug_word = "drug" if drug_count == 1 else "drugs"
    return f"{disease_count} linked {disease_word} · {drug_count} targeting {drug_word}"


def display_label(item: dict[str, Any]) -> str:
    """Label a protein as 'GENE (Full name)', falling back to gene, then accession.

    Works for search hits (uniprot_accession key) and partners/neighbors (accession key).
    """
    gene = item.get("gene_symbol")
    name = item.get("protein_name")
    accession = item.get("accession") or item.get("uniprot_accession")
    if gene and name:
        return f"{gene} ({name})"
    if gene:
        return str(gene)
    return str(accession) if accession else "?"


def identity_line(card: dict[str, Any]) -> str:
    """Build the subtitle identity line, skipping any missing parts.

    e.g. "INS · P01308 · Pfam PF00049 · 110 aa"
    """
    parts: list[str] = []
    if card.get("gene_symbol"):
        parts.append(str(card["gene_symbol"]))
    parts.append(str(card["uniprot_accession"]))
    if card.get("pfam_id"):
        parts.append(f"Pfam {card['pfam_id']}")
    if card.get("sequence_length"):
        parts.append(f"{card['sequence_length']} aa")
    return " · ".join(parts)


def drugs_empty_message(gene_symbol: str | None) -> str:
    """Factual empty-state for a protein with no directly-targeting drugs."""
    name = gene_symbol or "This protein"
    return f"No approved or late-stage drugs target {name} directly in Open Targets."


def chips(values: str | None) -> list[str]:
    """Split a comma-separated HPA field (protein_class, subcellular_location) into chips."""
    if not values:
        return []
    return [token.strip() for token in values.split(",") if token.strip()]
