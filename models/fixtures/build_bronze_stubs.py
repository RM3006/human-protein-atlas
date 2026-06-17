"""Generate zero-row Bronze Parquet stubs for offline CI.

Each stub has the exact schema the corresponding staging model reads from
read_parquet(). Zero rows let dbt build every model (producing empty tables
with correct column types) so that unit tests can subsequently introspect
upstream relations and run against their own inline mock data.

Run once whenever a source schema changes:
    uv run python models/fixtures/build_bronze_stubs.py
Commit the generated .parquet files alongside this script.
"""

from pathlib import Path

import polars as pl

ROOT = Path(__file__).parent / "bronze"


def write(df: pl.DataFrame, *parts: str) -> None:
    path = ROOT.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
    print(f"wrote {path.relative_to(ROOT.parent.parent)} ({df.height} rows)")


# --- UniProt ---
write(
    pl.DataFrame(
        schema={
            "primary_accession": pl.String,
            "secondary_accessions": pl.List(pl.String),
            "gene_symbol": pl.String,
            "protein_name": pl.String,
            "sequence_length": pl.Int64,
            "sequence": pl.String,
            "function_raw": pl.String,
            "keywords": pl.List(pl.String),
            "pfam_ids": pl.List(pl.String),
            "ensembl_gene_ids": pl.List(pl.String),
            "string_ids": pl.List(pl.String),
        }
    ),
    "uniprot/v2026_01/uniprot_human_reviewed_raw.parquet",
)

# --- STRING ---
write(
    pl.DataFrame(
        schema={
            "uniprot_a": pl.String,
            "uniprot_b": pl.String,
            "combined_score": pl.Int32,
        }
    ),
    "string/v12.0/string_interactions.parquet",
)

# --- HPA ---
write(
    pl.DataFrame(
        schema={
            "uniprot_accession": pl.String,
            "gene_symbol": pl.String,
            "protein_class": pl.String,
            "rna_tissue_specificity": pl.String,
            "rna_tissue_distribution": pl.String,
            "subcellular_location": pl.String,
            "disease_involvement": pl.String,
        }
    ),
    "hpa/v24/hpa_proteome.parquet",
)

# --- Open Targets: associations ---
write(
    pl.DataFrame(
        schema={
            "targetId": pl.String,
            "diseaseId": pl.String,
            "associationScore": pl.Float64,
        }
    ),
    "opentargets/v26.03/ot_associations.parquet",
)

# --- Open Targets: diseases ---
write(
    pl.DataFrame(schema={"id": pl.String, "name": pl.String}),
    "opentargets/v26.03/ot_diseases.parquet",
)

# --- Open Targets: targets ---
# proteinIds is a List of {source, id} structs; list_filter in stg_ot_targets uses it.
write(
    pl.DataFrame(
        schema={
            "id": pl.String,
            "approvedSymbol": pl.String,
            "approvedName": pl.String,
            "proteinIds": pl.List(pl.Struct({"source": pl.String, "id": pl.String})),
        }
    ),
    "opentargets/v26.03/ot_targets.parquet",
)

# --- Open Targets: drugs (clinical_target) ---
# diseases is a List of structs; fact_drug_target_disease unnests on .diseaseId.
write(
    pl.DataFrame(
        schema={
            "drugId": pl.String,
            "targetId": pl.String,
            "diseases": pl.List(pl.Struct({"diseaseId": pl.String})),
            "maxClinicalStage": pl.String,
        }
    ),
    "opentargets/v26.03/ot_drugs.parquet",
)

# --- Open Targets: drug molecules ---
write(
    pl.DataFrame(
        schema={
            "id": pl.String,
            "name": pl.String,
            "drugType": pl.String,
        }
    ),
    "opentargets/v26.03/ot_drug_molecules.parquet",
)

# --- LLM rewrites ---
write(
    pl.DataFrame(
        schema={
            "uniprot_accession": pl.String,
            "function_friendly": pl.String,
            "tagline": pl.String,
        }
    ),
    "llm/v2026_06/protein_rewrites.parquet",
)
