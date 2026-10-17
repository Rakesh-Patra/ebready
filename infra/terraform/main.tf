data "aws_caller_identity" "current" {}
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}
locals {
  account               = data.aws_caller_identity.current.account_id
  app_arn               = "arn:aws:elasticbeanstalk:${var.region}:${local.account}:application/${var.name}"
  env_arn               = "arn:aws:elasticbeanstalk:${var.region}:${local.account}:environment/${var.name}/${local.cluster_environment}"
  github_subject_prefix = var.github_subject_prefix != "" ? var.github_subject_prefix : "repo:${var.repository}"
}

resource "aws_s3_bucket" "releases" {
  #checkov:skip=CKV_AWS_18:Practice release/state bucket has no separate access-log sink; S3 data-access auditing remains a documented production follow-up.
  #checkov:skip=CKV_AWS_144:Single-region practice deployment; cross-region recovery and its costs are outside this demo.
  #checkov:skip=CKV_AWS_145:AES256 SSE-S3 encryption is required below; this demo has no customer-managed KMS requirement.
  #checkov:skip=CKV2_AWS_62:Application delivery is triggered explicitly by CI; there is no bucket event-notification consumer.
  bucket        = "${var.name}-${local.account}-${var.region}-releases"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "releases" {
  bucket                  = aws_s3_bucket.releases.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id
  rule {
    bucket_key_enabled       = false
    blocked_encryption_types = ["SSE-C"]
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_versioning" "releases" {
  bucket = aws_s3_bucket.releases.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_lifecycle_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}
resource "aws_s3_bucket_policy" "releases" {
  bucket = aws_s3_bucket.releases.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.releases.arn, "${aws_s3_bucket.releases.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_ecr_repository" "app" {
  #checkov:skip=CKV_AWS_136:AES256 encryption is configured; a customer-managed KMS key is not required for this practice repository.
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

resource "aws_elastic_beanstalk_application" "app" {
  name        = var.name
  description = "EBKit public dashboard DevOps demonstration"
}
resource "aws_iam_role" "github" {
  name                 = "${var.name}-github-deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
    Action = "sts:AssumeRoleWithWebIdentity", Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "${local.github_subject_prefix}:environment:${var.github_environment}"
    } }
  }] })
}
resource "aws_iam_role_policy" "github" {
  name = "deploy-one-dashboard"
  role = aws_iam_role.github.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["cloudformation:UpdateStack", "cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents", "cloudformation:GetTemplate"], Resource = aws_cloudformation_stack.cluster.id },
    { Effect = "Allow", Action = ["elasticbeanstalk:ListTagsForResource", "elasticbeanstalk:DescribeEnvironments", "elasticbeanstalk:DescribeConfigurationSettings", "elasticbeanstalk:DescribeEnvironmentHealth", "elasticbeanstalk:UpdateEnvironment", "elasticbeanstalk:DescribeEvents"], Resource = local.env_arn },
    { Effect = "Allow", Action = ["elasticbeanstalk:DescribeApplicationVersions", "elasticbeanstalk:CreateApplicationVersion"], Resource = [local.app_arn, "arn:aws:elasticbeanstalk:${var.region}:${local.account}:applicationversion/${var.name}/*"] },
    { Effect = "Allow", Action = ["iam:GetRole"], Resource = [data.aws_iam_role.cluster.arn, data.aws_iam_role.node.arn, data.aws_iam_role.observability.arn, aws_iam_role.application.arn] },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [data.aws_iam_role.cluster.arn, data.aws_iam_role.node.arn, data.aws_iam_role.observability.arn, aws_iam_role.application.arn], Condition = { StringEquals = { "iam:PassedToService" = ["elasticbeanstalk.amazonaws.com", "eks.amazonaws.com", "ec2.amazonaws.com", "pods.eks.amazonaws.com"] } } },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages"], Resource = aws_ecr_repository.app.arn }
  ] })
}
