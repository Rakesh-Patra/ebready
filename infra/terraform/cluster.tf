# The AWS provider's native environment resource does not support Tier=Cluster.
# CloudFormation accepts the new tier; Terraform owns this named stack and role.
data "aws_iam_role" "cluster" { name = "aws-elasticbeanstalk-eks-cluster-role" }
data "aws_iam_role" "node" { name = "aws-elasticbeanstalk-eks-node-role" }
data "aws_iam_role" "observability" { name = "aws-elasticbeanstalk-eks-observability-role" }
resource "aws_iam_role" "application" {
  name = "${var.name}-application"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "pods.eks.amazonaws.com" }, Action = ["sts:AssumeRole", "sts:TagSession"]
  }] })
}
resource "aws_iam_role_policy" "application" {
  name = "runtime-secrets-only"
  role = aws_iam_role.application.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"], Resource = values(var.secret_arns)
  }] })
}
variable "cluster_image_version" {
  type        = string
  description = "Pre-registered Cluster image version used only for bootstrap; CI manages subsequent versions."
}
variable "cluster_login_enabled" {
  type    = bool
  default = false
}
variable "cluster_web_origin" {
  type        = string
  default     = ""
  description = "Verified HTTPS environment origin allowed to submit authentication requests."
  validation {
    condition     = var.cluster_web_origin == "" || can(regex("^https://[a-z0-9.-]+$", var.cluster_web_origin))
    error_message = "Use an HTTPS origin without a path or trailing slash."
  }
}
locals {
  cluster_environment = "${var.name}-cluster"
  cluster_options = concat([
    { Namespace = "aws:elasticbeanstalk:eks", OptionName = "cluster-role", Value = data.aws_iam_role.cluster.arn },
    { Namespace = "aws:elasticbeanstalk:eks", OptionName = "node-role", Value = data.aws_iam_role.node.arn },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "observability-role", Value = data.aws_iam_role.observability.arn },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "application-role", Value = aws_iam_role.application.arn },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "service-port", Value = "8080" },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "arch", Value = "amd64" },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "cpu", Value = "250m" },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "memory", Value = "512Mi" },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "memory-limit", Value = "1Gi" },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "load-balancer-type", Value = "ALB" },
    { Namespace = "aws:elasticbeanstalk:eks:alb", OptionName = "scheme", Value = "internet-facing" },
    { Namespace = "aws:elasticbeanstalk:eks:alb", OptionName = "listen-ports", Value = jsonencode([{ HTTPS = 443 }, { HTTP = 80 }]) },
    { Namespace = "aws:elasticbeanstalk:eks:environment:autoscaling", OptionName = "min-replica", Value = "1" },
    { Namespace = "aws:elasticbeanstalk:eks:environment:autoscaling", OptionName = "max-replica", Value = "1" },
    { Namespace = "aws:elasticbeanstalk:eks:environment:deployment", OptionName = "strategy", Value = "Recreate" },
    { Namespace = "aws:elasticbeanstalk:eks:environment:readiness-probe", OptionName = "enabled", Value = "true" },
    { Namespace = "aws:elasticbeanstalk:eks:alb", OptionName = "healthcheck-path", Value = "/api/health" },
    { Namespace = "aws:elasticbeanstalk:eks:environment:readiness-probe", OptionName = "http-path", Value = "/api/health" },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "env-variables", Value = jsonencode(merge(var.public_configuration, {
      EBKIT_WEB_ORIGINS                = var.cluster_web_origin, EBKIT_PUBLIC_DEMO = tostring(!var.cluster_login_enabled), EBKIT_LIVE_ENABLED = tostring(var.cluster_live_enabled), EBKIT_GITHUB_AUTO_DEPLOY = "false", EBKIT_STATE_S3_BUCKET = aws_s3_bucket.releases.id, EBKIT_GLOBAL_CAPACITY = "1", EBKIT_HOSTED_REGION = var.region,
      AWS_REGION                       = var.region, AWS_DEFAULT_REGION = var.region, EBKIT_AWS_ACCOUNT_ID = local.account,
      EBKIT_BUILD_PERMISSIONS_BOUNDARY = aws_iam_policy.build_boundary.arn,
      EBKIT_PREPROVISIONED_ROLES       = jsonencode({ cluster_role = data.aws_iam_role.cluster.arn, node_role = data.aws_iam_role.node.arn, observability_role = data.aws_iam_role.observability.arn })
    })) },
    { Namespace = "aws:elasticbeanstalk:eks:environment", OptionName = "secrets", Value = jsonencode(var.secret_arns) }
  ])
}
resource "aws_cloudformation_stack" "cluster" {
  #checkov:skip=CKV_AWS_124:No notification recipient is configured; CI waits for stack completion and retains failures. SNS/paging integration remains a documented follow-up.
  name       = "${var.name}-cluster"
  parameters = { VersionLabel = var.cluster_image_version }
  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09"
    Parameters               = { VersionLabel = { Type = "String" } }
    Resources = { Environment = { Type = "AWS::ElasticBeanstalk::Environment", Properties = {
      ApplicationName = aws_elastic_beanstalk_application.app.name
      EnvironmentName = local.cluster_environment
      Tier            = { Name = "Cluster", Type = "EKS" }
      VersionLabel    = { Ref = "VersionLabel" }
      OptionSettings  = local.cluster_options
    } } }
  })
  timeouts {
    create = "60m"
    update = "40m"
    delete = "40m"
  }
  lifecycle { ignore_changes = [parameters["VersionLabel"]] }
  depends_on = [aws_iam_role_policy.application, aws_iam_role_policy.hosted_worker]
}
