output "github_role_arn" { value = aws_iam_role.github.arn }
output "artifact_bucket" { value = aws_s3_bucket.releases.id }
output "ecr_repository" { value = aws_ecr_repository.app.repository_url }
output "application_name" { value = aws_elastic_beanstalk_application.app.name }
output "environment_name" { value = local.cluster_environment }
output "cluster_stack" { value = aws_cloudformation_stack.cluster.name }

output "environment_url" { value = var.cluster_web_origin }
