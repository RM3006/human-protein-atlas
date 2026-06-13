# ROADMAP.md — Protein Atlas Build Plan

## How to read this document

Eight parts, sequential. Each part depends on the previous — do not parallelize.

Each part has:

- **Goal** — the one-sentence outcome.
- **Effort** — rough hour estimate. Could be one focused day or two weeks of evenings.
- **Deliverables** — concrete artifacts that exist when the part is done.
- **Tasks** — the work, in order.
- **Exit criteria** — checkable conditions that mean the part is done.
- **Risks** — where this part typically gets stuck.

Pace yourself by **exit criteria, not the calendar**. If you have a free weekend, two parts in two days is fine. If life happens, a part stretching across two weeks is fine.

**Working pattern**: one focused session per task, starting from clean context. Standard opening prompt: *"We are on Part N. Read `CLAUDE.md` and the relevant section of `ROADMAP.md`. The task is X."*

---

## Part 0 — Pre-flight

Before building:

1. Move `protein_atlas_curation_list.md` and `protein_atlas_data_source_manifest.md` into a new `docs/` subfolder.
2. Create accounts and grab tokens: Cloudflare R2, MotherDuck, Modal, Qdrant Cloud, Anthropic (for the LLM rewrites in Part 5).
3. Install `uv`, `git`, OpenTofu locally.
4. Create the GitHub repo (private at first; flip to public in Part 8).
5. Read `CLAUDE.md` end to end. The rules there are non-negotiable.

---

## Part 1 — Foundation + UniProt ingest

**Goal**: the repo skeleton is up, infrastructure is provisioned by code, and UniProt data flows from the internet into object storage.

**Deliverables**
- Repo laid out per `CLAUDE.md`.
- OpenTofu modules provisioning the Cloudflare R2 bucket (`atlas-raw`).
- Dagster project under `pipelines/` with one materializing asset: `uniprot_human_reviewed_raw`.
- `pyproject.toml` with locked dependencies via `uv`.
- `.env.example` template; gitignored `.env.local` for real secrets.
- GitHub Actions CI running `ruff`, `pyright`, `pytest` on every PR.

**Tasks**
1. `git init`, commit existing docs, push.
2. Bootstrap directory structure from `CLAUDE.md`. `uv init` + locked dependencies.
3. Write the OpenTofu module in `infra/` for the R2 bucket and credentials. Apply.
4. `dagster project scaffold` under `pipelines/`. Wire up the R2 resource.
5. Write `uniprot_human_reviewed_raw`: paginates UniProt REST (`reviewed:true AND organism_id:9606`), stores Parquet in `r2://atlas-raw/uniprot/v{release}/`.
6. One test mocking the HTTP layer, confirming parsing produces the expected schema.
7. GitHub Actions: lint + type-check + test on every PR.

**Exit criteria**
- `dagster asset materialize uniprot_human_reviewed_raw` succeeds.
- R2 contains ~20,000 records.
- `ruff`, `pyright`, `pytest` all pass locally and in CI.

**Risks**
- OpenTofu + Cloudflare R2 has a learning curve; token scoping is fiddly. Budget 3–4 hours for this alone.
- UniProt's REST paginates with cursors — don't parallelize blindly; rate-limit handling matters.

---

## Part 2 — Remaining data sources ingested

**Goal**: raw data from all four other public sources lives in R2.

**Deliverables**
- Dagster assets for STRING (with UniProt ID mapping resolved), HPA, and Open Targets (4 datasets: targets, diseases, associationByOverall, knownDrugsAggregated).
- ChEMBL **deferred to v2** — do not implement.
- All raw data under `r2://atlas-raw/{source}/v{version}/`.
- A shared `pipelines/resources/r2.py` used by every ingest.
- Per-source tests mocking the network layer.

**Tasks**
1. STRING: download `9606.protein.links.v12.0.txt.gz` and `9606.protein.aliases.v12.0.txt.gz`. Resolve ENSP → UniProt via aliases (filter `source = Ensembl_UniProt`). Filter to `combined_score >= 700`. Store as Parquet.
2. HPA: download `proteinatlas.tsv`. Parse to Parquet.
3. Open Targets: download the four Parquet datasets directly. Each lands under its own R2 subpath with version.
4. Each ingest gets a fixture-based test.
5. End-to-end materialize; verify row counts against the manifest.

**Exit criteria**
- Five raw-layer assets materialize cleanly.
- `SELECT COUNT(*)` queries against R2 match expected counts.
- The Dagster UI shows assets with their dependency edges.

**Risks**
- **STRING's ID mapping is the #1 source of confusion in this project.** Write `resolve_string_ids` as a dedicated, tested function before integrating it into the asset.
- Open Targets files are large; stream them.

---

## Part 3 — dbt modeling: Bronze → Silver → Gold

**Goal**: the warehouse star schema from the manifest is built and tested in MotherDuck.

**Deliverables**
- `models/` dbt project with sources, staging, and marts.
- Seven tables populated: `dim_protein`, `dim_disease`, `dim_drug`, `fact_protein_tissue`, `fact_interaction`, `fact_protein_disease`, `fact_drug_target_disease`.
- dbt tests on every join: `unique`, `not_null`, `relationships`.
- One canonical SQL query (`models/queries/protein_story_card.sql`) returning a full story-card row for any UniProt accession.

**Tasks**
1. `dbt init` with `dbt-duckdb`, pointing at MotherDuck.
2. `models/sources.yml` over the Bronze Parquet in R2.
3. Build staging models: one per source.
4. Build the seven mart tables.
5. Add dbt tests on every PK and FK.
6. Write `protein_story_card.sql`.

**Exit criteria**
- `dbt run` and `dbt test` both pass.
- Story-card query for `P01308` (insulin) returns a fully populated row.
- Same query for a random long-tail accession returns a row with some nulls but no failures.

**Risks**
- MotherDuck free-tier memory limits. If a join blows up, push aggregations earlier in the pipeline.

---

## Part 4 — ESM-2 inference + UMAP + Qdrant

**Goal**: every protein has a 1280-dim embedding, a 2D UMAP position, and is searchable by similarity.

**Deliverables**
- Modal function running ESM-2 `t33_650M` on an A10G GPU; returns mean-pooled embeddings.
- Dagster asset batching all ~20,000 sequences through Modal.
- UMAP projection over the full embedding matrix.
- Qdrant collection populated with vectors + UniProt accession payload.
- `fact_embedding` table in MotherDuck with `embedding`, `umap_x`, `umap_y`, `model_version`.

**Tasks**
1. Set up Modal: install, authenticate, configure `Secret`s.
2. Write the Modal `App` with a GPU image (PyTorch + transformers + esm).
3. Implement inference: list of sequences → list of 1280-dim vectors.
4. Batch the 20k sequences in chunks of ~256.
5. Compute UMAP (`n_neighbors=15`, `min_dist=0.1`).
6. Write vectors to MotherDuck and Qdrant.
7. Sanity check: EGFR (`P00533`) → top-5 neighbors should include ERBB2, ERBB3, ERBB4.

**Exit criteria**
- Every `dim_protein` row has a corresponding `fact_embedding` row with non-null UMAP coords.
- Qdrant contains ~20,000 vectors.
- EGFR neighbor sanity check passes.
- Total Modal spend < $10.

**Risks**
- Modal cold-start latency; use `concurrency_limit` and warm-up.
- Long sequences (>1022 aa) need truncation; record which proteins were truncated.

---

## Part 5 — LLM rewrites + top-100 narrative authoring

**Goal**: every protein has a plain-English description; the top 100 have hand-written 3–5 sentence narratives.

**Deliverables**
- Batch script using Claude Haiku to rewrite UniProt `FUNCTION` text into `function_friendly` for all ~20,000 proteins.
- Hand-written narratives for the top 100, sourced from `docs/protein_atlas_curation_list.md`.
- `tagline` populated for every protein.
- Spot-check report sampling 20 random LLM rewrites for quality.

**Tasks**
1. Design and iterate the rewrite prompt. Strict rule: do not invent claims not in the source. Test on 10 known proteins.
2. Run batch over all 20k. Cost: ~$10 with Haiku.
3. Author the 100 hand-written narratives. Block ~5 focused hours, ideally two sessions of ~50 proteins.
4. Upsert into `dim_protein`. Set `is_curated = TRUE` for the top 100.
5. Spot-check 20 random rewrites against the source; rate 1–5.

**Exit criteria**
- Every `dim_protein` row has non-null `function_friendly` and `tagline`.
- `SELECT COUNT(*) FROM dim_protein WHERE is_curated = TRUE` returns exactly 100.
- ≥17/20 spot-checked rewrites rated 4 or 5.

**Risks**
- The 100 narratives are real writing work. Block dedicated writing time; don't interleave with coding.
- LLM can hallucinate medical claims. Prompt must explicitly forbid invention.

---

## Part 6 — Streamlit UI (vertical slice)

**Goal**: end-to-end working app. Search a protein, see the atlas highlight it, see the full story card, click neighbors.

**Architecture deviation from the original plan**: this part was originally scoped as a FastAPI service on Modal sitting between Streamlit and the data stores. Built instead: Streamlit queries MotherDuck (DuckDB) and Qdrant **directly** via `apps/ui/data.py` — for a single-tenant analytical dashboard, a separate API tier added a deployment unit and a network hop with no other consumer. See `MEMORY.md` for the full rationale.

**Deliverables**
- `apps/ui/data.py` — typed MotherDuck + Qdrant query functions (story card, search, atlas, neighbors), ported from `models/queries/protein_story_card.sql`.
- Streamlit app on Streamlit Community Cloud with:
  - Header + search bar
  - UMAP atlas (Plotly WebGL `scattergl`, colored by family)
  - Story-card panel updating on click
  - Nearest-neighbors table
  - Clickable cross-references on the story card: each interaction partner and each drug target is a link that loads that protein's card.

**Ligand → receptor → drug routing (design rule — do not deviate).** A drug attaches to the protein it acts *on* (the molecular target), never to a ligand. So a hormone/ligand like insulin (INS, `P01308`) correctly has **no drugs of its own** — its analogs (Glargine, Degludec, …) sit on the insulin **receptor** INSR (`P06213`). The UI must **not** synthesize a drug list for a ligand: deriving one from STRING interaction edges drags in biologically-wrong drugs (e.g. IGF1R cancer antibodies, tied at the same interaction score with no way to filter them) and joining via shared diseases yields the whole disease pharmacopeia, not the protein's drugs — both verified against live data; see `MEMORY.md`. Instead, the story card shows the receptor as a top interaction partner and makes it clickable, so the reader reaches the drugs by navigating **ligand → receptor → drugs**. No new data source, no invented links (CLAUDE.md rule 5). Each card still shows only the drugs whose molecular target *is* that protein, top few by clinical phase.

**Tasks**
1. Port `models/queries/protein_story_card.sql` plus search/atlas/neighbor queries into typed `apps/ui/data.py` functions over a MotherDuck connection and a Qdrant client.
2. Build the Streamlit UI in `apps/ui/`. Use `st.plotly_chart` + `st.session_state` for selection. Render interaction partners and drug targets as clickable cross-references that load the selected accession's card (this is the ligand → receptor → drug navigation path — see the design rule above; never fabricate a ligand's drug list).
3. Deploy to Streamlit Community Cloud with MotherDuck and Qdrant credentials as secrets.

**Exit criteria**
- Public Streamlit URL works in incognito.
- Typing "insulin" highlights INS on the atlas and renders its story card.
- Clicking a neighbor in the table updates the card without page reload.
- Insulin (`P01308`) shows **no direct drugs**, but its top interaction partner INSR (`P06213`) is a working link whose card lists the insulin analogs — confirming the ligand → receptor → drug navigation path.

**Risks**
- 20k points in Plotly without `scattergl` will be slow.
- Streamlit Community Cloud has cold-start delays — acknowledge with a loading state.

---

## Part 7 — Polish: tour, amino acids, design pass

**Goal**: the project looks designed, not assembled.

**Deliverables**
- A guided 90-second tour: 4 narrated steps highlighting anchor proteins (Rhodopsin → EGFR → TP53 → an empty zone). Stateful sequence in `st.session_state`.
- "Amino acid alphabet" side tab with 20 cards (one-sentence description + deficiency note where applicable).
- "Reading this chart" blue insight card at the top of the atlas tab.
- Empty / loading / error states on every surface.
- Source attribution footer.
- Favicon, page title, 1200×630 `og:image` for sharing.

**Tasks**
1. Write tour content; implement as stateful sequence.
2. Author the 20 amino-acid cards in one focused writing session.
3. Add the insight card; polish all panel headers.
4. Implement empty / loading / error states.
5. Generate the `og:image` from the running atlas.

**Exit criteria**
- A non-technical friend can use the app for 5 minutes without confusion.
- Tour runs end-to-end smoothly.
- All error states have human-readable messages.

**Risks**
- Polish is bottomless. Ship at the 15-hour mark regardless of perfection.

---

## Part 8 — Documentation, deploy, portfolio integration

**Goal**: ship publicly with documentation a senior reviewer respects.

**Deliverables**
- `README.md` with project description, hero screenshot, Mermaid architecture diagram, live URL, "how it works" section, tech stack table, license.
- `ARCHITECTURE.md` fully written: section per layer with "used / considered / why."
- Public Streamlit URL stable for ≥48 hours under demo load.
- Showcase card on personal portfolio linking to live URL and GitHub.
- ~300-word LinkedIn or Twitter writeup with a screenshot.

**Tasks**
1. Write `README.md` with the Mermaid diagram.
2. Write `ARCHITECTURE.md` layer by layer.
3. Take and lightly edit the hero screenshot.
4. Add showcase card to portfolio.
5. Publish the LinkedIn post.

**Exit criteria**
- Live URL works on a fresh incognito browser.
- README rendered on GitHub looks polished.
- Portfolio site links to the project.
- LinkedIn post is live.

**Risks**
- Writing `ARCHITECTURE.md`, don't underestimate.

---

## Out of scope for v1

Do **not** build these in Parts 1–8:

- ChEMBL integration + affinity heatmap (v2).
- Drug–target–disease knowledge graph view (v2).
- Pathway data (Reactome / KEGG).
- AlphaFold structure viewer.
- User accounts, saved searches.
- Multi-species support.
- Variant pathogenicity prediction.

Note these in the README as "where this project goes next" — that itself signals senior judgment.

---

## Checkpoints (don't skip)

After each part, verify the corresponding condition before starting the next part.

| After part | Verify |
|---|---|
| 1 | CI runs lint + types + tests on every PR. |
| 2 | All 5 raw sources in R2; row counts match the manifest. |
| 3 | Canonical story-card SQL returns a complete row for insulin. |
| 4 | EGFR's top-5 nearest neighbors include ERBB2/3/4. |
| 5 | Spot-check rated ≥17/20. |
| 6 | Vertical slice works end-to-end on a fresh incognito browser. |
| 7 | A non-technical person used it without confusion. |
| 8 | README impresses on first read (test with 2 friends). |

If a checkpoint fails, **do not skip ahead**. Fix it first.

---

## Beyond v1 — extensions ranked by value-per-effort

1. **ChEMBL bioactivity + kinase × inhibitor affinity heatmap** (~1 part). Quantitative drug-binding data.
2. **Drug–target–disease network tab** (~2 parts). Knowledge graph as a secondary surface.
3. **Pathway overlays from Reactome** (~1 part). Color the atlas by pathway membership.
4. **AlphaFold structure embedded in the story card** (~half a part). Visual upgrade for famous proteins.
5. **Variant overlay** — famous mutations + masked-marginal pathogenicity scores (~1 part). Brings the original "variant impact" idea back, done right.

Pick one v2 thread, ship it, then pick the next. Resist scope creep within v1.
