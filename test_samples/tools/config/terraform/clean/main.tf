variable "name" {
  type    = string
  default = "example"
}

locals {
  greeting = "Hello, ${var.name}"
}

output "greeting" {
  value = local.greeting
}
