locals {
  pod_namespace = "eb-${local.cluster_environment}"
}
resource "aws_cloudwatch_metric_alarm" "unhealthy" {
  alarm_name          = "${var.name}-cluster-no-ready-replicas"
  alarm_description   = "Cluster environment has fewer than one ready replica for three minutes."
  namespace           = "ElasticBeanstalk/Infrastructure"
  metric_name         = "EnvironmentReplicas"
  dimensions          = { namespace = local.pod_namespace }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"
}
resource "aws_cloudwatch_metric_alarm" "memory" {
  alarm_name          = "${var.name}-cluster-memory"
  alarm_description   = "Container working set exceeds 85 percent of its 1Gi limit."
  namespace           = "ElasticBeanstalk/Infrastructure"
  metric_name         = "container_memory_working_set_bytes"
  dimensions          = { namespace = local.pod_namespace }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 912680550
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
}
resource "aws_cloudwatch_dashboard" "app" {
  dashboard_name = var.name
  dashboard_body = jsonencode({ widgets = [
    { type = "text", x = 0, y = 0, width = 24, height = 2, properties = {
      markdown = "# EBKit HTTPS Cluster environment\nOne replica; ready replicas, container memory and CPU counters. Logs retained seven days. Alarm notifications are not configured."
    } },
    { type = "metric", x = 0, y = 2, width = 12, height = 6, properties = {
      title   = "Ready replicas", region = var.region, period = 60, stat = "Minimum",
      metrics = [["ElasticBeanstalk/Infrastructure", "EnvironmentReplicas", "namespace", local.pod_namespace]]
    } },
    { type = "metric", x = 12, y = 2, width = 12, height = 6, properties = {
      title   = "Container working-set memory (bytes)", region = var.region, period = 60, stat = "Maximum",
      metrics = [["ElasticBeanstalk/Infrastructure", "container_memory_working_set_bytes", "namespace", local.pod_namespace]]
    } },
    { type = "metric", x = 0, y = 8, width = 24, height = 6, properties = {
      title   = "Container CPU consumption (cumulative seconds)", region = var.region, period = 60, stat = "Maximum",
      metrics = [["ElasticBeanstalk/Infrastructure", "container_cpu_usage_seconds_total", "namespace", local.pod_namespace]]
    } }
  ] })
}
resource "aws_cloudwatch_query_definition" "errors" {
  name            = "${var.name}/application-errors"
  log_group_names = ["/aws/elasticbeanstalk/application/logs"]
  query_string    = "fields @timestamp, @logStream, @message | filter @logStream like /eb-${local.cluster_environment}\\./ | filter @message like /ERROR|Error|exception|Exception/ | sort @timestamp desc | limit 100"
}
output "cloudwatch_dashboard" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.app.dashboard_name}"
}

# Shared regional groups are retained if this application is destroyed.
resource "aws_cloudwatch_log_group" "cluster" {
  #checkov:skip=CKV_AWS_158:CloudWatch encrypts log data at rest; no customer-managed KMS requirement exists for this short-lived demo.
  #checkov:skip=CKV_AWS_338:Seven-day retention is deliberate for the hackathon's operational logs and cost limit, rather than a one-year audit archive.
  for_each          = toset(["/aws/elasticbeanstalk/application/logs", "/aws/elasticbeanstalk/application/metrics", "/aws/elasticbeanstalk/infrastructure/logs", "/aws/elasticbeanstalk/infrastructure/metrics"])
  name              = each.value
  retention_in_days = 7
  skip_destroy      = true
}
