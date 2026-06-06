#!/usr/bin/env python3
"""Generate models/seeds/dim_protein_editorial.csv from the top-100 curation list.

Parses docs/protein_atlas_curation_list.md for Gene, Tagline, and Function_friendly,
then queries MotherDuck's dim_protein to resolve gene symbols to UniProt accessions.
The markdown file is the single source of truth — edit it, then re-run this script.

Run from the project root (requires MOTHERDUCK_TOKEN in .env.local):
    python notebooks/generate_editorial_seed.py

After running:
  1. Review models/seeds/dim_protein_editorial.csv — check for missing proteins.
  2. git add models/seeds/dim_protein_editorial.csv && git commit
  3. cd models && dbt seed
  4. cd models && dbt run --select dim_protein
"""

import csv
import os
from pathlib import Path

import duckdb


def _load_env_local() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _parse_curation_list(md_path: Path) -> list[tuple[str, str, str]]:
    """Extract (gene_symbol, tagline, function_friendly) rows from the markdown tables."""
    entries: list[tuple[str, str, str]] = []
    for line in md_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 3:
            continue
        gene, tagline, func = cells[0], cells[1], cells[2]
        # Skip header rows and separator rows (--- cells)
        if not gene or gene == "Gene" or gene.startswith("-"):
            continue
        entries.append((gene, tagline, func))
    return entries


def main() -> None:
    _load_env_local()

    md_path = Path(__file__).resolve().parents[1] / "docs" / "protein_atlas_curation_list.md"
    curated = _parse_curation_list(md_path)
    print(f"Parsed {len(curated)} entries from {md_path.name}")

    token = os.environ["MOTHERDUCK_TOKEN"]
    conn = duckdb.connect(f"md:atlas?motherduck_token={token}")

    symbols = [gs for gs, _, _ in curated]
    placeholders = ", ".join(f"'{s}'" for s in symbols)
    rows = conn.execute(
        f"SELECT gene_symbol, uniprot_accession "
        f"FROM dim_protein "
        f"WHERE gene_symbol IN ({placeholders})"
    ).fetchall()

    symbol_to_accession: dict[str, str] = {r[0]: r[1] for r in rows}

    seed_dir = Path(__file__).resolve().parents[1] / "models" / "seeds"
    out_path = seed_dir / "dim_protein_editorial.csv"
    seed_dir.mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    written = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["uniprot_accession", "tagline", "function_friendly", "is_curated"])
        for gene_symbol, tagline, function_friendly in curated:
            accession = symbol_to_accession.get(gene_symbol)
            if accession is None:
                missing.append(gene_symbol)
                continue
            writer.writerow([accession, tagline, function_friendly, "true"])
            written += 1

    print(f"Written {written} rows to {out_path}")
    if missing:
        print(f"\nWARNING: {len(missing)} gene symbol(s) not found in dim_protein:")
        for sym in missing:
            print(f"  {sym}")
        print(
            "\nPossible causes: UniProt uses a different primary gene symbol, "
            "or the protein is absent from the Swiss-Prot reviewed set. "
            "Look up the correct symbol in stg_uniprot and add the row manually."
        )
    else:
        print("All 100 gene symbols resolved successfully.")

    print(
        "\nNext steps:\n"
        "  1. Review the CSV for correctness.\n"
        "  2. git add models/seeds/dim_protein_editorial.csv && git commit\n"
        "  3. cd models && dbt seed\n"
        "  4. Run the protein_llm_rewrites Dagster asset (submits Anthropic batch).\n"
        "  5. cd models && dbt run --select dim_protein"
    )


if __name__ == "__main__":
    main()
