# The Bronze raw-data bucket. Every ingest asset writes Parquet here under
# {source}/v{version}/ (see docs/protein_atlas_data_source_manifest.md).
resource "aws_s3_bucket" "atlas_raw" {
  bucket = var.bucket_name
}
