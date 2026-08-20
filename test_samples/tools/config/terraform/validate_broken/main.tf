variable "name" {
  type    = string
  default = "example"
}

output "broken" {
  value = local.does_not_exist
}
