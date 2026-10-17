import ssl
from unittest.mock import MagicMock

import botocore.httpsession
import click
import pytest
import truststore

from ebkit.aws_tls import configure_aws_tls


@pytest.mark.parametrize("bundle", [None, "C:/operator-ca.pem"])
def test_native_trust_keeps_verification_and_respects_bundle(monkeypatch, bundle):
    session = MagicMock()
    session._session.get_config_variable.return_value = bundle
    monkeypatch.setattr("ebkit.aws_tls.boto3.Session", lambda: session)
    original = botocore.httpsession.SSLContext
    with click.Context(click.Command("test")) as ctx:
        configure_aws_tls(ctx)
        assert botocore.httpsession.SSLContext is (original if bundle else truststore.SSLContext)
        context = botocore.httpsession.create_urllib3_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        # Botocore/urllib3 performs hostname matching itself.
        assert context.check_hostname is False
    assert botocore.httpsession.SSLContext is original
