{% docs __overview__ %}
# Protein Atlas — dbt project

A living atlas of ~20,000 reviewed human proteins, joined from UniProt, STRING-DB,
the Human Protein Atlas, and Open Targets on a single key: `uniprot_accession`. This
project transforms Bronze Parquet (landed in Cloudflare R2 by a separate Dagster
pipeline) into the Gold star schema that powers the
[live Streamlit atlas](https://human-protein-atlas-cqhrelt2uatfzhyt54udys.streamlit.app/).

**Layout**

- `staging/` — one view per source, 1:1 with the Bronze Parquet it reads.
- `marts/` — the Gold star schema: `dim_protein`, `dim_disease`, `dim_drug`, and five fact tables.
- `seeds/` — the 100-protein editorial CSV, the amino-acid glossary, and the HPA family-group lookup.
- `queries/` — the canonical story-card query, hand-ported into the Streamlit app (`apps/ui/data.py`).

**Where to start**: `dim_protein` is the anchor table every fact table joins to. Open
its page and follow the lineage graph to see how the four sources fan into it.

Source-by-source detail (URLs, license, refresh cadence, gotchas) lives in
[`docs/protein_atlas_data_source_manifest.md`](https://github.com/RM3006/human-protein-atlas/blob/main/docs/protein_atlas_data_source_manifest.md);
design rationale lives in
[`ARCHITECTURE.md`](https://github.com/RM3006/human-protein-atlas/blob/main/ARCHITECTURE.md).

**About the numbers on this site**: this site is generated from CI's zero-row Bronze
fixtures, not the production warehouse, so no MotherDuck credentials are ever loaded
into the workflow that publishes it. Column names, types, descriptions, and lineage
are accurate; row counts and the catalog's sample data are not — production holds
~20,431 proteins, not 0.
{% enddocs %}

{% docs uniprot_accession %}
UniProt primary accession (e.g. `P01308`) — the single join key across every source
in the atlas. Never gene symbol: symbols are many-to-many with accessions and drift
across releases, while accessions are stable (CLAUDE.md rule 1).
{% enddocs %}

{% docs efo_id %}
Experimental Factor Ontology (EFO) disease/phenotype ID from Open Targets (e.g.
`EFO_0001359`) — primary key of `dim_disease` and the join key for every disease
association in the atlas.
{% enddocs %}

{% docs chembl_id %}
ChEMBL molecule ID (e.g. `CHEMBL1201631`) — primary key of `dim_drug` and the join
key for every drug association in the atlas.
{% enddocs %}

{% docs ensembl_gene_id %}
Ensembl human gene ID (e.g. `ENSG00000254647`) — Open Targets' native target key.
Resolved to `uniprot_accession` via `stg_ot_targets.proteinIds` before joining
anything else in the atlas; not used as a cross-mart join key itself, since a small
number of UniProt accessions (paralog families such as histones and HLA) map to
multiple Ensembl gene IDs.
{% enddocs %}
