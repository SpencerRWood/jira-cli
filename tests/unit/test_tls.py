"""Real loopback TLS tests; no corporate connections or OS certificate changes."""

import socket
import ssl
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import trustme
import truststore
from pydantic import SecretStr

from jira_cli import tls
from jira_cli.cli import main
from jira_cli.client import JiraClient
from jira_cli.config import Config, Jira, UserError

HTTPX_CLIENT = httpx.Client
type Endpoint = tuple[Config, trustme.CA, list[str], str]
pytestmark = pytest.mark.usefixtures("application")


def test_default_uses_native_context_and_enforces_verification() -> None:
    context = tls.context()
    assert isinstance(context, truststore.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_client_keeps_proxy_settings_and_uses_native_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}
    previous = httpx.Client

    def capture(**kwargs: Any) -> httpx.Client:
        observed.update(kwargs)
        return previous(**kwargs)

    monkeypatch.setattr(httpx, "Client", capture)
    client = JiraClient(
        Config(jira=Jira(base_url="https://jira.example.test")),
        SecretStr("fixture-pat-never-print"),
    )
    client.close()
    assert observed["trust_env"] is True
    assert observed["follow_redirects"] is False
    assert isinstance(observed["verify"], truststore.SSLContext)


def test_explicit_file_overrides_directory_and_native_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca = trustme.CA()
    path = tmp_path / "approved fixture.pem"
    ca.cert_pem.write_to_path(path)
    monkeypatch.setenv("SSL_CERT_FILE", str(path))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "unused"))
    context = tls.context()
    assert not isinstance(context, truststore.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.cert_store_stats()["x509_ca"] == 1


def test_explicit_directory_uses_verified_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, Any] = {}
    original = ssl.create_default_context

    def capture(**kwargs: Any) -> ssl.SSLContext:
        observed.update(kwargs)
        return original(**kwargs)

    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    monkeypatch.setattr(ssl, "create_default_context", capture)
    context = tls.context()
    assert observed == {"capath": str(tmp_path)}
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("contents", [None, "not a certificate"])
def test_bad_explicit_override_fails_safely_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    contents: str | None,
) -> None:
    path = tmp_path / "fixture-pat-never-print.pem"
    if contents is not None:
        path.write_text(contents)
    monkeypatch.setenv("SSL_CERT_FILE", str(path))
    assert main(["auth", "test"]) == 1
    output = capsys.readouterr()
    assert "Cannot initialize TLS trust" in output.err
    assert "fixture-pat-never-print" not in output.err + output.out


def test_native_initialization_failure_does_not_disable_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_protocol: int) -> ssl.SSLContext:
        raise OSError("sensitive local details")

    monkeypatch.setattr(truststore, "SSLContext", fail)
    with pytest.raises(UserError, match="Certificate verification remains required"):
        tls.context()


@pytest.fixture
def endpoint(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Endpoint]:
    ca = trustme.CA()
    scenario = getattr(request, "param", "valid")
    identity = "wrong.example.test" if scenario == "wrong-host" else "127.0.0.1"
    if scenario == "expired":
        certificate = ca.issue_cert(
            identity,
            not_before=datetime.now(UTC) - timedelta(days=2),
            not_after=datetime.now(UTC) - timedelta(days=1),
        )
    else:
        certificate = ca.issue_cert(identity)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    certificate.configure_cert(server_context)
    received: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.append(self.path)
            assert self.headers["Authorization"] == "Bearer fixture-pat-never-print"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"name":"fixture-user"}')

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    # Override the suite's mock transport only for this explicit loopback server.
    monkeypatch.setattr(httpx, "Client", HTTPX_CLIENT)
    for variable in [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ]:
        monkeypatch.delenv(variable, raising=False)

    class QuietServer(ThreadingHTTPServer):
        def handle_error(
            self,
            _request: socket.socket | tuple[bytes, socket.socket],
            _client_address: Any,
        ) -> None:
            # Rejected handshake probes can close the connection before HTTP.
            pass

    with QuietServer(("127.0.0.1", 0), Handler) as server:
        server.socket = server_context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                Config(jira=Jira(base_url=f"https://127.0.0.1:{server.server_port}")),
                ca,
                received,
                scenario,
            )
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.mark.parametrize("endpoint", ["valid", "wrong-host", "expired"], indirect=True)
@pytest.mark.parametrize("source", ["native", "file"])
def test_real_verified_tls_handshake(
    endpoint: Endpoint,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, ca, received, scenario = endpoint
    if source == "native":
        original = tls.context

        def trusted_context() -> ssl.SSLContext:
            context = original()
            # Add a fixture CA to this connection only; never alter the OS store.
            ca.configure_trust(context)
            return context

        monkeypatch.setattr(tls, "context", trusted_context)
    else:
        path = tmp_path / "fixture.pem"
        ca.cert_pem.write_to_path(path)
        monkeypatch.setenv("SSL_CERT_FILE", str(path))
    client = JiraClient(config, SecretStr("fixture-pat-never-print"))
    try:
        if scenario == "valid":
            assert client.object("myself") == {"name": "fixture-user"}
            assert received == ["/rest/api/2/myself"]
        else:
            with pytest.raises(UserError, match="Jira connection failed"):
                client.object("myself")
            assert received == []
    finally:
        client.close()


def test_untrusted_native_certificate_is_rejected(
    endpoint: Endpoint,
) -> None:
    config, _, received, _ = endpoint
    client = JiraClient(config, SecretStr("fixture-pat-never-print"))
    try:
        with pytest.raises(UserError, match="Jira connection failed"):
            client.object("myself")
        assert received == []
    finally:
        client.close()
