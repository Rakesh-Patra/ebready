data "aws_caller_identity" "current" {}
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}
locals {
  account               = data.aws_caller_identity.current.account_id
  app_arn               = "arn:aws:elasticbeanstalk:${var.region}:${local.account}:application/${var.name}"
  env_arn               = "arn:aws:elasticbeanstalk:${var.region}:${local.account}:environment/${var.name}/${var.name}"
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

resource "aws_iam_role" "instance" {
  name = "${var.name}-ec2"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "web" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier"
}
resource "aws_iam_role_policy" "instance" {
  name = "release-and-image-read"
  role = aws_iam_role.instance.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "${aws_s3_bucket.releases.arn}/releases/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.releases.arn },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.app.arn },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"], Resource = "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/elasticbeanstalk/${var.name}/*" }
    ], length(var.secret_arns) == 0 ? [] : [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = values(var.secret_arns) }
  ]) })
}
resource "aws_iam_instance_profile" "app" {
  name = "${var.name}-ec2"
  role = aws_iam_role.instance.name
}
resource "aws_iam_role" "beanstalk" {
  name = "${var.name}-service"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "elasticbeanstalk.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "health" {
  role       = aws_iam_role.beanstalk.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSElasticBeanstalkEnhancedHealth"
}
resource "aws_elastic_beanstalk_application" "app" {
  name        = var.name
  description = "EBKit public dashboard DevOps demonstration"
}
resource "aws_elastic_beanstalk_environment" "app" {
  #checkov:skip=CKV_AWS_312:Checkov 3.3.29 checks HealthStreamingEnabled under the wrong namespace; SystemType enhanced below is the AWS enhanced-health setting.
  #checkov:skip=CKV_AWS_340:Practice platform versions are pinned and updated through reviewed Terraform; automatic managed updates require separately tested maintenance capacity and permissions.
  name                   = var.name
  application            = aws_elastic_beanstalk_application.app.name
  solution_stack_name    = var.solution_stack
  wait_for_ready_timeout = "30m"
  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "EnvironmentType"
    value     = "SingleInstance"
  }
  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "ServiceRole"
    value     = aws_iam_role.beanstalk.name
  }
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "IamInstanceProfile"
    value     = aws_iam_instance_profile.app.name
  }
  setting {
    namespace = "aws:ec2:instances"
    name      = "InstanceTypes"
    value     = var.instance_type
  }
  setting {
    namespace = "aws:elasticbeanstalk:healthreporting:system"
    name      = "SystemType"
    value     = "enhanced"
  }
  setting {
    namespace = "aws:elasticbeanstalk:cloudwatch:logs"
    name      = "StreamLogs"
    value     = "true"
  }
  setting {
    namespace = "aws:elasticbeanstalk:cloudwatch:logs"
    name      = "RetentionInDays"
    value     = "7"
  }
  setting {
    namespace = "aws:elasticbeanstalk:cloudwatch:logs"
    name      = "DeleteOnTerminate"
    value     = "true"
  }
  dynamic "setting" {
    for_each = merge(var.public_configuration, { EBKIT_PUBLIC_DEMO = "true", EBKIT_LIVE_ENABLED = "false", EBKIT_GITHUB_AUTO_DEPLOY = "false" })
    content {
      namespace = "aws:elasticbeanstalk:application:environment"
      name      = setting.key
      value     = setting.value
    }
  }
  dynamic "setting" {
    for_each = var.secret_arns
    content {
      namespace = "aws:elasticbeanstalk:application:environmentsecrets"
      name      = setting.key
      value     = setting.value
    }
  }
  lifecycle { ignore_changes = [version_label] }
  depends_on = [aws_iam_role_policy_attachment.web, aws_iam_role_policy.instance, aws_iam_role_policy_attachment.health]
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
    # Required Beanstalk platform-bucket configuration, scoped to one account/region.
    # No unrelated bucket access is granted; runtime objects belong to this environment.
    { Effect = "Allow", Action = ["s3:CreateBucket", "s3:ListBucket", "s3:GetBucket*", "s3:PutBucketPolicy", "s3:PutBucketOwnershipControls", "s3:PutBucketPublicAccessBlock"], Resource = "arn:aws:s3:::elasticbeanstalk-${var.region}-${local.account}" },
    { Effect = "Allow", Action = ["s3:Get*", "s3:Put*", "s3:DeleteObject"], Resource = "arn:aws:s3:::elasticbeanstalk-${var.region}-${local.account}/resources/environments/${aws_elastic_beanstalk_environment.app.id}/*" },
    # Observed in the successful operator deployment: Beanstalk also stages this
    # application's embedded extensions outside the per-environment prefix.
    { Effect = "Allow", Action = ["s3:Get*", "s3:Put*", "s3:DeleteObject"], Resource = "arn:aws:s3:::elasticbeanstalk-${var.region}-${local.account}/resources/_runtime/_embedded_extensions/${var.name}/*" },
    { Effect = "Allow", Action = ["cloudformation:Get*", "cloudformation:Describe*", "cloudformation:ListStackResources", "cloudformation:UpdateStack"], Resource = "arn:aws:cloudformation:${var.region}:${local.account}:stack/awseb-${aws_elastic_beanstalk_environment.app.id}-stack/*" },
    # These specific inventory APIs do not support per-environment resource scoping.
    { Effect = "Allow", Action = ["autoscaling:DescribeAutoScalingGroups", "autoscaling:DescribeLaunchConfigurations", "ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "ec2:DescribeLaunchTemplates", "ec2:DescribeLaunchTemplateVersions"], Resource = "*" },
    { Effect = "Allow", Action = ["autoscaling:SuspendProcesses", "autoscaling:ResumeProcesses"], Resource = "arn:aws:autoscaling:${var.region}:${local.account}:autoScalingGroup:*:autoScalingGroupName/awseb-${aws_elastic_beanstalk_environment.app.id}-stack-*" },
    { Effect = "Allow", Action = ["iam:GetRole"], Resource = [aws_iam_role.beanstalk.arn, aws_iam_role.instance.arn] },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.beanstalk.arn, aws_iam_role.instance.arn], Condition = { StringEquals = { "iam:PassedToService" = ["elasticbeanstalk.amazonaws.com", "ec2.amazonaws.com", "cloudformation.amazonaws.com", "autoscaling.amazonaws.com"] } } },
    { Effect = "Allow", Action = ["iam:GetInstanceProfile"], Resource = aws_iam_instance_profile.app.arn },
    { Effect = "Allow", Action = ["elasticbeanstalk:DescribeEnvironments", "elasticbeanstalk:DescribeConfigurationSettings", "elasticbeanstalk:DescribeInstancesHealth", "elasticbeanstalk:DescribeEnvironmentResources", "elasticbeanstalk:DescribeEnvironmentHealth"], Resource = local.env_arn },
    { Effect = "Allow", Action = ["elasticbeanstalk:DescribeApplicationVersions"], Resource = "arn:aws:elasticbeanstalk:${var.region}:${local.account}:applicationversion/${var.name}/*" },
    { Effect = "Allow", Action = ["elasticbeanstalk:DescribeEvents"], Resource = [local.app_arn, local.env_arn, "arn:aws:elasticbeanstalk:${var.region}:${local.account}:applicationversion/${var.name}/*"] },
    { Effect = "Allow", Action = ["elasticbeanstalk:CreateApplicationVersion"], Resource = [local.app_arn, "arn:aws:elasticbeanstalk:${var.region}:${local.account}:applicationversion/${var.name}/*"] },
    { Effect = "Allow", Action = ["elasticbeanstalk:UpdateEnvironment"], Resource = local.env_arn },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:Get*"], Resource = "${aws_s3_bucket.releases.arn}/releases/*" },
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucket*"], Resource = aws_s3_bucket.releases.arn },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages"], Resource = aws_ecr_repository.app.arn }
  ] })
}
