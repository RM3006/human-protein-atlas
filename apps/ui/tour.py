"""Content and pure step logic for the guided 90-second tour (Part 7).

Five narrated stops, each pinned to one UniProt accession: a receptor everyone
has felt (sight), an enzyme everyone has heard of (a cancer drug target), a
transcription factor everyone has heard of (the genome's guardian), that same
cancer drug target again seen through its amino acid composition (Part 8), and
a protein nobody has heard of (the long tail, most of the roughly 20,000 dots
on the atlas look more like this one than like the first three).

Each step names one tab (tab_label, matching the st.tabs labels in app.py
exactly) and explains, in tab_explanation, what that protein looks like there
and why. The tab picked for each protein is one where that protein actually
has something to show, so a protein with zero diseases or drugs never points
at "Clinical & therapeutic profile".

No Streamlit imports here, mirroring render.py: app.py owns st.session_state
and st.rerun(); this module owns the step content and step-index arithmetic so
both can be unit-tested without a running app.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TourStep:
    accession: str
    title: str
    narration: str
    tab_label: str
    tab_explanation: str


TOUR_STEPS: tuple[TourStep, ...] = (
    TourStep(
        accession="P08100",
        title="How sight begins",
        narration=(
            "Rhodopsin captures a single photon in your retina and fires the first "
            "signal your brain reads as light. It's a GPCR, a receptor that passes "
            "its signal onward through G proteins."
        ),
        tab_label="Sequence neighborhood",
        tab_explanation=(
            "Open the Sequence neighborhood tab. This map places every human protein "
            "by how similar its sequence is to every other one. A model trained on "
            "millions of protein sequences learned to group them this way on its "
            "own, without ever being told what each protein does. Rhodopsin sits "
            "inside a tight cluster of other receptors, grouped there by sequence "
            "alone."
        ),
    ),
    TourStep(
        accession="P00533",
        title="A growth switch, and a cancer drug target",
        narration=(
            "EGFR tells a cell when to grow and divide. When it gets stuck “on”, "
            "that signal runs away, which is why EGFR is one of the most heavily "
            "drugged proteins in the atlas."
        ),
        tab_label="Interactome topology",
        tab_explanation=(
            "Open the Interactome topology tab. EGFR connects to over 500 other "
            "proteins in STRING, one of the largest networks in the atlas. This is "
            "what a hub protein looks like, and why a single fault here can ripple "
            "through an entire growth signaling pathway."
        ),
    ),
    TourStep(
        accession="P04637",
        title="The guardian of the genome",
        narration=(
            "TP53 stops damaged cells from dividing. It's the most studied gene in "
            "cancer research: when it fails, cells lose their main brake on "
            "uncontrolled growth, which is why so many cancers trace back to a "
            "broken TP53."
        ),
        tab_label="Clinical & therapeutic profile",
        tab_explanation=(
            "Open the Clinical & therapeutic profile tab. The diseases shown here "
            "are all cancers or hereditary cancer syndromes, each with a strong "
            "evidence score from Open Targets: a quick read of how central this one "
            "gene is to cancer biology."
        ),
    ),
    TourStep(
        accession="P00533",
        title="What EGFR is built from",
        narration=(
            "EGFR's 1,210 amino acids aren't interchangeable: the chemistry of each "
            "one shapes how the protein folds, where it sits in the cell membrane, "
            "and how a drug can reach it."
        ),
        tab_label="Amino acid composition",
        tab_explanation=(
            "Open the Amino acid composition tab. EGFR's full sequence is shown "
            "alongside its 20 amino acids ranked from most to least common, colored "
            "by side-chain chemistry. Leucine, a hydrophobic amino acid, comes out "
            "on top at just over 9% — fitting for a receptor anchored in the cell's "
            "membrane."
        ),
    ),
    TourStep(
        accession="Q63HN1",
        title="The long tail",
        narration=(
            "Not every dot here is a famous protein. SPATA31F2P is a real human gene "
            "with a real sequence, but UniProt has no functional annotation for it, "
            "so its card is honest about what's unknown: no description, no known "
            "interactions, no diseases, no drugs."
        ),
        tab_label="Sequence neighborhood",
        tab_explanation=(
            "Open the Sequence neighborhood tab. Notice how few dots sit near this "
            "one: its embedding lands in a sparse, mostly unlabeled corner of the "
            "map. Most of the roughly 20,000 proteins in the atlas look more like "
            "this than like TP53."
        ),
    ),
)


def progress_label(step_index: int) -> str:
    """'Step 2 of 5' for the tour card's eyebrow."""
    return f"Step {step_index + 1} of {len(TOUR_STEPS)}"


def is_first_step(step_index: int) -> bool:
    return step_index <= 0


def is_last_step(step_index: int) -> bool:
    return step_index >= len(TOUR_STEPS) - 1
