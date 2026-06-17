# SETUP.md — Prerequisites for the Protein Atlas Build

This checklist enumerates every account, token, and local tool required before Part 1 begins. Most steps are free; the only real costs are an estimated ~$10 in Anthropic API spend during Part 5 and ~$5–10 in Modal GPU spend during Part 4. Everything else fits in free tiers.

On completion, the following nine secret values exist in a gitignored `.env.local`:

```
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_R2_ACCESS_KEY_ID=...
CLOUDFLARE_R2_SECRET_ACCESS_KEY=...
MOTHERDUCK_TOKEN=...
MODAL_TOKEN_ID=...
MODAL_TOKEN_SECRET=...
QDRANT_URL=...
QDRANT_API_KEY=...
ANTHROPIC_API_KEY=...
```

---

## Phase A — Tools running locally

Tools installed on the development machine. One-time install.

### A1. Git
- **Why**: version control; the project starts with `git init`.
- **Used in**: every Part.
- **Install**: `winget install Git.Git` (Windows) / `brew install git` (macOS) / the relevant package manager (Linux).
- **Verify**: `git --version` returns a version.

### A2. Python 3.11+
- **Why**: the project's language. Modal and Dagster both require ≥3.11.
- **Used in**: every Part.
- **Install**: `winget install Python.Python.3.12` (Windows) / `brew install python@3.12` (macOS) / pyenv recommended on Linux.
- **Verify**: `python --version` returns 3.11.x or higher.

### A3. uv
- **Why**: the project's Python package manager. Replaces pip + virtualenv + pip-tools; roughly 10× faster.
- **Used in**: every Part.
- **Install**: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows) / `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS / Linux).
- **Verify**: `uv --version` returns a version.

### A4. OpenTofu
- **Why**: provisions the Cloudflare R2 bucket via Infrastructure-as-Code in Part 1. Open-source fork of Terraform; identical `.tf` syntax.
- **Used in**: Part 1 (provisioning); occasional updates later.
- **Install**: `winget install OpenTofu.OpenTofu` (Windows) / `brew install opentofu` (macOS) / OpenTofu's install script on Linux.
- **Verify**: `tofu version` returns a version.

### A5. Claude Code
- **Why**: the agentic CLI used to build the project, session by session.
- **Used in**: every Part.
- **Install**: `npm install -g @anthropic-ai/claude-code` (requires Node 18+).
- **First-run setup**: `claude` inside the project folder triggers browser authentication.
- **Verify**: `claude --version` returns a version.

---

## Phase B — Code hosting

### B1. GitHub
- **Why**: code repository; CI runs on push.
- **Used in**: every Part. The public link goes on the portfolio in Part 9.
- **Cost**: free.
- **Steps**:
  1. Sign-in or registration at github.com.
  2. Create a new repository (e.g. `protein-atlas`), **private** initially; flip to public in Part 9.
  3. Do **not** initialize with a README, .gitignore, or license — the local repo will push as the source of truth.
- **Note**: no secret required in `.env.local`. Git uses local credentials.

---

## Phase C — Storage and warehouse

### C1. Cloudflare R2 — raw data + Parquet object storage
- **Why**: holds every Bronze-layer Parquet file from UniProt, STRING, HPA, and Open Targets. Zero egress fees, which matters because Modal reads these files during inference.
- **Used in**: Parts 1, 2, 3, 4 (every ingest writes here; dbt reads from here; Modal reads from here).
- **Cost**: free up to 10 GB storage and 10M reads/month. The project uses ~1 GB total.
- **Catch**: Cloudflare requires a payment method on file even for free-tier R2. No charge unless usage exceeds the limit.
- **Steps**:
  1. Sign-in or registration at cloudflare.com.
  2. From the dashboard left sidebar, activate **R2**.
  3. A payment method must be added when prompted (no charge unless usage exceeds free tier).
  4. **My Profile → API Tokens → Create Token → R2 → "Edit"** scope. Restrict to the account; allow object reads + writes.
  5. Retain the **Access Key ID**, **Secret Access Key**, and the **Account ID** (visible in the R2 sidebar URL).
- **Secrets**: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_R2_ACCESS_KEY_ID`, `CLOUDFLARE_R2_SECRET_ACCESS_KEY`.

### C2. MotherDuck — the warehouse
- **Why**: cloud-hosted DuckDB. Holds the modeled Silver/Gold tables (`dim_protein`, `dim_disease`, `dim_drug`, the fact tables). The API queries this in production.
- **Used in**: Parts 3, 4, 5, 6 (dbt builds here; the API reads from here).
- **Cost**: free up to 10 GB. The project uses well under.
- **Steps**:
  1. Registration at app.motherduck.com (Google or GitHub OAuth — no separate password).
  2. The `atlas` database is created automatically on first `dbt run` (no manual step needed).
  3. **Settings → Tokens → Generate a service token.** Retain the value.
- **Secrets**: `MOTHERDUCK_TOKEN`.
- **Running dbt (Part 3+)**: `cd models && dbt run --profiles-dir . && dbt test --profiles-dir .`

---

## Phase D — Compute

### D1. Modal — serverless GPU for ESM-2 inference
- **Why**: runs ESM-2 on an A10G GPU for the embedding batch (Part 4). No idle cluster costs.
- **Used in**: Part 4.
- **Cost**: $30/month in free credits. Full embedding run ≈ $5–10.
- **Steps**:
  1. Registration at modal.com.
  2. A payment method must be added (required, but free credits cover the project).
  3. The CLI installs automatically with the project's Python dependencies. Pre-install via `uv tool install modal` is also possible.
  4. Authentication: `modal token new` (opens a browser tab).
- **Secrets**: `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` (written by `modal token new` to `~/.modal/`).

### D2. Qdrant Cloud — vector database
- **Why**: similarity search over the 20k × 1280-dim ESM-2 embeddings. Streamlit queries Qdrant directly for "find proteins like this one" functionality.
- **Used in**: Parts 4 (load vectors) and 6 (query vectors).
- **Cost**: free for 1 cluster, 1 GB storage. The project fits comfortably.
- **Steps**:
  1. Registration at cloud.qdrant.io.
  2. Create a free cluster (e.g. `atlas`).
  3. Retain the **cluster URL** and generate an **API key**.
- **Secrets**: `QDRANT_URL`, `QDRANT_API_KEY`.

---

## Phase E — LLM batch rewrites

### E1. Anthropic API
- **Why**: Claude Haiku rewrites UniProt's biologist-flavored `FUNCTION` text into plain English for the ~20,000 long-tail proteins. This is what makes the auto-generated story cards readable.
- **Used in**: Part 5 (one-time batch).
- **Cost**: roughly **$10** for the full batch with Haiku. A budget cap acts as a safety net.
- **Steps**:
  1. Registration at console.anthropic.com.
  2. A payment method must be added.
  3. **Settings → Limits → set a monthly spend cap** (recommended: $25 — protects against runaway costs).
  4. **API Keys → Create Key.** Retain the value.
- **Secrets**: `ANTHROPIC_API_KEY`.

---

## Phase F — Public hosting

### F1. Streamlit Community Cloud
- **Why**: free public hosting for the Streamlit UI. One-click deploy from the GitHub repo.
- **Used in**: Parts 6 and 7 (deploying and iterating on the UI).
- **Cost**: free for public apps.
- **Steps**:
  1. Sign-in at share.streamlit.io via GitHub OAuth — no separate signup.
  2. At Part 6, configure it to point at the repo's `apps/ui/app.py`. Dependencies are
     installed from `apps/ui/requirements.txt` (a minimal subset of `pyproject.toml`,
     scoped to what the UI imports), not the repo-root `pyproject.toml`.
- **Secrets**: configured in the Streamlit Cloud UI, not in `.env.local`. `MOTHERDUCK_TOKEN`, `QDRANT_URL`, `QDRANT_API_KEY`, and any UI tokens are added there.

### F2. Keep-alive — two GitHub Actions workflows

Streamlit Community Cloud sleeps an app after ~7 days of no browser sessions. Plain
HTTP pings (e.g. cron-job.org hitting `/healthz`) do not prevent sleep — Streamlit
tracks WebSocket connections, not HTTP health checks. Two workflows handle this with no
external accounts needed:

**`.github/workflows/keep-app-alive.yml`** (every 10 hours)
Loads the full app URL in headless Chromium via Playwright, establishing the WebSocket
session that Streamlit counts as activity. No git changes.

**`.github/workflows/repo-heartbeat.yml`** (checks daily, commits only when needed)
Runs daily but reads `git log` to check days elapsed since the last commit. Only pushes
an empty `[skip ci]` commit when that count reaches 59 — no hardcoded dates, git history
is the clock. Organic pushes (your own code) reset the counter naturally, so in an active
period the workflow runs silently every day and never commits.

- **Cost**: free (GitHub Actions free tier; no external accounts required).
- **No manual setup**: both workflows are already committed and will activate on the
  next scheduled fire after the repo goes public.

---

## Public data sources — no accounts required

The actual data inputs. All public, all bulk-downloadable, no signup:

| Source | What it provides | Download location |
|---|---|---|
| UniProt | Reviewed human proteins (sequences, function text, IDs) | `ftp.uniprot.org` + REST API at `rest.uniprot.org` |
| STRING-DB | Protein-protein interactions | `stringdb-downloads.org` |
| Human Protein Atlas | Tissue + subcellular localization | `proteinatlas.org/about/download` |
| Open Targets | Diseases + drugs aggregated | `platform.opentargets.org/downloads` |

No accounts required for any of these. The ingest assets in Parts 1 and 2 fetch them directly.

---

## Out of scope

Tools and services deliberately excluded from the stack:

- **AWS / GCP / Azure** — the stack uses Cloudflare R2 + Modal + MotherDuck because they are cheaper and simpler at this scale.
- **Docker Desktop** — Modal builds images server-side. Local Docker is only useful for debugging Modal image builds (uncommon).
- **Snowflake / Databricks** — MotherDuck covers the warehouse need at far lower cost for this data volume.
- **Postgres or a managed relational DB** — no relational store needed; MotherDuck is the warehouse, Qdrant is the vector store.
- **Neo4j or any graph DB** — deferred to v2 if the knowledge-graph view is built.
- **Dagster Cloud** — explicitly avoided in favor of self-hosted OSS Dagster to dodge credit limits.

---

## Verification checklist

Part 1 can begin when **every** box is checked:

- [ ] `git --version`, `python --version`, `uv --version`, `tofu version`, `claude --version` all return a version.
- [ ] GitHub repo exists (private), no README initialized.
- [ ] `.env.local` exists in the project root and contains all nine secret values listed at the top of this document.
- [ ] `.env.local` is in `.gitignore` and not committed.
- [ ] Cloudflare R2 dashboard shows the activated R2 service.
- [ ] MotherDuck dashboard shows the `atlas` database.
- [ ] Modal dashboard shows an authenticated workspace.
- [ ] Qdrant Cloud dashboard shows the running `atlas` cluster.
- [ ] Anthropic console shows an API key and an active monthly budget cap.


When the checklist is green, Claude Code can be opened in the project folder using the standard opening prompt from `ROADMAP.md` Part 1.
