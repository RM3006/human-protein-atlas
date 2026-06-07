-- dim_protein is sourced from a single UniProt Swiss-Prot human-reviewed export
-- (currently 20,431 rows) that grows by low hundreds per release. A row count
-- far below that signals a broken ingest (wrong species filter, truncated
-- download, broken pagination) rather than organic drift — exactly the kind of
-- "wrong-but-present" failure that stayed green through the STRING resolver bug
-- this session (see MEMORY.md). The floor is set well below current volume so
-- it tolerates years of normal UniProt change while still catching a collapse.
-- Fails (returns a row) if the count drops below 15,000.

SELECT COUNT(*) AS row_count
FROM {{ ref('dim_protein') }}
HAVING COUNT(*) < 15000
