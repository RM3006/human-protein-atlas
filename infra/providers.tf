# The AWS provider points at the account's R2 S3 endpoint. R2 is not real AWS,
# so every AWS-specific preflight (STS, IMDS, region/credential validation) is
# disabled, and S3 requests use path-style addressing.
provider "aws" {
  access_key = var.r2_access_key_id
  secret_key = var.r2_secret_access_key
  region     = "auto"

  skip_credentials_validation = true
  skip_region_validation      = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  s3_use_path_style           = true

  endpoints {
    s3 = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
  }
}
