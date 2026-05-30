output "bucket_name" {
  value       = aws_s3_bucket.atlas_raw.id
  description = "Name of the provisioned R2 raw-data bucket."
}

output "r2_s3_endpoint" {
  value       = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
  description = "S3-compatible endpoint for the R2 account."
}
