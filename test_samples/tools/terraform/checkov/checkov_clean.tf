# Clean Terraform sample: declares no scannable resources, so Checkov reports
# no misconfigurations. Used by the checkov integration tests.

output "noop" {
  value = "ok"
}
