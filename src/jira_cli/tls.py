"""Verified HTTPS using managed system trust or an explicit CA override."""

import os
import ssl

import truststore

from jira_cli.config import UserError


def context() -> ssl.SSLContext:
    """Preserve HTTPX's file-before-directory environment override precedence."""
    try:
        if certificate_file := os.environ.get("SSL_CERT_FILE"):
            return ssl.create_default_context(cafile=certificate_file)
        if certificate_directory := os.environ.get("SSL_CERT_DIR"):
            return ssl.create_default_context(capath=certificate_directory)
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except (OSError, ValueError) as exc:
        raise UserError(
            "Cannot initialize TLS trust; check system certificates and "
            "SSL_CERT_FILE/SSL_CERT_DIR. Certificate verification remains required."
        ) from exc
