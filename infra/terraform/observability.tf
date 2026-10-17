resource "aws_cloudwatch_metric_alarm" "health" {
  alarm_name          = "${var.name}-unhealthy"
  alarm_description   = "Enhanced Beanstalk health is Warning or worse. Inspect environment events and application logs."
  namespace           = "AWS/ElasticBeanstalk"
  metric_name         = "EnvironmentHealth"
  dimensions          = { EnvironmentName = aws_elastic_beanstalk_environment.app.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  threshold           = 15
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"
}
resource "aws_cloudwatch_metric_alarm" "cpu" {
  alarm_name          = "${var.name}-high-cpu"
  alarm_description   = "CPU exceeds 80 percent for three five minute periods on the demo instance."
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = one(aws_elastic_beanstalk_environment.app.instances) }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
}
resource "aws_cloudwatch_dashboard" "app" {
  dashboard_name = var.name
  dashboard_body = jsonencode({ widgets = [
    { type = "text", x = 0, y = 0, width = 24, height = 2, properties = {
      markdown = "# EBKit standard Docker environment\nHealth, CPU and network metrics. Logs are retained for seven days. Alarms have no notification subscriptions yet. Memory and traces are not instrumented."
    } },
    { type = "metric", x = 0, y = 2, width = 12, height = 6, properties = {
      title   = "Elastic Beanstalk enhanced health", region = var.region, period = 60, stat = "Maximum",
      metrics = [["AWS/ElasticBeanstalk", "EnvironmentHealth", "EnvironmentName", aws_elastic_beanstalk_environment.app.name]]
    } },
    { type = "metric", x = 12, y = 2, width = 12, height = 6, properties = {
      title   = "Instance CPU utilization", region = var.region, period = 300, stat = "Average",
      metrics = [["AWS/EC2", "CPUUtilization", "InstanceId", one(aws_elastic_beanstalk_environment.app.instances)]]
    } },
    { type = "metric", x = 0, y = 8, width = 24, height = 6, properties = {
      title   = "Instance network traffic", region = var.region, period = 300, stat = "Sum",
      metrics = [["AWS/EC2", "NetworkIn", "InstanceId", one(aws_elastic_beanstalk_environment.app.instances)], ["AWS/EC2", "NetworkOut", "InstanceId", one(aws_elastic_beanstalk_environment.app.instances)]]
    } }
  ] })
}
resource "aws_cloudwatch_query_definition" "errors" {
  name         = "${var.name}/application-errors"
  query_string = "fields @timestamp, @logStream, @message | filter @message like /ERROR|Error|exception|Exception/ | sort @timestamp desc | limit 100"
}
output "cloudwatch_dashboard" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.app.dashboard_name}"
}
