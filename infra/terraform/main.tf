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
    bucket_key_enabled = false
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_versioning" "releases" {
  bucket = aws_s3_bucket.releases.id
  versioning_configuration { status = "Enabled" }
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
    # Beanstalk validates this platform bucket action even after bootstrap.
    # No other bucket can be created and no platform bucket objects can be read.
    { Effect = "Allow", Action = ["s3:CreateBucket"], Resource = "arn:aws:s3:::elasticbeanstalk-${var.region}-${local.account}" },
    { Effect = "Allow", Action = ["elasticbeanstalk:DescribeEnvironments", "elasticbeanstalk:DescribeEvents", "elasticbeanstalk:DescribeApplicationVersions", "elasticbeanstalk:DescribeEnvironmentHealth"], Resource = "*" },
    { Effect = "Allow", Action = ["elasticbeanstalk:CreateApplicationVersion"], Resource = [local.app_arn, "arn:aws:elasticbeanstalk:${var.region}:${local.account}:applicationversion/${var.name}/*"] },
    { Effect = "Allow", Action = ["elasticbeanstalk:UpdateEnvironment"], Resource = local.env_arn },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.releases.arn}/releases/*" },
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.releases.arn },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages"], Resource = aws_ecr_repository.app.arn }
  ] })
}
