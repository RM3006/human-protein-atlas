# UI Color Theme — `apps/ui/app.py`

The visual language is split into two non-overlapping layers: **editorial chrome** (structure, layout, typography) stays strictly monochrome; **semantic colors** are applied only to data values and are imported from `render.py`.

---

## Layer 1 — Editorial Chrome (monochrome)

These four values carry every structural element: backgrounds, borders, dividers, labels, body text, and interactive controls.

| Role | HEX | RGB |
|---|---|---|
| Canvas / card / sidebar background | `#ffffff` | `rgb(255, 255, 255)` |
| Borders, dividers, progress-bar track, dropdown frame, list rules | `#e6e6e6` | `rgb(230, 230, 230)` |
| Muted labels, captions, metric labels, tab text (inactive), secondary text | `#888888` | `rgb(136, 136, 136)` |
| Primary ink — headings, body text, metric values, active tab underline, primary button fill | `#111111` | `rgb(17, 17, 17)` |

### Derived / one-off chrome shades

| Role | HEX | RGB |
|---|---|---|
| Family-group pill background; primary button hover state | `#333333` | `rgb(51, 51, 51)` |
| Secondary button hover background (very light tint over white) | `#f5f5f5` | `rgb(245, 245, 245)` |
| Composition category-legend text (slightly softer than `#888888`) | `#555555` | `rgb(85, 85, 85)` |
| Button hover shorthand (equivalent to `#111111`) | `#000` | `rgb(0, 0, 0)` |
| Neighbor-ring fill in atlas scatter (fully transparent) | `rgba(0,0,0,0)` | transparent |
| Neighbor-ring stroke in atlas scatter | `#000000` | `rgb(0, 0, 0)` |

---

## Layer 2 — Tour Chrome (warm tint)

The guided-tour card uses a warm palette to visually distinguish tutorial chrome from the neutral protein-data chrome above. No other element uses these colors.

| Role | HEX | RGB |
|---|---|---|
| Tour card background | `#fdf6e3` | `rgb(253, 246, 227)` |
| Tour card border | `#ecdfb8` | `rgb(236, 223, 184)` |
| Tour nav button text (Back / Next / Finish / Exit) | `#8a6d1f` | `rgb(138, 109, 31)` |
| Tour nav button text — hover state | `#a3821f` | `rgb(163, 130, 31)` |

---

## Layer 3 — Semantic Colors (defined in `render.py`)

These are **not** defined in `app.py`; they are imported as `render.PROTEIN_COLOR`, `render.DISEASE_COLOR`, `render.DRUG_COLOR`, and used only on data values — never on chrome elements.

| Semantic role | Constant | Applied to |
|---|---|---|
| Protein / interaction partner | `render.PROTEIN_COLOR` (slate-blue) | Interactome partner nodes, partner-table confidence bar, gene-symbol eyebrow text |
| Disease / pathology | `render.DISEASE_COLOR` (crimson) | Disease nodes in graph, disease evidence bar fill, drug pill border |
| Drug / therapeutic | `render.DRUG_COLOR` (emerald) | Drug nodes in graph, drug pill border and phase label |
| Strength scale (grey → violet) | `render.strength_color(t)` | All spoke lines in the interactome graph (shared scale across entity types) |
| Amino acid category palette | `render.category_color(cat)` | Stacked category bar and per-row composition bars (5-color side-chain palette, scoped to the composition tab) |

---

## Design Rules (from module docstring)

- **Chrome never takes a semantic hue.** Borders, backgrounds, labels, and controls are always from Layer 1/2.
- **Data values never take a chrome hue.** Protein, disease, and drug nodes/bars always use their semantic color.
- The amino acid composition tab is the sole exception: it uses its own 5-color side-chain-category palette, scoped to that tab only.
