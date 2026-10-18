variable "cluster_live_enabled" {
  type    = bool
  default = false
}
resource "aws_iam_policy" "build_boundary" {
  name        = "${var.name}-build-boundary"
  description = "Maximum permissions for untrusted customer-source builds; no IAM or secret access."
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload", "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart"], Resource = "arn:aws:ecr:${var.region}:${local.account}:repository/ebk-*" },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/codebuild/ebkit-ebk-*" },
    { Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:GetBucketAcl"], Resource = "arn:aws:s3:::ebkit-source-${local.account}-${var.region}-*" },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = "arn:aws:s3:::ebkit-source-${local.account}-${var.region}-*/sources/*" }
  ] })
}
resource "aws_iam_role_policy" "hosted_worker" {
  name = "scoped-customer-deployment"
  role = aws_iam_role.application.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.releases.arn}/worker-state/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.releases.arn, Condition = { StringLike = { "s3:prefix" = "worker-state/*" } } },
    { Effect = "Allow", Action = ["ec2:DescribeVpcs", "ec2:DescribeSubnets"], Resource = "*" },
    { Effect = "Allow", Action = ["s3:CreateBucket"], Resource = "arn:aws:s3:::elasticbeanstalk-${var.region}-${local.account}" },
    { Effect = "Allow", Action = ["iam:GetRole"], Resource = [data.aws_iam_role.cluster.arn, data.aws_iam_role.node.arn, data.aws_iam_role.observability.arn, "arn:aws:iam::${local.account}:role/ebkit-ebk-*-build-role"] },
    { Effect = "Allow", Action = ["iam:CreateRole", "iam:PutRolePolicy"], Resource = "arn:aws:iam::${local.account}:role/ebkit-ebk-*-build-role", Condition = { ArnEquals = { "iam:PermissionsBoundary" = aws_iam_policy.build_boundary.arn } } },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [data.aws_iam_role.cluster.arn, data.aws_iam_role.node.arn, data.aws_iam_role.observability.arn], Condition = { StringEquals = { "iam:PassedToService" = ["elasticbeanstalk.amazonaws.com", "eks.amazonaws.com", "ec2.amazonaws.com", "pods.eks.amazonaws.com"] } } },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = "arn:aws:iam::${local.account}:role/ebkit-ebk-*-build-role", Condition = { StringEquals = { "iam:PassedToService" = "codebuild.amazonaws.com" } } },
    { Effect = "Allow", Action = ["codebuild:CreateProject", "codebuild:UpdateProject", "codebuild:StartBuild", "codebuild:BatchGetProjects", "codebuild:ListBuildsForProject", "codebuild:DeleteProject"], Resource = "arn:aws:codebuild:${var.region}:${local.account}:project/ebkit-ebk-*" },
    { Effect = "Allow", Action = ["codebuild:BatchGetBuilds", "codebuild:StopBuild"], Resource = ["arn:aws:codebuild:${var.region}:${local.account}:build/ebkit-ebk-*", "arn:aws:codebuild:${var.region}:${local.account}:project/ebkit-ebk-*"] },
    { Effect = "Allow", Action = ["ecr:CreateRepository", "ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:ListTagsForResource", "ecr:TagResource", "ecr:DeleteRepository"], Resource = "arn:aws:ecr:${var.region}:${local.account}:repository/ebk-*" },
    { Effect = "Allow", Action = ["elasticbeanstalk:CreateApplication", "elasticbeanstalk:CreateApplicationVersion", "elasticbeanstalk:CreateEnvironment", "elasticbeanstalk:DescribeApplications", "elasticbeanstalk:DescribeApplicationVersions", "elasticbeanstalk:DescribeEnvironments", "elasticbeanstalk:DescribeEnvironmentResources", "elasticbeanstalk:DescribeEnvironmentHealth", "elasticbeanstalk:DescribeConfigurationSettings", "elasticbeanstalk:DescribeEvents", "elasticbeanstalk:ListTagsForResource", "elasticbeanstalk:UpdateTagsForResource", "elasticbeanstalk:UpdateEnvironment", "elasticbeanstalk:TerminateEnvironment", "elasticbeanstalk:DeleteApplication", "elasticbeanstalk:DeleteApplicationVersion"], Resource = ["arn:aws:elasticbeanstalk:${var.region}:${local.account}:application/ebk-*", "arn:aws:elasticbeanstalk:${var.region}:${local.account}:applicationversion/ebk-*/*", "arn:aws:elasticbeanstalk:${var.region}:${local.account}:environment/ebk-*/ebk-*-cluster"] },
    { Effect = "Allow", Action = ["s3:CreateBucket", "s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:PutBucketTagging", "s3:PutBucketPublicAccessBlock", "s3:PutEncryptionConfiguration", "s3:PutLifecycleConfiguration", "s3:DeleteBucket"], Resource = "arn:aws:s3:::ebkit-source-${local.account}-${var.region}-*" },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "arn:aws:s3:::ebkit-source-${local.account}-${var.region}-*/sources/*" },
    { Effect = "Allow", Action = ["logs:DescribeLogStreams"], Resource = [for category in ["application", "infrastructure"] : "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/elasticbeanstalk/${category}/*"] },
    { Effect = "Allow", Action = ["logs:DeleteLogStream"], Resource = "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/elasticbeanstalk/*:log-stream:eb-ebk-*" },
    { Effect = "Allow", Action = ["logs:DeleteLogGroup"], Resource = "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/codebuild/ebkit-ebk-*" }
  ] })
}
