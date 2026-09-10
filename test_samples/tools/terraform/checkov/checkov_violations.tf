# Terraform sample with seeded security misconfigurations for Checkov.
# This file intentionally violates several CKV_AWS policies: an S3 bucket with
# no encryption, versioning, logging or public-access block, and a security
# group open to 0.0.0.0/0 on every port. Used by the Checkov integration test —
# do not "fix" these resources.

resource "aws_s3_bucket" "example" {
  bucket = "my-insecure-bucket"
}

resource "aws_security_group" "allow_all" {
  name = "allow_all"

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
