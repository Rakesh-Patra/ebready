variable "region" {
  type    = string
  default = "us-east-2"
}
variable "name" {
  type    = string
  default = "ebkit-dashboard-practice"
}
variable "repository" {
  type    = string
  default = "Rakesh-Patra/ebready"
}
variable "github_environment" {
  type    = string
  default = "production"
}
variable "solution_stack" {
  type        = string
  description = "A reviewed current Amazon Linux 2023 Docker platform returned by AWS."
}
variable "instance_type" {
  type    = string
  default = "t3.small"
}
variable "public_configuration" {
  type        = map(string)
  default     = {}
  description = "Public settings only. Never supply admin tokens, passwords or AWS access keys."
  validation {
    condition     = length(setsubtract(toset(keys(var.public_configuration)), toset(["INSFORGE_URL", "INSFORGE_ANON_KEY"]))) == 0
    error_message = "Only public InsForge URL and anonymous key may be supplied here."
  }
}
variable "secret_arns" {
  type        = map(string)
  default     = {}
  description = "Optional pre-existing Secrets Manager ARNs. Values are never read by Terraform. Unused for the public demo."
}
