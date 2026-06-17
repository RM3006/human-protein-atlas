"""Protein Atlas — Streamlit UI.

A standalone analytical dashboard for one protein at a time. A sidebar drives
selection and filtering; a header strip of KPIs orients the visitor; three tabs
explore the protein along its three axes — who it physically talks to (STRING
interactome), what it structurally resembles (ESM-2 embeddings), and what it
means clinically (diseases, drugs). Every cross-reference (interactome nodes,
partner-table rows, neighbour rows, the ligand->receptor link) drives one
selected protein, kept in st.session_state and mirrored to the URL
(?accession=...) for deep links.

The visual language is two layers. A monochrome editorial chrome — #ffffff
canvas, #e6e6e6 lines/borders/dividers, #888888 labels, #111111 ink — carries
all structure (cards, sidebar, tabs, typography). Three semantic colors are
layered on top, applied only to data *values*: slate-blue for proteins, crimson
for diseases, emerald for drugs, plus a shared grey->violet "strength" scale for
the interactome graph's edges. Chrome never takes a semantic hue; data values
never take a chrome hue. The amino acid composition tab is the one exception:
it uses its own 5-color side-chain-category palette (render.CATEGORY_COLORS),
scoped to that tab only.

Queries MotherDuck + Qdrant directly via apps/ui/data.py (no API tier). Run with:
    streamlit run apps/ui/app.py
Credentials come from st.secrets (.streamlit/secrets.toml) or environment variables:
MOTHERDUCK_TOKEN, QDRANT_URL, QDRANT_API_KEY.
"""

import html
import os
import sys
from pathlib import Path
from typing import Any

import duckdb
import plotly.graph_objects as go
import streamlit as st
from qdrant_client.http.exceptions import ApiException

# `streamlit run apps/ui/app.py` only puts apps/ui on sys.path; add the project root
# so the absolute `apps.ui.*` imports resolve the same way they do under pytest.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.ui import data, render, tour  # noqa: E402

DEFAULT_ACCESSION = "P02452"  # collagen alpha-1(I) chain (COL1A1)

st.set_page_config(page_title="Protein Atlas", page_icon="🧬", layout="wide")


def _secret(name: str) -> str:
    """Read a credential from st.secrets, falling back to the environment."""
    try:
        return str(st.secrets[name])
    except Exception:
        return os.environ[name]


@st.cache_resource
def get_conn() -> Any:
    return data.connect_motherduck(_secret("MOTHERDUCK_TOKEN"))


@st.cache_resource
def get_qdrant() -> Any:
    return data.make_qdrant_client(_secret("QDRANT_URL"), _secret("QDRANT_API_KEY"))


@st.cache_data(show_spinner="Loading the protein atlas…")
def load_atlas() -> dict[str, list[Any]]:
    return data.fetch_atlas(get_conn())


@st.cache_data(show_spinner="Loading story card…")
def load_story_card(accession: str) -> dict[str, Any] | None:
    return data.fetch_story_card(get_conn(), accession)


@st.cache_data(show_spinner="Loading protein index…")
def protein_index() -> tuple[list[str], dict[str, str]]:
    """All proteins as (accessions sorted by label, accession -> 'GENE (Name)' label)."""
    rows = data.list_proteins(get_conn())
    labels = {r["uniprot_accession"]: render.display_label(r) for r in rows}
    accessions = sorted(labels, key=lambda a: labels[a])
    return accessions, labels


@st.cache_data(show_spinner="Finding similar proteins…")
def load_neighbors(accession: str, k: int = 20) -> list[dict[str, Any]]:
    return data.find_neighbors(get_qdrant(), accession, k)


@st.cache_data(show_spinner=False)
def load_sequence_lengths(accessions: tuple[str, ...]) -> dict[str, int]:
    return data.fetch_sequence_lengths(get_conn(), list(accessions))


@st.cache_data(show_spinner="Loading composition…")
def load_composition(accession: str) -> list[dict[str, Any]]:
    return data.fetch_composition(get_conn(), accession)


def select(accession: str) -> None:
    """Set the selected protein, mirror to the URL, then rerun."""
    st.session_state.selected_accession = accession
    st.query_params["accession"] = accession
    st.rerun()


def current_accession() -> str:
    if "selected_accession" not in st.session_state:
        st.session_state.selected_accession = st.query_params.get("accession", DEFAULT_ACCESSION)
    return st.session_state.selected_accession


# ---------------------------------------------------------------------------
# Guided tour — five narrated stops, content in apps/ui/tour.py
# ---------------------------------------------------------------------------


def start_tour() -> None:
    st.session_state.tour_pre_accession = current_accession()
    st.session_state.tour_step = 0
    select(tour.TOUR_STEPS[0].accession)


def tour_step_to(step_index: int) -> None:
    st.session_state.tour_step = step_index
    select(tour.TOUR_STEPS[step_index].accession)


def end_tour() -> None:
    """Leave the tour and jump back to whatever was selected before it started."""
    st.session_state.tour_step = None
    pre_accession = st.session_state.pop("tour_pre_accession", None)
    select(pre_accession or DEFAULT_ACCESSION)


def render_tour_button() -> None:
    """The tour's entry-point button, shown beside the page title when inactive."""
    if st.session_state.get("tour_step") is None and st.button(
        "Take the 90-second tour", key="tour_start", type="primary"
    ):
        start_tour()


def render_tour_banner(step: tour.TourStep, step_index: int) -> None:
    """The active step's narration card, with Back / Next / Exit controls top right."""
    with st.container(key="tour_card"):
        col_label, col_nav = st.columns([2, 1], vertical_alignment="center")
        with col_label:
            st.markdown(
                "<div style='color:#888888;font-size:0.72rem;font-weight:700;"
                "text-transform:uppercase;letter-spacing:0.12em;'>"
                f"Guided tour · {tour.progress_label(step_index)}</div>",
                unsafe_allow_html=True,
            )
        with col_nav, st.container(key="tour_nav"):
            if not tour.is_first_step(step_index) and st.button("← Back", key="tour_back"):
                tour_step_to(step_index - 1)
            label = "Finish →" if tour.is_last_step(step_index) else "Next →"
            if st.button(label, key="tour_next", type="primary"):
                if tour.is_last_step(step_index):
                    end_tour()
                else:
                    tour_step_to(step_index + 1)
            if st.button("Exit tour", key="tour_exit"):
                end_tour()

        st.markdown(
            f"<div style='font-family:{DISPLAY_FONT};font-weight:700;font-size:1rem;"
            f"color:#111111;margin-bottom:6px;'>{step.title}</div>"
            f"<div style='color:#111111;font-size:0.95rem;line-height:1.55;"
            f"margin-bottom:10px;'>{step.narration}</div>"
            f"<div style='color:#111111;font-size:0.88rem;line-height:1.55;"
            f"border-top:1px solid #ecdfb8;padding-top:10px;'>{step.tab_explanation}</div>",
            unsafe_allow_html=True,
        )


DISPLAY_FONT = '"Space Grotesk", -apple-system, system-ui, sans-serif'


def inject_css() -> None:
    """Monochrome editorial chrome, plus dashboard-shell rules (sidebar, tabs, metrics).

    Structural language stays #ffffff/#e6e6e6/#888888/#111111 throughout. The three
    semantic colors (render.PROTEIN_COLOR / DISEASE_COLOR / DRUG_COLOR) and the
    grey->violet strength scale are applied only inside the figures and the
    clinical tab's markup — never to chrome elements via this stylesheet.
    """
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700;800&display=swap');

        .stApp { background: #ffffff; }

        /* Flat bordered card (no shadow) — the one reusable chrome unit. */
        .card {
            background: #ffffff;
            border: 1px solid #e6e6e6;
            border-radius: 10px;
            padding: 22px 26px;
            margin-bottom: 4px;
        }
        .card a { color: #111111; }

        /* Streamlit bordered / scrollable containers -> same flat card look. */
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: #ffffff;
            border: 1px solid #e6e6e6 !important;
            border-radius: 10px;
            box-shadow: none;
        }

        /* Sidebar: a quiet control panel, divided from the canvas by one rule. */
        [data-testid="stSidebar"] {
            background: #ffffff;
            border-right: 1px solid #e6e6e6;
        }

        /* st.metric: numbers carry the hierarchy — no colored boxes. */
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e6e6e6;
            border-radius: 10px;
            padding: 14px 18px;
        }
        [data-testid="stMetricLabel"] { color: #888888; }
        [data-testid="stMetricValue"] { color: #111111; font-family: 'Space Grotesk', sans-serif; }

        /* st.tabs: underline the active tab in ink; no filled pill backgrounds. */
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: 28px; border-bottom: 1px solid #e6e6e6;
        }
        [data-testid="stTabs"] [data-baseweb="tab"] {
            color: #888888; font-weight: 600; background: transparent;
        }
        [data-testid="stTabs"] [aria-selected="true"] { color: #111111 !important; }
        [data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color: #111111; }

        /* List item links: flush-left, borderless, ink text. */
        [data-testid="stButton"] button {
            justify-content: flex-start; text-align: left; padding: 0.05rem 0;
            border: none; font-weight: 600; color: #111111; background: transparent;
        }
        [data-testid="stButton"] button:hover { color: #000; text-decoration: underline; }

        /* Primary buttons (tour start / Next / Finish): solid ink, the one filled
           chrome element — deliberately heavier than the borderless list links above. */
        [data-testid="stButton"] button[kind="primary"] {
            justify-content: center; text-align: center; padding: 0.5rem 1.4rem;
            border: 1px solid #111111; border-radius: 8px; font-weight: 700;
            color: #ffffff; background: #111111;
        }
        [data-testid="stButton"] button[kind="primary"]:hover {
            color: #ffffff; text-decoration: none; background: #333333; border-color: #333333;
        }

        /* Tour Back / Exit: same pill shape as the primary button above, outlined
           instead of filled, so the tour's button row reads as one control group. */
        [data-testid="stButton"] button[kind="secondary"] {
            justify-content: center; text-align: center; padding: 0.5rem 1.4rem;
            border: 1px solid #111111; border-radius: 8px; font-weight: 700;
            color: #111111; background: #ffffff;
        }
        [data-testid="stButton"] button[kind="secondary"]:hover {
            color: #111111; text-decoration: none; background: #f5f5f5;
        }

        /* Tour Back / Next / Finish / Exit: plain underlined text in the tour card's
           dark brown, laid out as a tight row. [kind=...] is repeated to outweigh
           the specificity of the rules above. */
        .st-key-tour_nav {
            display: flex; flex-direction: row; justify-content: flex-end;
            align-items: center; gap: 0.4rem; margin-top: 10px;
        }
        .st-key-tour_nav [data-testid="stButton"] button[kind="primary"],
        .st-key-tour_nav [data-testid="stButton"] button[kind="secondary"] {
            padding: 0.1rem 0.2rem; font-size: 0.82rem; font-weight: 600;
            border: none; border-radius: 0; text-decoration: underline;
            color: #8a6d1f; background: transparent;
        }
        .st-key-tour_nav [data-testid="stButton"] button[kind="primary"]:hover,
        .st-key-tour_nav [data-testid="stButton"] button[kind="secondary"]:hover {
            color: #a3821f; background: transparent; text-decoration: underline;
        }

        /* Tour instruction card: a warm tint marks this as tutorial chrome, kept
           separate from the neutral .card used for protein data everywhere else. */
        .st-key-tour_card {
            background: #fdf6e3;
            border: 1px solid #ecdfb8;
            border-radius: 10px;
            padding: 4px 26px 22px;
            margin-bottom: 4px;
        }

        /* Atlas insight card: thinner than Streamlit's default st.info padding, so it
           reads as a quiet caption rather than a competing focal point. */
        .st-key-atlas_insight [data-testid="stAlertContainer"] {
            padding-top: 0.5rem; padding-bottom: 0.5rem;
        }

        /* Search dropdown / slider: #e6e6e6 frame, ink accents — chrome stays monochrome. */
        [data-baseweb="select"] > div { border-color: #e6e6e6 !important; border-radius: 8px; }
        [data-testid="stSlider"] [role="slider"] { background-color: #111111 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def app_header() -> None:
    col_title, col_tour = st.columns([5, 2], vertical_alignment="center")
    with col_title:
        st.markdown(
            f"<div style='font-family:{DISPLAY_FONT};font-size:72px;font-weight:bold;"
            "color:#111111;letter-spacing:-0.02em;line-height:1.05;'>Protein Atlas</div>"
            "<div style='color:#888888;font-size:0.9rem;'>"
            "Every human protein — what it does, who it talks to, and what "
            "goes wrong when it breaks."
            "</div>",
            unsafe_allow_html=True,
        )
    with col_tour:
        render_tour_button()
    st.markdown(
        "<div style='border-bottom:1px solid #e6e6e6;margin-bottom:1.1rem;'></div>",
        unsafe_allow_html=True,
    )


def subheader(title: str, sub: str | None = None) -> None:
    """A quiet section label: #111 title (+ optional #888 caption) on an #e6e6e6 rule."""
    sub_html = (
        f"<div style='color:#888888;font-size:0.82rem;margin-top:4px;'>{sub}</div>" if sub else ""
    )
    st.markdown(
        "<div style='border-top:1px solid #e6e6e6;margin-top:1.2rem;padding-top:0.9rem;"
        "margin-bottom:0.7rem;'>"
        f"<span style='font-family:{DISPLAY_FONT};font-size:1.05rem;font-weight:700;"
        f"color:#111111;'>{title}</span>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def _kv_row(label: str, value: str) -> str:
    """Key-value row: #888 label (fixed column) + #111 value."""
    return (
        "<div style='display:flex;gap:14px;margin-bottom:8px;'>"
        f"<span style='color:#888888;font-size:0.85rem;min-width:130px;'>{label}</span>"
        f"<span style='color:#111111;font-size:0.92rem;'>{html.escape(value)}</span></div>"
    )


def clickable_protein_row(item: dict[str, Any], metric: str, key_prefix: str) -> None:
    """A borderless name link (left), muted metric (right), and a #e6e6e6 list divider."""
    col_name, col_metric = st.columns([5, 2], vertical_alignment="center")
    with col_name:
        if st.button(
            render.display_label(item), key=f"{key_prefix}_{item['accession']}", type="tertiary"
        ):
            select(item["accession"])
    with col_metric:
        st.markdown(
            f"<div style='text-align:right;color:#888888;font-size:0.78rem;'>{metric}</div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<div style='border-bottom:1px solid #e6e6e6;margin:1px 0 3px;'></div>",
        unsafe_allow_html=True,
    )


def _resolve_clicked_accession(event: Any) -> str | None:
    """Pull an accession out of a Plotly point-selection event's customdata, if any.

    Shared by the interactome graph, the focused minimap, and the full atlas —
    all three carry the clicked point's accession in `customdata` (either bare or
    as the first element of a per-point list, depending on how the trace built it).
    """
    selection = getattr(event, "selection", None)
    points = selection.get("points") if isinstance(selection, dict) else None
    if not points:
        return None
    custom = points[0].get("customdata")
    return custom[0] if isinstance(custom, list) else custom


def _handle_point_click(event: Any, selected: str) -> None:
    accession = _resolve_clicked_accession(event)
    if accession and accession != selected:
        select(accession)


# ---------------------------------------------------------------------------
# Identity card + KPI strip — orientation, ahead of the tabs
# ---------------------------------------------------------------------------


def render_identity(card: dict[str, Any]) -> None:
    """Condensed hero: name, tagline, family tag, technical metadata, plain-English description."""
    gene = html.escape(card.get("gene_symbol") or "")
    name = html.escape(card["protein_name"] or card["uniprot_accession"])
    eyebrow = (
        f"<div style='font-family:{DISPLAY_FONT};text-transform:uppercase;letter-spacing:0.12em;"
        f"font-size:32px;color:{render.PROTEIN_COLOR};font-weight:bold;'>{gene}</div>"
        if gene
        else ""
    )
    tagline_html = (
        "<div style='font-size:1.08rem;color:#111111;margin:2px 0 10px;'>"
        f"{html.escape(card['tagline'])}</div>"
        if card.get("tagline")
        else ""
    )
    meta_parts = [html.escape(card["uniprot_accession"])]
    if card.get("pfam_id"):
        meta_parts.append(html.escape(card["pfam_id"]))
    if card.get("sequence_length"):
        meta_parts.append(f"{card['sequence_length']} aa")
    family = card.get("family_group")
    pill = (
        "<span style='display:inline-block;background:#333333;color:#ffffff;"
        "padding:3px 11px;border-radius:20px;font-size:0.72rem;font-weight:600;"
        "text-transform:uppercase;letter-spacing:0.04em;'>"
        f"{html.escape(family)}</span>"
        if family
        else ""
    )
    location_pills = "".join(
        "<span style='display:inline-block;border:1px solid #888888;color:#888888;"
        "padding:3px 11px;border-radius:20px;font-size:0.72rem;font-weight:600;"
        "text-transform:uppercase;letter-spacing:0.04em;'>"
        f"{html.escape(loc)}</span>"
        for loc in render.chips(card.get("subcellular_location"))
    )
    st.markdown(
        "<div class='card'>"
        f"{eyebrow}"
        f"<div style='font-family:{DISPLAY_FONT};font-size:24px;font-weight:700;color:#111111;"
        f"line-height:1.08;margin:2px 0 6px;'>{name}</div>"
        f"{tagline_html}"
        "<div style='display:flex;align-items:center;flex-wrap:wrap;gap:14px;margin-top:4px;'>"
        "<span style='font-family:monospace;color:#888888;font-size:0.82rem;'>"
        f"{' · '.join(meta_parts)}</span>{pill}{location_pills}</div>"
        "<div style='border-top:1px solid #e6e6e6;margin:16px 0 14px;'></div>"
        "<div style='font-size:0.98rem;color:#111111;line-height:1.6;'>"
        f"{html.escape(card['function_friendly'])}</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_kpis(card: dict[str, Any], threshold: float) -> None:
    """Three st.metric anchors: connected entities, linked pathologies, targeted drugs.

    Partners and diseases count only what clears the sidebar's strength threshold, so
    the row stays consistent with what the interactome graph below is actually showing.
    Drugs have no strength score, so the drug count is unfiltered.
    """
    partners = [p for p in card["top_interaction_partners"] if p["combined_score"] >= threshold]
    diseases = [d for d in card["top_diseases"] if d["overall_score"] >= threshold]
    drugs = card["approved_drugs"]

    col1, col2, col3 = st.columns(3)
    col1.metric(
        "Connected entities",
        len(partners) + len(diseases) + len(drugs),
        help="Interaction partners, linked diseases, and targeting drugs shown in the "
        "tabs below (strongest evidence first; partners and diseases respect the "
        "strength filter in the sidebar).",
    )
    col2.metric(
        "Linked pathologies",
        len(diseases),
        help="Diseases associated with this protein in Open Targets, above the "
        "threshold (strongest evidence first; up to 5 considered).",
    )
    col3.metric(
        "Targeted drugs",
        len(drugs),
        help="Approved or late-stage drugs (Open Targets max_phase >= 3) that act "
        "directly on this protein.",
    )


# ---------------------------------------------------------------------------
# Sidebar — the control panel: search, strength threshold, entity toggles
# ---------------------------------------------------------------------------


def render_search(selected: str) -> None:
    """A reset-after-use search box: it never carries a value into the next run.

    With ~20,000 options, pre-selecting the current protein (via `index`) anchors
    the dropdown's virtualized list to that position, so typing to filter opens
    on a near-empty view scrolled to where that item used to be. Always starting
    from `index=None` keeps the dropdown scrolled to the top of the results.
    """
    accessions, labels = protein_index()
    search_key = f"protein_search_{st.session_state.get('protein_search_seq', 0)}"
    choice = st.selectbox(
        "Find a protein",
        options=accessions,
        index=None,
        format_func=lambda a: labels.get(a, a),
        placeholder="Search a protein — insulin, TP53, EGFR …",
        label_visibility="collapsed",
        key=search_key,
    )
    if choice is not None and choice != selected:
        # Rotate the widget's key so the next run starts a fresh (empty) selectbox —
        # Streamlit disallows resetting a widget's own session_state after it's
        # been instantiated in the same run.
        st.session_state.protein_search_seq = st.session_state.get("protein_search_seq", 0) + 1
        select(choice)


def render_sidebar(selected: str) -> float:
    """Search + filter controls. Returns the strength_threshold (0-1)."""
    with st.sidebar:
        st.markdown(
            f"<div style='font-family:{DISPLAY_FONT};font-weight:700;color:#111111;"
            "font-size:0.95rem;margin-bottom:6px;'>Find a protein</div>",
            unsafe_allow_html=True,
        )
        render_search(selected)

        st.markdown(
            "<div style='border-top:1px solid #e6e6e6;margin:1.4rem 0 1rem;'></div>"
            f"<div style='font-family:{DISPLAY_FONT};font-weight:700;color:#111111;"
            "font-size:0.95rem;margin-bottom:2px;'>Interactome filters</div>"
            "<div style='color:#888888;font-size:0.78rem;margin-bottom:10px;'>"
            "Thin out the graph, KPIs, and lists by evidence strength.</div>",
            unsafe_allow_html=True,
        )
        threshold_pct = st.slider(
            "Minimum strength",
            min_value=0,
            max_value=100,
            value=40,
            help="Hides interaction partners (STRING confidence) and diseases "
            "(Open Targets evidence) scoring below this from the graph, KPIs, and lists.",
        )
    return threshold_pct / 100.0


# ---------------------------------------------------------------------------
# Tab 1 — Interactome topology (STRING ego-network, the macro view)
# ---------------------------------------------------------------------------


def build_network_graph(
    card: dict[str, Any],
    *,
    threshold: float,
    height: int = 540,
) -> go.Figure:
    """Radial ego-network: the selected protein at the centre, one ring per entity type.

    No graph-layout library — at this scale (well under fifty nodes) a hand-placed
    radial layout is exact, legible, and keeps the app dependency-free. Each ring's
    fill encodes what the node *is* (slate-blue protein, crimson disease, emerald
    drug); every spoke is colored on the shared grey->violet strength scale, so
    evidence strength reads consistently across entity types.
    """
    fig = go.Figure()
    center_label = card.get("gene_symbol") or card["uniprot_accession"]

    partners = sorted(
        (p for p in card["top_interaction_partners"] if p["combined_score"] >= threshold),
        key=lambda p: p["combined_score"],
        reverse=True,
    )
    diseases = sorted(
        (d for d in card["top_diseases"] if d["overall_score"] >= threshold),
        key=lambda d: d["overall_score"],
        reverse=True,
    )
    drugs = list(card["approved_drugs"])

    def add_spokes(
        positions: list[tuple[float, float]], strengths: list[float], dash: str | None
    ) -> None:
        for (x, y), t in zip(positions, strengths, strict=True):
            line: dict[str, Any] = {"color": render.strength_color(t), "width": 1 + t * 3}
            if dash:
                line["dash"] = dash
            fig.add_trace(
                go.Scatter(
                    x=[0, x], y=[0, y], mode="lines", line=line, hoverinfo="skip", showlegend=False
                )
            )

    # Ring 1 — interaction partners (slate-blue, clickable -> navigates to that protein).
    partner_pos = render.ring_positions(len(partners), radius=1.0)
    partner_strength = [p["combined_score"] for p in partners]
    add_spokes(partner_pos, partner_strength, dash=None)
    if partners:
        fig.add_trace(
            go.Scatter(
                x=[xy[0] for xy in partner_pos],
                y=[xy[1] for xy in partner_pos],
                mode="markers+text",
                text=[p.get("gene_symbol") or p["accession"] for p in partners],
                hovertext=[
                    f"{render.display_label(p)} — "
                    f"confidence {render.confidence_pct(p['combined_score'])}%"
                    for p in partners
                ],
                textposition="top center",
                textfont={"size": 10, "color": "#111111"},
                customdata=[p["accession"] for p in partners],
                marker={
                    "size": [16 + s * 28 for s in partner_strength],
                    "color": render.PROTEIN_COLOR,
                    "opacity": 0.88,
                    "line": {"width": 1.5, "color": "#ffffff"},
                },
                name="Interaction partner",
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )

    # Ring 2 — linked diseases (crimson, informational — no protein card to open).
    disease_pos = render.ring_positions(len(diseases), radius=1.7, start_angle=-78.0)
    disease_strength = [d["overall_score"] for d in diseases]
    add_spokes(disease_pos, disease_strength, dash="dot")
    if diseases:
        fig.add_trace(
            go.Scatter(
                x=[xy[0] for xy in disease_pos],
                y=[xy[1] for xy in disease_pos],
                mode="markers+text",
                text=[d["disease_name"] for d in diseases],
                hovertext=[
                    f"{d['disease_name']} — evidence {render.confidence_pct(d['overall_score'])}%"
                    for d in diseases
                ],
                textposition="bottom center",
                textfont={"size": 9, "color": "#888888"},
                marker={
                    "size": [14 + s * 24 for s in disease_strength],
                    "color": render.DISEASE_COLOR,
                    "symbol": "diamond",
                    "opacity": 0.85,
                    "line": {"width": 1, "color": "#ffffff"},
                },
                name="Linked disease",
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )

    # Ring 3 — drugs that target it (emerald, informational — strength = trial phase).
    drug_pos = render.ring_positions(len(drugs), radius=2.4, start_angle=-66.0)
    drug_strength = [min(1.0, (d["max_phase"] or 0) / 4.0) for d in drugs]
    add_spokes(drug_pos, drug_strength, dash="dash")
    if drugs:
        fig.add_trace(
            go.Scatter(
                x=[xy[0] for xy in drug_pos],
                y=[xy[1] for xy in drug_pos],
                mode="markers+text",
                text=[d["drug_name"] or d["chembl_id"] for d in drugs],
                hovertext=[
                    f"{d['drug_name'] or d['chembl_id']} — {render.phase_label(d['max_phase'])}"
                    for d in drugs
                ],
                textposition="top center",
                textfont={"size": 9, "color": "#888888"},
                marker={
                    "size": [14 + s * 24 for s in drug_strength],
                    "color": render.DRUG_COLOR,
                    "symbol": "square",
                    "opacity": 0.85,
                    "line": {"width": 1, "color": "#ffffff"},
                },
                name="Targeting drug",
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )

    # Centre — the selected protein itself, in chrome ink so it reads as "you are here".
    fig.add_trace(
        go.Scatter(
            x=[0],
            y=[0],
            mode="markers+text",
            text=[center_label],
            textposition="middle center",
            textfont={"size": 12, "color": "#ffffff", "family": DISPLAY_FONT},
            marker={"size": 46, "color": "#111111", "line": {"width": 2, "color": "#ffffff"}},
            name=center_label,
            hoverinfo="skip",
            showlegend=False,
        )
    )

    fig.update_layout(
        height=height,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        showlegend=True,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "font": {"size": 11, "color": "#888888"},
        },
        xaxis={"visible": False, "scaleanchor": "y", "scaleratio": 1},
        yaxis={"visible": False},
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        dragmode="pan",
    )
    return fig


def render_partner_table(card: dict[str, Any], threshold: float) -> None:
    """A clean, sortable st.dataframe of partners; selecting a row opens that card.

    The infinite-rerun bug came from Streamlit restoring a *stale* row selection
    (from the previously-displayed protein's table) on the very next render, which
    immediately re-triggered navigation back and forth forever. Deleting the widget's
    session-state entry before calling `select()` forces a clean slate on rerun, so a
    restored selection can never re-fire `select()` — in either navigation direction.
    """
    partners = sorted(
        (p for p in card["top_interaction_partners"] if p["combined_score"] >= threshold),
        key=lambda p: p["combined_score"],
        reverse=True,
    )
    if not partners:
        st.caption(render.PARTNERS_EMPTY)
        return
    rows = [
        {
            "Protein": render.display_label(p),
            "Accession": p["accession"],
            "Confidence": render.confidence_pct(p["combined_score"]),
        }
        for p in partners
    ]
    event = st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "Confidence": st.column_config.ProgressColumn(
                "Confidence", format="%d%%", min_value=0, max_value=100, color=render.PROTEIN_COLOR
            ),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="partner_table",
    )
    selection = getattr(event, "selection", None)
    selected_rows = selection.get("rows") if isinstance(selection, dict) else None
    if selected_rows:
        next_accession = partners[selected_rows[0]]["accession"]
        del st.session_state["partner_table"]
        select(next_accession)
    st.caption("Select a row to open that protein's card.")


def render_function_panel(card: dict[str, Any]) -> None:
    """Right-column companion to the partner table: where it is, then the source data."""
    where_rows: list[str] = []
    if card.get("tissue_specificity"):
        where_rows.append(_kv_row("Made mainly in", card["tissue_specificity"]))
    if card.get("subcellular_location"):
        where_rows.append(
            _kv_row("Inside the cell", ", ".join(render.chips(card["subcellular_location"])))
        )
    if card.get("tissue_distribution"):
        where_rows.append(_kv_row("Spread", card["tissue_distribution"]))
    body = "".join(where_rows) or "<i style='color:#888888;'>Tissue data not available.</i>"
    st.markdown(f"<div class='card'>{body}</div>", unsafe_allow_html=True)

    with st.expander("Show the science"):
        st.markdown(f"**UniProt function (source text):** {card['function_raw']}")
        st.markdown(
            f"**Pfam:** {card.get('pfam_id') or '—'}  ·  "
            f"**Protein class (HPA):** {card.get('protein_class') or '—'}  ·  "
            f"**Family group:** {card.get('family_group') or '—'}"
        )
        st.caption(
            "Sources: UniProt · STRING-DB · Human Protein Atlas · Open Targets. "
            "Map: ESM-2 t33_650M embeddings projected with UMAP."
        )


def render_interactome_tab(card: dict[str, Any], *, threshold: float) -> None:
    subheader(
        "Interactome topology",
        "The selected protein sits at the centre; rings are its interaction partners, "
        "linked diseases, and targeting drugs. Spokes are colored on one shared scale "
        "— pale grey is weak evidence, deep violet is strong — so strength compares "
        "across entity types at a glance.",
    )
    fig = build_network_graph(card, threshold=threshold)
    event = st.plotly_chart(fig, width="stretch", on_select="rerun", key="interactome_graph")
    _handle_point_click(event, card["uniprot_accession"])
    st.caption("Click a slate-blue node to open that protein's card.")

    col_partners, col_function = st.columns([3, 2])
    with col_partners:
        subheader("Interaction partners", "Sorted by STRING-DB confidence score.")
        render_partner_table(card, threshold)
    with col_function:
        subheader(
            "Where & what", "Tissue location, subcellular compartment, and source annotations."
        )
        render_function_panel(card)


# ---------------------------------------------------------------------------
# Tab 2 — Sequence neighborhood (ESM-2 / UMAP atlas, a different axis entirely)
# ---------------------------------------------------------------------------


def build_atlas_figure(
    atlas: dict[str, list[Any]],
    selected: str,
    neighbor_accessions: set[str],
    *,
    x_range: list[float] | None = None,
    y_range: list[float] | None = None,
    height: int = 480,
    marker_size: int = 4,
    show_legend: bool = True,
) -> go.Figure:
    """One WebGL scatter trace per family group, plus highlight + neighbour overlays.

    Pass x_range/y_range to zoom to a window (the focused minimap); leave them None to
    autoscale to the whole proteome (the full atlas). Each point carries its accession
    as customdata so a click resolves to a protein without a name lookup.
    """
    fig = go.Figure()
    by_family: dict[str, dict[str, list[Any]]] = {}
    selected_xy: tuple[float, float] | None = None
    for i, fam in enumerate(atlas["family_group"]):
        key = fam if fam in render.FAMILY_COLORS else "Unclassified"
        bucket = by_family.setdefault(
            key, {"x": [], "y": [], "text": [], "acc": [], "disease_count": [], "drug_count": []}
        )
        bucket["x"].append(atlas["umap_x"][i])
        bucket["y"].append(atlas["umap_y"][i])
        bucket["text"].append(atlas["gene_symbol"][i] or atlas["accession"][i])
        bucket["acc"].append(atlas["accession"][i])
        bucket["disease_count"].append(atlas["disease_count"][i])
        bucket["drug_count"].append(atlas["drug_count"][i])
        if atlas["accession"][i] == selected:
            selected_xy = (atlas["umap_x"][i], atlas["umap_y"][i])

    for fam in render.FAMILY_ORDER:
        bucket = by_family.get(fam)
        if not bucket:
            continue
        hovertext = [
            f"<b>{txt}</b><br>{render.hover_counts_label(dc, drc)}"
            for txt, dc, drc in zip(
                bucket["text"], bucket["disease_count"], bucket["drug_count"], strict=True
            )
        ]
        fig.add_trace(
            go.Scattergl(
                x=bucket["x"],
                y=bucket["y"],
                mode="markers",
                name=fam,
                hovertext=hovertext,
                customdata=bucket["acc"],
                marker={"size": marker_size, "color": render.FAMILY_COLORS[fam], "opacity": 0.6},
                hovertemplate="%{hovertext}<extra>" + fam + "</extra>",
            )
        )

    if neighbor_accessions:
        idx = [i for i, a in enumerate(atlas["accession"]) if a in neighbor_accessions]
        fig.add_trace(
            go.Scattergl(
                x=[atlas["umap_x"][i] for i in idx],
                y=[atlas["umap_y"][i] for i in idx],
                mode="markers",
                name="similar",
                marker={
                    "size": marker_size + 7,
                    "color": "rgba(0,0,0,0)",
                    "line": {"width": 2, "color": "#000000"},
                },
                hoverinfo="skip",
                showlegend=False,
            )
        )

    if selected_xy is not None:
        fig.add_trace(
            go.Scattergl(
                x=[selected_xy[0]],
                y=[selected_xy[1]],
                mode="markers",
                name="selected",
                marker={
                    "size": marker_size + 12,
                    "color": "#111111",
                    "symbol": "star",
                    "line": {"width": 1, "color": "#ffffff"},
                },
                hoverinfo="skip",
                showlegend=False,
            )
        )

    fig.update_layout(
        height=height,
        margin={"l": 0, "r": 120 if show_legend else 0, "t": 0, "b": 0},
        showlegend=show_legend,
        legend={
            "orientation": "v",
            "yanchor": "top",
            "y": 1 - 18 / height,
            "xanchor": "left",
            "x": 1.02,
            "font": {"size": 14},
        },
        xaxis={"visible": False, "range": x_range},
        yaxis={"visible": False, "range": y_range},
        dragmode="pan",
    )
    return fig


def focused_minimap(atlas: dict[str, list[Any]], selected: str) -> go.Figure | None:
    """A small atlas zoomed to a window around the selected protein (dense + local).

    No neighbour rings here: at this zoom the nearby dots are themselves the local
    look-alikes, and embedding-nearest neighbours can fall outside the window.
    """
    try:
        i = atlas["accession"].index(selected)
    except ValueError:
        return None
    sx, sy = atlas["umap_x"][i], atlas["umap_y"][i]
    half_w = (max(atlas["umap_x"]) - min(atlas["umap_x"])) * 0.03
    half_h = (max(atlas["umap_y"]) - min(atlas["umap_y"])) * 0.03
    return build_atlas_figure(
        atlas,
        selected,
        set(),
        x_range=[sx - half_w, sx + half_w],
        y_range=[sy - half_h, sy + half_h],
        height=420,
        marker_size=8,
        show_legend=True,
    )


def render_neighborhood_tab(card: dict[str, Any], selected: str) -> None:
    subheader(
        "Sequence neighborhood",
        "Proteins with the most similar shape, found by the ESM-2 AI model — a "
        "different axis from the interactome (STRING measures physical contact; "
        "this measures sequence resemblance, with no notion of “talking to”).",
    )
    with st.container(key="atlas_insight"):
        st.info(render.ATLAS_INSIGHT)
    atlas = load_atlas()
    try:
        neighbors = load_neighbors(selected)
    except ApiException:
        st.warning("Sequence-similarity search is temporarily unavailable.")
        neighbors = []
    neighbor_accs = {n["accession"] for n in neighbors}

    _, mid, _ = st.columns([1, 4, 1])
    with mid:
        minimap = focused_minimap(atlas, selected)
        if minimap is not None:
            st.plotly_chart(minimap, width="stretch", key="minimap")
            st.caption("★ is this protein; nearby dots are look-alikes, colored by family.")
        else:
            st.caption("No map position available for this protein.")

    if not neighbors:
        st.caption("No similar proteins found.")
    else:
        seq_lengths = load_sequence_lengths(tuple(n["accession"] for n in neighbors))
        with st.container(height=313):
            for neighbor in neighbors:
                metric = render.neighbor_metric_label(
                    neighbor["similarity"], seq_lengths.get(neighbor["accession"])
                )
                clickable_protein_row(neighbor, metric, "neighbor")
    st.caption(render.NEIGHBORS_HELP_CAPTION)

    with st.expander("Explore the full atlas of ~20,000 proteins"):
        full = build_atlas_figure(atlas, selected, neighbor_accs, height=560)
        event = st.plotly_chart(full, width="stretch", on_select="rerun", key="full_atlas")
        _handle_point_click(event, selected)


# ---------------------------------------------------------------------------
# Tab 3 — Clinical & therapeutic profile (the micro view)
# ---------------------------------------------------------------------------


def render_diseases(card: dict[str, Any], threshold: float) -> None:
    subheader(
        "Diseases linked to it",
        "Conditions associated with this protein — strongest evidence first.",
    )
    diseases = sorted(
        (d for d in card["top_diseases"] if d["overall_score"] >= threshold),
        key=lambda d: d["overall_score"],
        reverse=True,
    )
    if not diseases:
        st.caption("No disease associations above the current threshold.")
        return
    bars = ""
    for disease in diseases:
        pct = render.confidence_pct(disease["overall_score"])
        bars += (
            "<div style='margin-bottom:13px;'>"
            "<div style='display:flex;justify-content:space-between;"
            "font-size:0.92rem;color:#111111;'>"
            f"<span>{html.escape(disease['disease_name'])}</span>"
            f"<span style='color:#888888;'>evidence {pct}%</span></div>"
            "<div style='background:#e6e6e6;height:6px;border-radius:3px;margin-top:4px;'>"
            f"<div style='background:{render.DISEASE_COLOR};height:6px;width:{pct}%;"
            "border-radius:3px;'></div></div></div>"
        )
    st.markdown(f"<div class='card'>{bars}</div>", unsafe_allow_html=True)


def render_drugs(card: dict[str, Any]) -> None:
    subheader("Drugs that target it", "Medicines that act directly on this protein.")
    drugs = sorted(card["approved_drugs"], key=lambda d: d["max_phase"] or 0, reverse=True)
    if not drugs:
        st.markdown(
            "<div class='card' style='color:#888888;font-size:0.95rem;'>"
            f"{html.escape(render.drugs_empty_message(card.get('gene_symbol')))}</div>",
            unsafe_allow_html=True,
        )
        st.caption(render.DRUGS_HELP_CAPTION)
        return
    pills = ""
    for drug in drugs:
        nm = html.escape(drug["drug_name"] or drug["chembl_id"])
        phase = render.phase_label(drug["max_phase"])
        pills += (
            "<span style='display:inline-block;background:#ffffff;"
            f"border:1px solid {render.DRUG_COLOR};border-radius:20px;padding:6px 14px;"
            "margin:0 8px 8px 0;font-size:0.9rem;color:#111111;'>"
            f"<b>{nm}</b> <span style='color:{render.DRUG_COLOR};font-size:0.8rem;"
            f"font-weight:600;'>{phase}</span></span>"
        )
    st.markdown(f"<div class='card'>{pills}</div>", unsafe_allow_html=True)


def render_clinical_tab(card: dict[str, Any], threshold: float) -> None:
    subheader(
        "Clinical & therapeutic profile",
        "What goes wrong when this protein breaks, and the medicines that act on it directly.",
    )
    col_diseases, col_drugs = st.columns(2)
    with col_diseases:
        render_diseases(card, threshold)
    with col_drugs:
        render_drugs(card)


# ---------------------------------------------------------------------------
# Tab 4 — Amino acid composition: full sequence + ranked composition
# ---------------------------------------------------------------------------


def _aa_tooltip(entry: dict[str, Any]) -> str:
    """Help text for an amino acid: its biochemical role, plus diet note if essential."""
    parts = [entry.get("description") or ""]
    if entry.get("deficiency_note"):
        parts.append(entry["deficiency_note"])
    return " ".join(p for p in parts if p)


def _render_category_rollup(composition: list[dict[str, Any]]) -> None:
    """A stacked bar + legend summarizing this protein's side-chain chemistry.

    The per-amino-acid percentages, rolled up by category, become a per-protein
    fingerprint readable at a glance (hydrophobic-heavy vs polar-heavy vs charged).
    """
    breakdown = render.category_breakdown(composition)
    segments = "".join(
        f"<div style='width:{pct:.2f}%;background:{render.category_color(cat)};'"
        f" title='{html.escape(render.CATEGORY_LABEL.get(cat, cat))} {pct:.1f}%'></div>"
        for cat, pct in breakdown
    )
    legend = " &nbsp;·&nbsp; ".join(
        f"<span style='color:{render.category_color(cat)};'>■</span> "
        f"{html.escape(render.CATEGORY_LABEL.get(cat, cat))} {pct:.0f}%"
        for cat, pct in breakdown
    )
    st.markdown(
        "<div style='margin-bottom:18px;'>"
        "<div style='display:flex;height:10px;border-radius:5px;overflow:hidden;'>"
        f"{segments}</div>"
        f"<div style='color:#555555;font-size:0.8rem;margin-top:7px;'>{legend}</div></div>",
        unsafe_allow_html=True,
    )


def _render_composition_row(entry: dict[str, Any], max_pct: float) -> None:
    """One amino acid: its name and a bar relative to this protein's most common amino acid."""
    col_icon_name, col_bar = st.columns([2, 3], vertical_alignment="center")
    with col_icon_name:
        col_icon, col_name = st.columns([1, 9], gap="xxsmall", vertical_alignment="center")
        with col_icon:
            st.markdown("", help=_aa_tooltip(entry))
        with col_name:
            essential = entry.get("produced_by_body") is False
            marker = (
                "<span style='color:#888888;font-size:0.72rem;'> · essential</span>"
                if essential
                else ""
            )
            st.markdown(
                f"{html.escape(entry['name'])} ({html.escape(entry['three_letter_code'])}){marker}",
                unsafe_allow_html=True,
            )
    with col_bar:
        bar_pct = (entry["pct_of_sequence"] / max_pct * 100) if max_pct else 0.0
        color = render.category_color(entry["category"])
        st.markdown(
            "<div style='margin-top:4px;'>"
            "<div style='background:#e6e6e6;height:6px;border-radius:3px;'>"
            f"<div style='background:{color};height:6px;width:{bar_pct:.1f}%;"
            "border-radius:3px;'></div></div>"
            "<div style='color:#888888;font-size:0.78rem;margin-top:2px;'>"
            f"{render.aa_pct_label(entry['pct_of_sequence'], entry['count'])}</div></div>",
            unsafe_allow_html=True,
        )


def render_composition_tab(card: dict[str, Any], accession: str) -> None:
    subheader("Amino acid composition", render.COMPOSITION_INSIGHT)
    sequence = card.get("sequence")
    composition = load_composition(accession)
    if not sequence or not composition:
        st.caption("No sequence available for this protein.")
        return
    st.markdown(
        "<div class='card' style='font-family:monospace;font-size:0.82rem;"
        f"color:#111111;line-height:1.6;word-break:break-all;'>{html.escape(sequence)}</div>",
        unsafe_allow_html=True,
    )

    _render_category_rollup(composition)

    max_pct = max(e["pct_of_sequence"] for e in composition)
    half = (len(composition) + 1) // 2
    col_left, col_right = st.columns(2)
    for col, entries in ((col_left, composition[:half]), (col_right, composition[half:])):
        with col:
            for entry in entries:
                _render_composition_row(entry, max_pct)


def render_footer() -> None:
    st.divider()
    st.caption(
        "Data: UniProt (CC-BY) · STRING-DB (CC-BY) · Human Protein Atlas (CC-BY-SA) · "
        "Open Targets (CC0). A portfolio project — not medical advice."
    )


def main() -> None:
    inject_css()

    try:
        get_conn()
    except duckdb.Error:
        st.error(
            "Could not connect to the database. MotherDuck may be waking up from "
            "idle — please refresh in a moment."
        )
        return

    selected = current_accession()
    threshold = render_sidebar(selected)

    app_header()
    tour_step_index = st.session_state.get("tour_step")
    if tour_step_index is not None:
        render_tour_banner(tour.TOUR_STEPS[tour_step_index], tour_step_index)

    card = load_story_card(selected)
    if card is None:
        st.error(f"No protein found for accession “{selected}”. Try the search in the sidebar.")
        return

    render_identity(card)
    render_kpis(card, threshold)

    tab_interactome, tab_neighborhood, tab_clinical, tab_composition = st.tabs(
        [
            "Interactome topology",
            "Sequence neighborhood",
            "Clinical & therapeutic profile",
            "Amino acid composition",
        ]
    )
    with tab_interactome:
        render_interactome_tab(card, threshold=threshold)
    with tab_neighborhood:
        render_neighborhood_tab(card, selected)
    with tab_clinical:
        render_clinical_tab(card, threshold)
    with tab_composition:
        render_composition_tab(card, selected)

    render_footer()


main()
