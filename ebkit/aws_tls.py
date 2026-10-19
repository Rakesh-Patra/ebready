"""Use native certificate trust for AWS connections in the CLI application."""
import boto3
import botocore.httpsession
import truststore


def configure_aws_tls(ctx):
    # Respect operator-provided bundles, including the selected AWS profile.
    if boto3.Session()._session.get_config_variable("ca_bundle"):
        return
    # Botocore holds its own SSLContext reference. Change only its context
    # factory rather than replacing ssl globally for unrelated libraries.
    previous = botocore.httpsession.SSLContext
    botocore.httpsession.SSLContext = truststore.SSLContext
    ctx.call_on_close(lambda: setattr(botocore.httpsession, "SSLContext", previous))
