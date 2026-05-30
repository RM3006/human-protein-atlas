variable "cloudflare_account_id" {
  type        = string
  description = "Cloudflare account id; forms the R2 S3 endpoint host."
}

variable "r2_access_key_id" {
  type        = string
  description = "R2 S3 access key id (CLOUDFLARE_R2_ACCESS_KEY_ID)."
  sensitive   = true
}

variable "r2_secret_access_key" {
  type        = string
  description = "R2 S3 secret access key (CLOUDFLARE_R2_SECRET_ACCESS_KEY)."
  sensitive   = true
}

variable "bucket_name" {
  type        = string
  description = "Name of the raw-data bucket."
  default     = "atlas-raw"
}
