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
    sequence_length INTEGER, pfam_id VARCHAR, function_raw VARCHAR,
    function_friendly VARCHAR, tagline VARCHAR, is_curated BOOLEAN,
    protein_class VARCHAR, subcellular_location VARCHAR, family_group VARCHAR
);
INSERT INTO dim_protein VALUES
('P01308','INS','Insulin',110,'PF00049','Insulin decreases blood glucose.',
 'Insulin is the hormone that manages blood sugar.','the blood-sugar hormone',TRUE,
 'Predicted secreted proteins','Secreted','Secreted'),
('P06213','INSR','Insulin receptor',1382,'PF07714','Receptor tyrosine kinase.',
 'INSR is the docking station for insulin on cells.','insulin docking station',TRUE,
 'Enzymes','Plasma membrane','Enzymes'),
('P08069','IGF1R','Insulin-like growth factor 1 receptor',1367,'PF07714',
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


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(":memory:")
    connection.execute(_FIXTURE_SQL)
    yield connection
    connection.close()
