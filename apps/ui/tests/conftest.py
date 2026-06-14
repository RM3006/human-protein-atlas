"""Test fixture: an in-memory DuckDB loaded with a tiny gold-table dataset.

Built around the ligand -> receptor -> drug case (CLAUDE.md / ROADMAP Part 6):
insulin (INS, P01308) has interaction partners and a disease but NO drug; the insulin
analog sits on the receptor INSR (P06213). overall_score is DECIMAL (as in the real
NUMERIC mart) so the Decimal -> float coercion in data.py is exercised.
"""

from collections.abc import Iterator

import duckdb
import pytest

_FIXTURE_SQL = """
CREATE TABLE dim_protein (
    uniprot_accession VARCHAR, gene_symbol VARCHAR, protein_name VARCHAR,
    sequence_length INTEGER, sequence VARCHAR, pfam_id VARCHAR, function_raw VARCHAR,
    function_friendly VARCHAR, tagline VARCHAR, is_curated BOOLEAN,
    protein_class VARCHAR, subcellular_location VARCHAR, family_group VARCHAR
);
INSERT INTO dim_protein VALUES
('P01308','INS','Insulin',110,'MALWMRLLPLLALLALWGPDPAAA','PF00049',
 'Insulin decreases blood glucose.',
 'Insulin is the hormone that manages blood sugar.','the blood-sugar hormone',TRUE,
 'Predicted secreted proteins','Secreted','Secreted'),
('P06213','INSR','Insulin receptor',1382,'MGTGTSHPAFLVLGCLLTGLSLILCQLSLP','PF07714',
 'Receptor tyrosine kinase.',
 'INSR is the docking station for insulin on cells.','insulin docking station',TRUE,
 'Enzymes','Plasma membrane','Enzymes'),
('P08069','IGF1R','Insulin-like growth factor 1 receptor',1367,
 'MKSGSGGGSPTSLWGLLFLSAALSLWPTS','PF07714',
 'Receptor tyrosine kinase.','IGF1R is a growth receptor.','growth receptor',FALSE,
 'Enzymes','Plasma membrane','Enzymes');

CREATE TABLE fact_protein_tissue (
    uniprot_accession VARCHAR, tissue VARCHAR, expression_level VARCHAR
);
INSERT INTO fact_protein_tissue VALUES
('P01308','Tissue enhanced (pancreas)','Detected in single');

CREATE TABLE fact_interaction (
    uniprot_a VARCHAR, uniprot_b VARCHAR, combined_score INTEGER
);
INSERT INTO fact_interaction VALUES
('P01308','P06213',999),
('P01308','P08069',990);

CREATE TABLE dim_disease (efo_id VARCHAR, disease_name VARCHAR);
INSERT INTO dim_disease VALUES ('EFO_0001359','type 1 diabetes mellitus');

-- overall_score is NUMERIC in the real mart -> DuckDB returns Python Decimal.
CREATE TABLE fact_protein_disease (
    uniprot_accession VARCHAR, efo_id VARCHAR, overall_score DECIMAL(5, 3)
);
INSERT INTO fact_protein_disease VALUES
('P01308','EFO_0001359',0.9),
('P06213','EFO_0001359',0.5);

CREATE TABLE dim_drug (chembl_id VARCHAR, drug_name VARCHAR, max_phase SMALLINT);
INSERT INTO dim_drug VALUES ('CHEMBL1201631','Insulin glargine',4);

CREATE TABLE fact_drug_target_disease (
    chembl_id VARCHAR, uniprot_accession VARCHAR, efo_id VARCHAR
);
INSERT INTO fact_drug_target_disease VALUES
('CHEMBL1201631','P06213','EFO_0001359');

CREATE TABLE fact_embedding (
    uniprot_accession VARCHAR, umap_x FLOAT, umap_y FLOAT
);
INSERT INTO fact_embedding VALUES
('P01308',1.0,2.0),('P06213',1.1,2.1),('P08069',1.2,2.2);
"""

# (code, name, three-letter code, category, produced_by_body), matching
# models/seeds/seed_amino_acids.csv order and side-chain categories.
_AA_CODES = (
    ("A", "Alanine", "Ala", "Nonpolar aliphatic", True),
    ("R", "Arginine", "Arg", "Positively charged", True),
    ("N", "Asparagine", "Asn", "Polar uncharged", True),
    ("D", "Aspartate", "Asp", "Negatively charged", True),
    ("C", "Cysteine", "Cys", "Polar uncharged", True),
    ("E", "Glutamate", "Glu", "Negatively charged", True),
    ("Q", "Glutamine", "Gln", "Polar uncharged", True),
    ("G", "Glycine", "Gly", "Nonpolar aliphatic", True),
    ("H", "Histidine", "His", "Positively charged", False),
    ("I", "Isoleucine", "Ile", "Nonpolar aliphatic", False),
    ("L", "Leucine", "Leu", "Nonpolar aliphatic", False),
    ("K", "Lysine", "Lys", "Positively charged", False),
    ("M", "Methionine", "Met", "Nonpolar aliphatic", False),
    ("F", "Phenylalanine", "Phe", "Aromatic", False),
    ("P", "Proline", "Pro", "Nonpolar aliphatic", True),
    ("S", "Serine", "Ser", "Polar uncharged", True),
    ("T", "Threonine", "Thr", "Polar uncharged", False),
    ("W", "Tryptophan", "Trp", "Aromatic", False),
    ("Y", "Tyrosine", "Tyr", "Aromatic", True),
    ("V", "Valine", "Val", "Nonpolar aliphatic", False),
)

_SEQUENCE_LENGTHS = {"P01308": 110, "P06213": 1382, "P08069": 1367}


def _seed_amino_acids_values() -> str:
    rows: list[str] = []
    for code, name, three, category, produced in _AA_CODES:
        produced_sql = "true" if produced else "false"
        deficiency = "NULL" if produced else f"'{name} deficiency note.'"
        rows.append(
            f"('{code}','{name}','{three}','{category}',{produced_sql},"
            f"'{name} side chain.',{deficiency})"
        )
    return ",\n".join(rows)


def _composition_values() -> str:
    """Synthetic (uniprot_accession, amino_acid_code, count, pct_of_sequence) rows.

    Percentages descend monotonically with the amino acid's position in _AA_CODES, so
    "sorted by pct DESC" is exercised the same way for every fixture protein. The values
    are not derived from real sequences; only their relative ordering matters here.
    """
    rows: list[str] = []
    for accession, length in _SEQUENCE_LENGTHS.items():
        for i, (code, _, _, _, _) in enumerate(_AA_CODES):
            pct = round(10.0 - 0.5 * i, 2)
            count = round(pct / 100 * length)
            rows.append(f"('{accession}','{code}',{count},{pct})")
    return ",\n".join(rows)


_AA_FIXTURE_SQL = f"""
CREATE TABLE seed_amino_acids (
    amino_acid_code VARCHAR, name VARCHAR, three_letter_code VARCHAR,
    category VARCHAR, produced_by_body BOOLEAN, description VARCHAR, deficiency_note VARCHAR
);
INSERT INTO seed_amino_acids VALUES
{_seed_amino_acids_values()};

CREATE TABLE fact_protein_aa_composition (
    uniprot_accession VARCHAR, amino_acid_code VARCHAR, "count" INTEGER, pct_of_sequence DOUBLE
);
INSERT INTO fact_protein_aa_composition VALUES
{_composition_values()};
"""


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(":memory:")
    connection.execute(_FIXTURE_SQL)
    connection.execute(_AA_FIXTURE_SQL)
    yield connection
    connection.close()
