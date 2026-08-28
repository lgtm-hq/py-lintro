# Terraform sample with seeded security misconfigurations for Checkov.
# This file intentionally violates several CKV_AWS policies (unencrypted and
# public S3 bucket, wide-open security group) and is used by the Checkov
# integration test. Do not "fix" these resources.

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
