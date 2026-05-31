# Protein Atlas — Data Source Manifest

## What this document is

This document defines the **data contract** of the protein atlas. It answers, precisely, four questions for every external data source the project uses:

1. **Where does the data come from?** (URL, format, access method)
2. **What exactly do we pull from it?** (specific column names, with example values)
3. **How does it join to everything else?** (the key, with a worked example)
4. **What do we trust and what should we watch out for?** (license, refresh cadence, gotchas)

If you can answer those four questions for every source, you can build the pipeline. If you can't, you can't. This is the document that prevents the "I have data in fifteen folders and don't know what's in any of them" failure mode that kills most personal projects.

A reviewer reading the repo will look for exactly this document. Having it is the difference between "this project gathers data from public sources" and "this project gathers data from `uniprot.proteins`, `string.protein_links_v12`, `opentargets.target_disease_associations_24.09`, etc." The second framing reads as production engineering.

---

## The five sources at a glance

| # | Source | What it gives the story card | Access | Format | License |
|---|---|---|---|---|---|
| 1 | **UniProt** | Name, sequence, length, family, raw function text | REST API + bulk FTP | JSON / TSV / FASTA | CC-BY 4.0 |
| 2 | **STRING-DB** | "Who this protein talks to" — interaction partners | Bulk download | TSV (gzipped) | CC-BY 4.0 |
| 3 | **Human Protein Atlas** | "Where in the body" — tissue expression, subcellular location | Bulk download | TSV | CC-BY-SA 3.0 |
| 4 | **Open Targets** | "When broken" — disease associations + "Drugs" — therapy links | Bulk download | Parquet | CC0 (fully open) |
| 5 | **ChEMBL** | "How strongly drugs bind" — IC50, Ki, pChEMBL bioactivity | REST API + SQL dump | JSON / SQLite | CC-BY-SA 3.0 |

**Anchor identifier across all five: the UniProt accession** (e.g., `P01308` for insulin). Every other source either uses this directly or has a mapping table that brings it back to UniProt. This is the single non-negotiable design decision — pick the anchor early and everything else falls into place.

---

## How the five sources connect

```
                          ┌─────────────────────┐
                          │      UniProt        │  ← anchor: accession (e.g. P01308)
                          │  (every protein)    │     also: HGNC symbol, Ensembl ID
                          └──────────┬──────────┘
                                     │
              ┌─────────────┬────────┼────────┬─────────────────┐
              │             │        │        │                 │
              ▼             ▼        ▼        ▼                 ▼
      ┌───────────┐  ┌──────────┐  ┌────┐  ┌──────────────┐  ┌─────────┐
      │  STRING   │  │   HPA    │  │ OT │  │  Open Targets│  │ ChEMBL  │
      │           │  │          │  │    │  │              │  │         │
      │ joins via │  │ joins via│  │ via│  │  joins via   │  │joins via│
      │   ENSP    │  │ UniProt  │  │Ens-│  │ Ensembl gene │  │ UniProt │
      │ (→ map to │  │ direct   │  │embl│  │ id (→ map to │  │  direct │
      │  UniProt) │  │ column   │  │gene│  │   UniProt)   │  │ column  │
      └───────────┘  └──────────┘  └────┘  └──────────────┘  └─────────┘
       interactions   tissues      diseases  drugs           bioactivity
```

Three of the five (HPA, ChEMBL, UniProt itself) give you the UniProt accession directly in their files. Two (STRING and Open Targets) use a different ID and need a mapping step. The mapping is simple — UniProt's own `idmapping` files do it — but it has to happen, and forgetting it is the #1 way new pipelines silently lose half their data.

---

## Source 1 — UniProt

### What it is

UniProt is the world's reference catalog of proteins. Run by the EBI in the UK, the SIB in Switzerland, and PIR in the US. The "Swiss-Prot" subset (~570k entries) is human-reviewed; the "TrEMBL" subset (~250M entries) is auto-generated. **For this project we use Swiss-Prot only, filtered to human (taxonomy 9606)** — about 20,400 proteins.

### Why we need it

UniProt is the **anchor** of the entire data model. The accession (`P01308`, `P53_HUMAN`, etc.) is the primary key for every other join. UniProt also provides:

- The amino acid sequence (the input to ESM-2)
- The protein name, gene symbol, length, family
- A free-text `FUNCTION` field that the LLM rewrites into plain English

### How to get it

Two paths. For the bulk load (the one-shot ingest that builds the whole atlas), use the **proteomes FTP dump** which is much faster than the API. For incremental refreshes (a few dozen proteins at a time), use the REST API.

**Bulk:** [https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/proteomes/](https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/proteomes/) — download `UP000005640_9606.fasta.gz` (sequences) and `UP000005640_9606.xml.gz` (full annotations). Reference proteome for human, ~21,000 entries.

**API:** [https://rest.uniprot.org/uniprotkb/search](https://rest.uniprot.org/uniprotkb/search) with query parameters `query=reviewed:true+AND+organism_id:9606&format=json&size=500&cursor=...`. Paginated, ~40 calls to get everything.

### What we pull

| Field | Example | Used for |
|---|---|---|
| `primaryAccession` | `P01308` | Primary key |
| `secondaryAccessions` | `[Q14CN3]` | Legacy ID handling |
| `genes[0].geneName.value` | `INS` | HGNC symbol; used to join HPA and Open Targets |
| `proteinDescription.recommendedName.fullName.value` | `Insulin` | Display name |
| `sequence.length` | `110` | Story card field |
| `sequence.value` | `MALWMRLLPLLALLALWGPDPAAAFV…` | Input to ESM-2 |
| `comments[type=FUNCTION].texts[0].value` | `Insulin decreases blood glucose…` | Raw function; LLM rewrites this |
| `keywords[*].value` | `[Hormone, Diabetes mellitus, ...]` | Tagging, filtering |
| `dbReferences[type=Pfam].id` | `PF00049` | Protein family |
| `dbReferences[type=Ensembl].id` | `ENSG00000254647` | **Join key for Open Targets** |
| `dbReferences[type=STRING].id` | `9606.ENSP00000250971` | **Join key for STRING** |

### How it joins

UniProt **provides** every other source's join key. Pull all `dbReferences` and store them in a side table, then build the join in your warehouse.

| To reach… | Use UniProt's xref to… | Example |
|---|---|---|
| STRING | `STRING` xref | `P01308` → `9606.ENSP00000250971` |
| Open Targets | `Ensembl` xref (gene level) | `P01308` → `ENSG00000254647` |
| Human Protein Atlas | direct UniProt column in HPA file | `P01308` → row in `proteinatlas.tsv` |
| ChEMBL | direct UniProt column in ChEMBL `component_sequences` | `P01308` → `CHEMBL5881` |

### License, cadence, volume

- **License**: CC-BY 4.0. Attribution required; commercial use fine.
- **Refresh cadence**: UniProt releases monthly. For this project, **refresh once at project start**, then quarterly thereafter. The famous 100 proteins virtually never change; only their cross-references do.
- **Volume**: Human Swiss-Prot is ~30 MB compressed JSON. Trivial.

### Gotchas

- **Isoforms.** Some proteins have multiple isoforms (e.g., `P01308-1`, `P01308-2`). Use the canonical form (`P01308`) only — ignore isoforms in v1.
- **Secondary accessions.** UniProt sometimes merges entries, leaving old accessions as `secondaryAccessions`. Always resolve secondary → primary before joining.
- **The function text is biologist-flavored.** "Insulin decreases blood glucose concentration. It increases cell permeability to monosaccharides, amino acids and fatty acids…" — don't show this to a layperson without rewriting.

---

## Source 2 — STRING-DB

### What it is

STRING is a database of known and predicted protein-protein interactions, both physical (two proteins bind each other) and functional (two proteins act in the same pathway). Maintained by EMBL and the SIB. **Current version: v12.0** (released July 2023; v13 expected late 2025).

### Why we need it

This populates the **"who it talks to"** slot on every story card. For insulin, it gives us INSR (the receptor), IGF1 (sibling), SLC2A4 (the sugar gate it activates), GCG (its opposite).

### How to get it

Bulk download only — there is an API but it rate-limits hard for any meaningful query. Use the bulk files.

**URL**: [https://stringdb-downloads.org/download/](https://stringdb-downloads.org/download/) (or `https://string-db.org/cgi/download` for the friendlier browse)

**Files to download** (for human, species `9606`):

- `9606.protein.links.v12.0.txt.gz` — the interaction edges, ~190 MB compressed, ~1.6 GB uncompressed, ~11.7 million rows
- `9606.protein.aliases.v12.0.txt.gz` — the mapping from STRING's ENSP IDs to UniProt and gene symbols, ~25 MB

### What we pull

From `protein.links.v12.0.txt`:

| Column | Example | Used for |
|---|---|---|
| `protein1` | `9606.ENSP00000250971` | Source protein (STRING ID) |
| `protein2` | `9606.ENSP00000303830` | Target protein (STRING ID) |
| `combined_score` | `999` | Confidence, 0–1000 integer |

From `protein.aliases.v12.0.txt`:

| Column | Example | Used for |
|---|---|---|
| `#string_protein_id` | `9606.ENSP00000250971` | STRING ID |
| `alias` | `P01308` | UniProt accession (when `source` = `Ensembl_UniProt`) |
| `source` | `Ensembl_UniProt` | Filter to keep only UniProt mappings |

### How it joins

Two-step join through the aliases file:

```
STRING edge row:        9606.ENSP00000250971  ──linked to──  9606.ENSP00000303830
                                │                                    │
   aliases lookup ──────────────┘                                    └──────── aliases lookup
                                │                                    │
                                ▼                                    ▼
                          P01308 (INS)                          P06213 (INSR)
```

So one STRING interaction row, after joining the aliases twice, becomes a `(uniprot_a, uniprot_b, score)` triplet you can store directly.

**Filtering**: STRING ships with everything. For the story card, keep only edges with `combined_score >= 700` (the "high confidence" threshold STRING itself recommends), and for each protein keep the top 5 partners. That collapses 11.7M edges to roughly 100,000 useful ones.

### License, cadence, volume

- **License**: CC-BY 4.0. Attribution required.
- **Refresh cadence**: STRING ships a major version every ~2 years. **Refresh once at project start.** No need to repeat unless v13 comes out.
- **Volume**: ~215 MB compressed, post-filter ~5 MB stored.

### Gotchas

- **The IDs are not UniProt.** This is the single biggest source of confusion. The aliases mapping step is mandatory.
- **The `combined_score` is an integer, not a probability.** Divide by 1000 to display as a confidence.
- **Many proteins map to multiple ENSP IDs** (different isoforms). Pick the canonical one — UniProt's `Ensembl` xref tells you which.

---

## Source 3 — Human Protein Atlas (HPA)

### What it is

The Human Protein Atlas is a Swedish project (Karolinska Institute) that has spent ~20 years systematically antibody-staining every human protein across 44 tissues and 64 cell lines. It's the source of truth for **"where in the body does this protein actually live?"**

### Why we need it

The "Made in / Travels through / Acts on" body diagram on the insulin card came from HPA. For every protein, HPA tells you which tissues express it strongly, weakly, or not at all, and which subcellular compartment it lives in (nucleus, membrane, cytoplasm, mitochondria, etc.).

### How to get it

**URL**: [https://www.proteinatlas.org/about/download](https://www.proteinatlas.org/about/download)

**Files**:

- `proteinatlas.tsv` (~40 MB) — the all-in-one file with one row per gene and many wide columns
- `normal_tissue.tsv` (~50 MB) — long-format tissue-expression detail
- `subcellular_location.tsv` (~10 MB) — long-format subcellular detail

For v1, **just `proteinatlas.tsv` is enough** — it has the summary fields we need.

### What we pull from `proteinatlas.tsv`

| Column | Example | Used for |
|---|---|---|
| `Gene` | `INS` | HGNC gene symbol |
| `Uniprot` | `P01308` | **Direct join key** |
| `Protein class` | `Predicted secreted proteins, Plasma proteins` | Tag chips |
| `RNA tissue specificity` | `Tissue enhanced` | "Made in" slot (replaces removed `Tissue expression`) |
| `RNA tissue distribution` | `Detected in single` | Breadth of expression |
| `Subcellular location` | `Vesicles, Golgi apparatus` | Detail field |
| `Disease involvement` | `Diabetes mellitus, FDA approved drug targets` | Tag chip |

> **v24 schema change**: the `Tissue expression` column (which previously gave "Tissue enhanced (pancreas)"-style values) was removed in HPA v24. `RNA tissue specificity` now carries the equivalent tissue category information. The specific tissue name is no longer available in a single summary column; use the long-format `normal_tissue.tsv` file if per-tissue detail is needed in a later version.

### How it joins

Direct: the `Uniprot` column gives you the accession. One `LEFT JOIN dim_protein ON hpa.Uniprot = dim_protein.uniprot_accession` and you're done.

### License, cadence, volume

- **License**: CC-BY-SA 3.0. Attribution required. The `-SA` part ("share-alike") means if you republish the data verbatim, you must license your version the same way. For a portfolio you display data through the UI; you're not republishing the raw file, so this is comfortable.
- **Refresh cadence**: Annual releases. Refresh once a year, otherwise stable.
- **Volume**: ~40 MB.

### Gotchas

- **The `Uniprot` column is sometimes empty** for less-characterized proteins. Use a `LEFT JOIN` and tolerate nulls.
- **Some genes have multiple HPA rows** (rare but happens). Deduplicate on `Uniprot`, keeping the first.
- **`Tissue expression` column removed in v24.** The ingest uses `RNA tissue specificity` in its place. The Bronze schema has no `tissue_expression` column — dbt staging maps `rna_tissue_specificity` to the "Made in" story-card slot.

---

## Source 4 — Open Targets

### What it is

Open Targets is a UK-based consortium (EMBL-EBI, Sanger, GSK, Sanofi, Pfizer, Bristol Myers Squibb) that has built **the canonical aggregated platform for drug discovery**. It already does the dirty work of merging gene–disease associations from DisGeNET, ClinVar, GWAS Catalog, OMIM, ChEMBL, and a dozen others into one harmonized dataset. **This is the single biggest leverage point in this manifest** — one source replaces five.

### Why we need it

Two slots on the story card:

- **"When broken"** — disease associations (e.g., insulin → type 1 diabetes, type 2 diabetes, insulinoma)
- **"Drugs that work with it"** — drug-target relationships, fully reconciled with disease evidence (e.g., insulin → Humulin, Lispro, Glargine)

It also gives you a single composite confidence score per gene-disease pair that you can sort by.

### How to get it

**URL**: [https://platform.opentargets.org/downloads](https://platform.opentargets.org/downloads)

Open Targets releases quarterly with semantic versioning like `24.09` (year.month). Get the latest. Files are **Parquet**, which is data-engineering paradise: typed, compressed, columnar, splittable, readable by DuckDB and Spark and pandas alike.

**Datasets to pull** (only these four, the rest are out of scope for v1):

| Dataset (v26.03 path) | Contents | Volume |
|---|---|---|
| `output/target/` | Target metadata, proteinIds | ~50 MB |
| `output/disease/disease.parquet` | EFO disease ontology (single file since v26.03) | ~7 MB |
| `output/association_overall_direct/` | Gene-disease evidence scores | ~500 MB |
| `output/clinical_target/clinical_target.parquet` | Drug-target-disease triples (replaces removed `knownDrugsAggregated`) | ~3 MB |

> **v26.03 layout change**: prior versions used `output/etl/parquet/{dataset}/`. From v26.03 the path is `output/{dataset}/` directly. `disease` and `clinical_target` are now single Parquet files rather than partitioned directories.

### What we pull

From `targets/`:

| Column | Example | Used for |
|---|---|---|
| `id` | `ENSG00000254647` | Ensembl gene ID, primary key in OT |
| `approvedSymbol` | `INS` | HGNC symbol |
| `approvedName` | `insulin` | |
| `proteinIds[type=uniprot_swissprot].id` | `P01308` | **Join key back to UniProt** |

From `association_overall_direct/` (v26.03 name; was `associationByOverallDirect/`):

| Column | Example | Used for |
|---|---|---|
| `targetId` | `ENSG00000254647` | Gene |
| `diseaseId` | `EFO_0001359` | Disease (EFO ontology) |
| `associationScore` | `0.87` | Composite confidence, 0–1 (was `score` before v26.03) |

From `disease/disease.parquet` (v26.03; was partitioned `diseases/`):

| Column | Example | Used for |
|---|---|---|
| `id` | `EFO_0001359` | Primary key |
| `name` | `type 1 diabetes mellitus` | Display name |

> **v26.03**: `therapeuticAreas` was removed from this table. Therapeutic area hierarchy is now derivable from the `parents` column, which the dbt staging layer resolves.

From `clinical_target/clinical_target.parquet` (v26.03; replaces removed `knownDrugsAggregated/`):

| Column | Example | Used for |
|---|---|---|
| `drugId` | `CHEMBL1201631` | ChEMBL drug ID |
| `targetId` | `ENSG00000254647` | Target gene |
| `diseases` | `["EFO_0001359"]` | List of disease indications |
| `maxClinicalStage` | `4` | Highest trial phase (4 = approved) |

> **v26.03**: `knownDrugsAggregated` was removed. `clinical_target` carries the drug-target-disease triples. Drug display name (`prefName`) and mechanism of action are in separate `drug_molecule/` and `drug_mechanism_of_action/` datasets and are joined in the dbt Silver layer.

### How it joins

Open Targets uses **Ensembl gene ID** (`ENSG…`) as its target key. So:

```
Open Targets row:    ENSG00000254647  ←  disease  ←  drug
                            │
   resolved via OT's  proteinIds[type=uniprot_swissprot].id  
                            │
                            ▼
                       P01308 (INS)  ←  joins to dim_protein
```

For every target row, OT *already includes* a `proteinIds` list. You don't need to call UniProt's id-mapping service — the OT file itself contains the UniProt accession. This is the cleanest cross-reference in the whole manifest.

### License, cadence, volume

- **License**: **CC0** — public domain, no attribution required. The most permissive license possible. This is one of the reasons Open Targets is so dominant in biotech data engineering.
- **Refresh cadence**: Quarterly major releases. **Refresh quarterly** to stay current; the project's data freshness story is "as fresh as Open Targets."
- **Volume**: Filtered for our 5 fields, ~150 MB Parquet, ~30 MB after filtering to human + relevant evidence.

### Gotchas

- **Multiple ENSG per UniProt accession is rare but possible** (gene duplications, pseudogenes). Use the canonical one OT marks as `isApproved=true`.
- **EFO disease IDs are hierarchical.** `EFO_0001359` (type 1 diabetes) has parents like `EFO_0000400` (diabetes mellitus). For display, use the most specific term; for filtering, you may want to walk the parent chain.
- **The `association_overall_direct` vs `association_by_datasource_direct` distinction**: `overall` aggregates across all evidence sources, `datasource` keeps each source separate. For the story card, `overall` is what you want.
- **`ot_targets_raw` contains all species** (78,691 rows), not just human. The dbt staging layer filters to human proteins by joining on `uniprot_accession` from the UniProt Bronze asset.
- **`ot_associations_raw` is large** (4.5M rows, all species/disease combinations). After filtering to the ~20k human proteins, the working set shrinks substantially.
- **Drug names are not in `clinical_target`**. Join `ot_drugs_raw` with `drug_molecule/` on `drugId` in the Silver layer to get display names.

---

## Source 5 — ChEMBL

### What it is

ChEMBL is EMBL-EBI's database of bioactive molecules with drug-like properties. It contains ~2.4M compounds, ~20M activity measurements, and ~15,000 protein targets. Where Open Targets says "this drug targets this protein," ChEMBL says **"this drug binds this protein with IC50 = 2 nM, measured in this assay, published in this paper."**

### Why we need it

Two reasons. **First**, for the deeper story-card slot showing affinity ranges (the heatmap I sketched in earlier mockups for kinases × inhibitors). **Second**, ChEMBL is the source of truth for drug *chemistry* — SMILES strings, molecular weight, modality (small molecule vs antibody) — which you'll want if you ever extend the project to molecular visualizations.

For v1 you can defer ChEMBL and rely on Open Targets's `knownDrugsAggregated/`. **For v2 (the part of the project that demonstrates "I can also handle quantitative bioactivity data"), bring ChEMBL in.**

### How to get it

Three options:

- **Full SQLite dump** (~25 GB): [https://chembl.gitbook.io/chembl-interface-documentation/downloads](https://chembl.gitbook.io/chembl-interface-documentation/downloads). Heaviest but lets you query offline.
- **REST API**: [https://www.ebi.ac.uk/chembl/api/data/](https://www.ebi.ac.uk/chembl/api/data/). Best for targeted queries (e.g., "give me all activities for UniProt P01308").
- **MCP server (the connector available in this session)**: `mcp__plugin_bio-research_chembl__*` tools — `compound_search`, `target_search`, `get_bioactivity`, `get_mechanism`, `drug_search`, `get_admet`. Fastest for ad-hoc exploration during development.

For the bulk load, use the REST API filtered to the ~100 curated proteins; that's ~100 calls, no SQL dump needed.

### What we pull

For each curated UniProt protein, call the API endpoint `target/uniprot/{accession}` then `activity?target_chembl_id={id}&pchembl_value__gte=6`:

| Column | Example | Used for |
|---|---|---|
| `molecule_chembl_id` | `CHEMBL941` | Drug ID (e.g., imatinib) |
| `target_chembl_id` | `CHEMBL1862` | Target ID |
| `standard_type` | `IC50` | Type of measurement |
| `standard_value` | `2` | The number |
| `standard_units` | `nM` | Units |
| `pchembl_value` | `8.7` | -log10 of activity in molar; comparable across IC50/Ki/EC50 |
| `assay_description` | `Inhibition of human ABL1 kinase` | Context |

### How it joins

ChEMBL has a direct UniProt accession column on its `target_components` table:

```
ChEMBL target:    CHEMBL1862 (ABL1 kinase)
                       │
   via component_sequences.accession
                       │
                       ▼
                  P00519 (ABL1)  ←  joins to dim_protein
```

The MCP `target_search` tool and the REST API both surface this mapping automatically.

### License, cadence, volume

- **License**: CC-BY-SA 3.0. Attribution required; share-alike if you republish the raw data. Display-through-UI is fine.
- **Refresh cadence**: Major releases every ~6 months (currently v34). **Refresh annually.**
- **Volume**: full dump is ~25 GB, but for our use (top-100 proteins, pChEMBL ≥ 6) the slice is ~20 MB.

### Gotchas

- **Many measurement types and units**: IC50, Ki, EC50, Kd, in nM, μM, % inhibition, etc. Always filter by `pchembl_value IS NOT NULL` to get the standardized, comparable values.
- **Multiple assays per drug-target pair** are normal. Aggregate by `MEDIAN(pchembl_value)`, not `MIN` or `MAX` (which both have biased outlier behavior).
- **Approved drugs have `max_phase = 4`**, clinical-trial drugs `1-3`, preclinical `0.5`. For the story card, filter to `max_phase >= 4`.

---

## A worked example — INS (insulin) through all five sources

To make this concrete, here's what flows in for insulin specifically. This is the trace a reviewer can follow when reading your code.

| Field on the insulin story card | Source | Exact column/path | Value |
|---|---|---|---|
| Name | UniProt | `proteinDescription.recommendedName.fullName.value` | `Insulin` |
| Accession | UniProt | `primaryAccession` | `P01308` |
| Gene | UniProt | `genes[0].geneName.value` | `INS` |
| Length | UniProt | `sequence.length` | `110` |
| Sequence (for ESM-2) | UniProt | `sequence.value` | `MALWMRLLPLLALLALWGPDPAAAFV…` |
| Family pill | UniProt | `dbReferences[type=Pfam].id` | `PF00049 (Ins)` |
| Tagline (manual) | n/a — hand-written | curation list | "the hormone that tells your body what to do with sugar" |
| Narrative (manual) | n/a — hand-written | story card author | 3–5 sentence paragraph |
| Made in | HPA | `proteinatlas.tsv` → `Tissue expression` | `Tissue enhanced (pancreas)` |
| Subcellular | HPA | `proteinatlas.tsv` → `Subcellular location` | `Vesicles, Golgi apparatus` |
| Top partner 1 | STRING | `protein.links` filtered to `combined_score ≥ 700`, top by score | `INSR (0.99)` |
| Top partner 2 | STRING | same | `IGF1 (0.96)` |
| Top partner 3 | STRING | same | `SLC2A4 (0.94)` |
| When broken — Type 1 diabetes | Open Targets | `associationByOverall` filtered to `INS` | `EFO_0001359, score 0.92` |
| When broken — Type 2 diabetes | Open Targets | same | `EFO_0001360, score 0.88` |
| Drug — Humulin | Open Targets | `knownDrugsAggregated` filtered to `INS`, `phase=4` | `CHEMBL1201631` |
| Drug bioactivity (v2) | ChEMBL | `activity` endpoint, `target=CHEMBL1850`, `pchembl_value ≥ 6` | IC50 / Ki rows |

Every value on the card has a traceable provenance. That's the test of a working manifest.

---

## What's deliberately not in the manifest

- **DisGeNET** — superseded by Open Targets, which already includes DisGeNET's evidence with a cleaner CC0 license. One less source to wrangle.
- **OMIM** — the gold standard for monogenic-disease curation but commercially licensed. Open Targets aggregates the freely-redistributable subset.
- **Reactome / KEGG pathways** — pathway membership is interesting but doesn't fit the story-card layout. Add in v3 if at all.
- **AlphaFold structures** — 3D structures are visually impressive but require a viewer (Mol*, NGL) that's a separate engineering project. Out of scope.
- **PubMed abstracts** — literature linking is rich but is its own enormous data engineering problem (NER, entity resolution, ranking). The story card's narrative paragraph is hand-written or LLM-generated, not literature-extracted.
- **GTEx** — tissue expression at the RNA level. HPA covers this slot more cleanly for our use case.

If a reviewer asks "why didn't you include X?", these are the answers. Knowing what you didn't include is as important as knowing what you did.

---

## The minimum warehouse schema this implies

Once you have the five sources, the warehouse table design is straightforward. **One dimension table for the protein**, one each for diseases and drugs, and a few fact tables for the many-to-many joins.

```sql
-- The anchor: one row per protein, joined to UniProt
CREATE TABLE dim_protein (
    uniprot_accession   VARCHAR PRIMARY KEY,    -- e.g. P01308
    gene_symbol         VARCHAR,                -- INS
    protein_name        VARCHAR,                -- Insulin
    sequence_length     INTEGER,                -- 110
    sequence            TEXT,                   -- amino acid string
    pfam_id             VARCHAR,                -- PF00049
    function_raw        TEXT,                   -- UniProt FUNCTION text
    function_friendly   TEXT,                   -- LLM-rewritten or hand-written
    tagline             VARCHAR,                -- short subtitle on card
    is_curated          BOOLEAN,                -- TRUE for the 100
    ensembl_gene_id     VARCHAR,                -- for joining Open Targets
    string_protein_id   VARCHAR,                -- for joining STRING
    chembl_target_id    VARCHAR,                -- for joining ChEMBL
    updated_at          TIMESTAMP
);

-- Tissue expression from HPA (many tissues per protein)
CREATE TABLE fact_protein_tissue (
    uniprot_accession   VARCHAR,                -- FK to dim_protein
    tissue              VARCHAR,                -- "pancreas"
    expression_level    VARCHAR                 -- "High", "Medium", "Low", "Not detected"
);

-- Interactions from STRING
CREATE TABLE fact_interaction (
    uniprot_a           VARCHAR,                -- FK to dim_protein
    uniprot_b           VARCHAR,                -- FK to dim_protein
    combined_score      INTEGER                 -- 0-1000
);

-- Diseases (dimension), one row per EFO term
CREATE TABLE dim_disease (
    efo_id              VARCHAR PRIMARY KEY,    -- EFO_0001359
    disease_name        VARCHAR,                -- "type 1 diabetes mellitus"
    therapeutic_area    VARCHAR                 -- "Endocrine system disease"
);

-- Protein-disease association from Open Targets
CREATE TABLE fact_protein_disease (
    uniprot_accession   VARCHAR,                -- FK
    efo_id              VARCHAR,                -- FK
    overall_score       NUMERIC                 -- 0-1
);

-- Drugs (dimension)
CREATE TABLE dim_drug (
    chembl_id           VARCHAR PRIMARY KEY,    -- CHEMBL1201631
    drug_name           VARCHAR,                -- "Humulin"
    max_phase           SMALLINT,               -- 4
    modality            VARCHAR                 -- "Small molecule", "Antibody", etc.
);

-- Drug-target-disease triples from Open Targets
CREATE TABLE fact_drug_target_disease (
    chembl_id           VARCHAR,                -- FK
    uniprot_accession   VARCHAR,                -- FK
    efo_id              VARCHAR,                -- FK
    mechanism_of_action VARCHAR
);

-- Bioactivity (v2 only, from ChEMBL)
CREATE TABLE fact_bioactivity (
    chembl_id           VARCHAR,                -- FK
    uniprot_accession   VARCHAR,                -- FK
    standard_type       VARCHAR,                -- "IC50"
    pchembl_value       NUMERIC                 -- -log10 molar
);

-- The ML output: ESM-2 embeddings + UMAP coords
CREATE TABLE fact_embedding (
    uniprot_accession   VARCHAR PRIMARY KEY,
    embedding           FLOAT[],                -- 1280-dim ESM-2 vector
    umap_x              NUMERIC,
    umap_y              NUMERIC,
    model_version       VARCHAR,                -- "esm2_t33_650M_UR50D"
    generated_at        TIMESTAMP
);
```

This is a textbook **star schema**: `dim_protein` and `dim_disease` and `dim_drug` at the center, fact tables linking them. dbt models it cleanly. DuckDB / MotherDuck queries it fast. Every story card is **one query**: pick a `uniprot_accession`, fan out via the fact tables, return.

---

## Refresh strategy

| Source | Cadence | Strategy |
|---|---|---|
| UniProt | Quarterly | Full reload — only ~30 MB |
| STRING | Once (it ships v12 only every ~2 years) | Full reload on new major version |
| Human Protein Atlas | Annual | Full reload |
| Open Targets | Quarterly | Full reload of the four datasets we use |
| ChEMBL | Annual (v2 only) | Full reload of filtered slice |
| Embeddings (ESM-2) | On protein add | Incremental — only embed new sequences |

**No incremental ETL is required for v1.** Every source is small enough that "full reload" is faster and simpler than incremental diff logic. This is a deliberate simplification; you can add incremental loads later if the data grows. Right now, the entire stack fits in a few hundred MB.

---

## License summary

All five sources are **free for portfolio use**. Three are CC-BY (attribution), one is CC-BY-SA (share-alike, only matters if you republish raw data), one is CC0 (no restrictions). Attribution lives in the source footer of every story card and on the project README.

```
Sources: UniProt (CC-BY 4.0) · STRING-DB (CC-BY 4.0) ·
         Human Protein Atlas (CC-BY-SA 3.0) · Open Targets (CC0) ·
         ChEMBL (CC-BY-SA 3.0)
```

No source requires API keys for the bulk-download path used by this project.

---

## Working notes

- Store every raw source download in `cloudflare-r2://atlas-raw/{source}/v{version}/` so old versions are reproducible. Never overwrite.
- Each source's ingest is a separate Dagster asset. The asset materializes Parquet in R2 (`cloudflare-r2://atlas-bronze/{source}/`).
- dbt transforms Bronze → Silver → Gold. Gold = the seven tables above. The story-card API reads Gold only.
- The schema lives in MotherDuck. Embeddings additionally live in Qdrant (because vector search wants a vector index, not a column scan).
- Run all joins through the `uniprot_accession` column. Resist the temptation to join on gene symbol; symbols change over time, accessions do not.
