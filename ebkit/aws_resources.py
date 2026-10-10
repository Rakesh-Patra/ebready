"""Read Cluster metadata even when the installed SDK predates Cluster Mode."""
import json
import subprocess


def environment_resources(eb, environment, region):
    resources = eb.describe_environment_resources(EnvironmentName=environment).get("EnvironmentResources", {})
    model = getattr(getattr(eb, "meta", None), "service_model", None)
    if model is not None:
        shape = model.operation_model("DescribeEnvironmentResources").output_shape.members["EnvironmentResources"]
        if isinstance(shape.members, dict) and "Cluster" not in shape.members:
            command = ["aws", "elasticbeanstalk", "describe-environment-resources", "--environment-name", environment,
                       "--region", region, "--output", "json", "--no-cli-pager"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
            if result.returncode:
                raise RuntimeError("AWS CLI could not read Cluster metadata. Check CLI version and resource-read permissions.")
            resources = json.loads(result.stdout).get("EnvironmentResources", {})
    return resources
