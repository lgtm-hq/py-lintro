variable  "region"   {
type = string
    default="us-east-1"
}

locals {
   endpoint    = "https://example.com/${var.region}"
}

output "endpoint" {
value=local.endpoint
}
