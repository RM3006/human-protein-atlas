# ROADMAP.md — Protein Atlas

v1 is complete (all 9 parts shipped). See `ARCHITECTURE.md` for design rationale and `MEMORY.md` for build decisions.

---

## What shipped

| Part | Deliverable |
|---|---|
| 1 | Repo skeleton, OpenTofu R2 bucket, UniProt ingest (20,431 rows), CI (ruff + pyright + pytest) |
| 2 | STRING, HPA, Open Targets ingest (4 datasets), per-source network-mocked tests |
| 3 | dbt star schema (dim_protein, dim_disease, dim_drug, 4 fact tables), story-card SQL |
| 4 | ESM-2 t33_650M embeddings via Modal A10G, UMAP 2D projection, Qdrant `proteins` collection |
| 5 | Claude Haiku LLM rewrites (~17k proteins), 100 hand-authored editorial proteins, two-tier COALESCE in dim_protein |
| 6 | Streamlit UI (atlas scatter, story card, interactome graph, sequence neighbors), deployed on Community Cloud |
| 7 | Guided 90-second tour, insight cards, empty/loading/error states on every surface |
| 8 | `fact_protein_aa_composition` mart + dbt unit test, per-protein amino acid composition tab |
| 9 | README, ARCHITECTURE.md, public deploy, keep-alive |

---

## Beyond v1 — extensions ranked by value-per-effort

1. **ChEMBL bioactivity + kinase × inhibitor affinity heatmap** (~1 part). Quantitative drug-binding data — the highest-value addition not yet in the atlas.
2. **Drug–target–disease network tab** (~2 parts). Knowledge graph as a secondary surface.
3. **Pathway overlays from Reactome** (~1 part). Color the atlas by pathway membership.
4. **AlphaFold structure embedded in the story card** (~half a part). Visual upgrade for famous proteins.
5. **Variant overlay** — famous mutations + masked-marginal pathogenicity scores (~1 part).
