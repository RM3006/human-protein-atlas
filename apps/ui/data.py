"""Data access for the Streamlit app: MotherDuck (DuckDB) queries + Qdrant search.

The Streamlit app calls these functions directly (no API tier). Connection objects
are created by the factory helpers here and cached in app.py via st.cache_resource;
the query functions take a connection/client so they stay framework-agnostic and
testable against an in-memory DuckDB loaded with fixtures.

The story-card statement is a hand-port of models/queries/protein_story_card.sql
(the canonical spec): dbt `{{ ref('x') }}` -> `x`, `{{ var("accession") }}` -> `?`,
plus the `family_group` column. Its three LIST(STRUCT) columns come back from DuckDB
as nested Python lists of dicts.
"""

import hashlib
import threading
from decimal import Decimal
from typing import Any, cast

import duckdb
from qdrant_client import QdrantClient

QDRANT_COLLECTION = "proteins"

# DuckDB connections are not safe for concurrent use, and st.cache_resource shares one
# connection across all sessions, so serialize the (sub-100ms) queries with a lock.
_query_lock = threading.Lock()

_LIST_FIELDS = ("top_interaction_partners", "top_diseases", "approved_drugs")


def connect_motherduck(token: str) -> duckdb.DuckDBPyConnection:
    """Open a MotherDuck connection to the `atlas` database."""
    return duckdb.connect(f"md:atlas?motherduck_token={token}")


def make_qdrant_client(url: str, api_key: str) -> QdrantClient:
    """Create a Qdrant Cloud client."""
    return QdrantClient(url=url, api_key=api_key)


def accession_to_id(accession: str) -> int:
    """Stable positive int64 Qdrant point ID from a UniProt accession.

    Mirrors atlas.assets.ml.embeddings.accession_to_id (the IDs were assigned in Part 4).
    """
    digest = hashlib.sha256(accession.encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


STORY_CARD_SQL = """
SELECT
    p.uniprot_accession,
    p.gene_symbol,
    p.protein_name,
    p.sequence_length,
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


def fetch_sequence_lengths(
    conn: duckdb.DuckDBPyConnection, accessions: list[str]
) -> dict[str, int]:
    """Return {accession: sequence_length} for the given accessions.

    Used for the "Sequence neighborhood" list, whose rows come from the Qdrant
    payload (no sequence_length there) and need a small batched DuckDB lookup.
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


def find_neighbors(
    client: QdrantClient, accession: str, k: int = 10, collection: str = QDRANT_COLLECTION
) -> list[dict[str, Any]]:
    """Return up to k nearest proteins by ESM-2 cosine similarity, excluding self."""
    point_id = accession_to_id(accession)
    found = client.retrieve(collection_name=collection, ids=[point_id], with_vectors=True)
    if not found:
        return []
    vector = found[0].vector
    if vector is None:
        return []
    # The `proteins` collection stores a single unnamed vector, so .vector is a plain
    # list[float] (not the named-vector dict the union type also allows).
    response = client.query_points(
        collection_name=collection,
        query=cast(list[float], vector),
        limit=k + 1,
        with_payload=True,
    )
    hits: list[dict[str, Any]] = []
    for point in response.points:
        payload = point.payload or {}
        neighbor_accession = payload.get("uniprot_accession")
        if neighbor_accession is None or neighbor_accession == accession:
            continue
        hits.append(
            {
                "accession": neighbor_accession,
                "gene_symbol": payload.get("gene_symbol"),
                "protein_name": payload.get("protein_name"),
                "similarity": round(point.score, 3),
            }
        )
        if len(hits) >= k:
            break
    return hits
