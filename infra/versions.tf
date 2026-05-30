terraform {
  required_version = ">= 1.8"

  required_providers {
    # R2 is S3-compatible, so the standard AWS provider manages the bucket using
    # the R2 S3 access keys -- no separate Cloudflare API token needed.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
