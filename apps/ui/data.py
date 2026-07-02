"""Data access for the Streamlit app: MotherDuck (DuckDB) queries.

The Streamlit app calls these functions directly (no API tier). The connection object
is created by connect_motherduck and cached in app.py via st.cache_resource; the query
functions take a connection so they stay framework-agnostic and testable against an
in-memory DuckDB loaded with fixtures.

The story-card statement is a hand-port of models/queries/protein_story_card.sql
(the canonical spec): dbt `{{ ref('x') }}` -> `x`, `{{ var("accession") }}` -> `?`,
plus the `family_group` column. Its three LIST(STRUCT) columns come back from DuckDB
as nested Python lists of dicts.
"""

import threading
from decimal import Decimal
from typing import Any

import duckdb

# DuckDB connections are not safe for concurrent use, and st.cache_resource shares one
# connection across all sessions, so serialize the (sub-100ms) queries with a lock.
_query_lock = threading.Lock()

_LIST_FIELDS = ("top_interaction_partners", "top_diseases", "approved_drugs")


def connect_motherduck(token: str) -> duckdb.DuckDBPyConnection:
    """Open a MotherDuck connection to the `atlas` database."""
    return duckdb.connect(f"md:atlas?motherduck_token={token}")


STORY_CARD_SQL = """
SELECT
    p.uniprot_accession,
    p.gene_symbol,
    p.protein_name,
    p.sequence_length,
    p.sequence,
    p.pfam_id,
    p.function_raw,
    p.function_friendly,
    p.tagline,
    p.is_curated,
    p.protein_class,
    p.subcellular_location,
    p.family_group,
    fpt.tissue            AS tissue_specificity,
    fpt.expression_level  AS tissue_distribution,
    (
        SELECT LIST({
            'accession': dp.uniprot_accession,
            'gene_symbol': dp.gene_symbol,
            'protein_name': dp.protein_name,
            'combined_score': ROUND(partner.combined_score / 1000.0, 3)
        })
        FROM (
            SELECT
                CASE
                    WHEN i.uniprot_a = p.uniprot_accession THEN i.uniprot_b
                    ELSE i.uniprot_a
                END AS partner_accession,
                i.combined_score
            FROM fact_interaction i
            WHERE i.uniprot_a = p.uniprot_accession
               OR i.uniprot_b = p.uniprot_accession
            ORDER BY i.combined_score DESC
            LIMIT 20
        ) partner
        JOIN dim_protein dp ON dp.uniprot_accession = partner.partner_accession
    ) AS top_interaction_partners,
    (
        SELECT LIST({
            'efo_id': pd.efo_id,
            'disease_name': d.disease_name,
            'overall_score': ROUND(pd.overall_score, 3)
        })
        FROM (
            SELECT efo_id, overall_score
            FROM fact_protein_disease
            WHERE uniprot_accession = p.uniprot_accession
            ORDER BY overall_score DESC
            LIMIT 5
        ) pd
        JOIN dim_disease d ON pd.efo_id = d.efo_id
    ) AS top_diseases,
    (
        SELECT LIST({
            'chembl_id': drug.chembl_id,
            'drug_name': drug.drug_name,
            'max_phase': drug.max_phase
        })
        FROM (
            SELECT DISTINCT dr.chembl_id, dr.drug_name, dr.max_phase
            FROM fact_drug_target_disease fdt
            JOIN dim_drug dr ON fdt.chembl_id = dr.chembl_id
            WHERE fdt.uniprot_accession = p.uniprot_accession
              AND dr.max_phase >= 3
            ORDER BY dr.max_phase DESC
            LIMIT 5
        ) drug
    ) AS approved_drugs
FROM dim_protein p
LEFT JOIN fact_protein_tissue fpt
    ON p.uniprot_accession = fpt.uniprot_accession
WHERE p.uniprot_accession = ?
"""

SEARCH_SQL = """
SELECT uniprot_accession, gene_symbol, protein_name
FROM dim_protein
WHERE gene_symbol ILIKE ? OR protein_name ILIKE ? OR uniprot_accession ILIKE ?
ORDER BY
    CASE WHEN gene_symbol ILIKE ? OR uniprot_accession ILIKE ? OR protein_name ILIKE ?
         THEN 0 ELSE 1 END,
    length(COALESCE(protein_name, ''))
LIMIT 20
"""

ATLAS_SQL = """
SELECT
    p.uniprot_accession,
    p.gene_symbol,
    p.family_group,
    e.umap_x,
    e.umap_y,
    COALESCE(pd.disease_count, 0) AS disease_count,
    COALESCE(dc.drug_count, 0) AS drug_count
FROM dim_protein p
JOIN fact_embedding e ON p.uniprot_accession = e.uniprot_accession
LEFT JOIN (
    SELECT uniprot_accession, COUNT(*) AS disease_count
    FROM fact_protein_disease
    GROUP BY uniprot_accession
) pd ON pd.uniprot_accession = p.uniprot_accession
LEFT JOIN (
    SELECT fdt.uniprot_accession, COUNT(DISTINCT fdt.chembl_id) AS drug_count
    FROM fact_drug_target_disease fdt
    JOIN dim_drug d ON fdt.chembl_id = d.chembl_id
    WHERE d.max_phase >= 3
    GROUP BY fdt.uniprot_accession
) dc ON dc.uniprot_accession = p.uniprot_accession
"""


def _floatify_scores(card: dict[str, Any]) -> None:
    """Coerce NUMERIC disease scores (DuckDB returns Decimal) to plain floats in place."""
    for disease in card["top_diseases"]:
        score = disease.get("overall_score")
        if isinstance(score, Decimal):
            disease["overall_score"] = float(score)


def fetch_story_card(conn: duckdb.DuckDBPyConnection, accession: str) -> dict[str, Any] | None:
    """Return the full story-card row for an accession, or None if it does not exist."""
    with _query_lock:
        cursor = conn.execute(STORY_CARD_SQL, [accession])
        columns = [desc[0] for desc in cursor.description]
        row = cursor.fetchone()
    if row is None:
        return None
    card: dict[str, Any] = dict(zip(columns, row, strict=True))
    # LIST aggregates over zero rows return NULL, not []; normalize so the UI iterates
    # uniformly (insulin's approved_drugs is the canonical empty case).
    for field in _LIST_FIELDS:
        if card.get(field) is None:
            card[field] = []
    _floatify_scores(card)
    return card


def search_proteins(conn: duckdb.DuckDBPyConnection, query: str) -> list[dict[str, Any]]:
    """Search proteins by gene symbol, name, or accession; prefix matches rank first."""
    contains = f"%{query}%"
    prefix = f"{query}%"
    with _query_lock:
        rows = conn.execute(
            SEARCH_SQL, [contains, contains, contains, prefix, prefix, prefix]
        ).fetchall()
    return [{"uniprot_accession": r[0], "gene_symbol": r[1], "protein_name": r[2]} for r in rows]


def list_proteins(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Return (accession, gene_symbol, protein_name) for every protein (search dropdown)."""
    rows = conn.execute(
        "SELECT uniprot_accession, gene_symbol, protein_name FROM dim_protein"
    ).fetchall()
    return [{"uniprot_accession": r[0], "gene_symbol": r[1], "protein_name": r[2]} for r in rows]


def fetch_atlas(conn: duckdb.DuckDBPyConnection) -> dict[str, list[Any]]:
    """Return every UMAP point as parallel columnar arrays for the scatter plot."""
    with _query_lock:
        rows = conn.execute(ATLAS_SQL).fetchall()
    return {
        "accession": [r[0] for r in rows],
        "gene_symbol": [r[1] for r in rows],
        "family_group": [r[2] for r in rows],
        "umap_x": [r[3] for r in rows],
        "umap_y": [r[4] for r in rows],
        "disease_count": [r[5] for r in rows],
        "drug_count": [r[6] for r in rows],
    }


COMPOSITION_SQL = """
SELECT
    c.amino_acid_code,
    aa.name,
    aa.three_letter_code,
    aa.category,
    aa.produced_by_body,
    aa.description,
    aa.deficiency_note,
    c."count",
    c.pct_of_sequence
FROM fact_protein_aa_composition c
JOIN seed_amino_acids aa ON c.amino_acid_code = aa.amino_acid_code
WHERE c.uniprot_accession = ?
ORDER BY c.pct_of_sequence DESC
"""


def fetch_composition(conn: duckdb.DuckDBPyConnection, accession: str) -> list[dict[str, Any]]:
    """Return the 20-row amino-acid composition for a protein, richest first."""
    with _query_lock:
        rows = conn.execute(COMPOSITION_SQL, [accession]).fetchall()
    return [
        {
            "amino_acid_code": r[0],
            "name": r[1],
            "three_letter_code": r[2],
            "category": r[3],
            "produced_by_body": r[4],
            "description": r[5],
            "deficiency_note": r[6],
            "count": r[7],
            "pct_of_sequence": r[8],
        }
        for r in rows
    ]


def fetch_sequence_lengths(
    conn: duckdb.DuckDBPyConnection, accessions: list[str]
) -> dict[str, int]:
    """Return {accession: sequence_length} for the given accessions.

    Used for the "Sequence neighborhood" list, whose rows come from
    fact_protein_neighbor (no sequence_length there) and need a small batched lookup.
    """
    if not accessions:
        return {}
    placeholders = ", ".join("?" for _ in accessions)
    with _query_lock:
        rows = conn.execute(
            f"SELECT uniprot_accession, sequence_length FROM dim_protein "
            f"WHERE uniprot_accession IN ({placeholders})",
            accessions,
        ).fetchall()
    return {r[0]: r[1] for r in rows}


NEIGHBORS_SQL = """
SELECT dp.uniprot_accession, dp.gene_symbol, dp.protein_name, n.similarity
FROM fact_protein_neighbor n
JOIN dim_protein dp ON dp.uniprot_accession = n.neighbor_accession
WHERE n.uniprot_accession = ?
ORDER BY n.rank
LIMIT ?
"""


def find_neighbors(
    conn: duckdb.DuckDBPyConnection, accession: str, k: int = 10
) -> list[dict[str, Any]]:
    """Return up to k nearest proteins by ESM-2 cosine similarity, excluding self.

    Reads the precomputed fact_protein_neighbor table (top-20 per protein, written by
    the protein_neighbors Dagster asset) instead of a live vector-search call.
    """
    with _query_lock:
        rows = conn.execute(NEIGHBORS_SQL, [accession, k]).fetchall()
    return [
        {
            "accession": r[0],
            "gene_symbol": r[1],
            "protein_name": r[2],
            "similarity": round(r[3], 3),
        }
        for r in rows
    ]
