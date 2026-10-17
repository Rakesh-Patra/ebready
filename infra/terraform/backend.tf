terraform {
  # Configure the private bucket and environment-specific key at init time.
  # The deployment role is limited to releases/* and cannot read this state.
  backend "s3" {}
}
