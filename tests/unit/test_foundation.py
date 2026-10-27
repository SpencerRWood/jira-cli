import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import SecretStr

from jira_cli.cli import main
from jira_cli.client import JiraClient
from jira_cli.config import (
    Config,
    UserError,
    application_directory,
    base_url,
    issue_key,
    load_config,
    project_key,
    read_pat,
    set_config,
)
from jira_cli.context import ContextStore, Selection

pytestmark = pytest.mark.usefixtures("application")


def test_fixed_configuration_and_token_paths(
    application: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "unrelated repository"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("JIRA_PAT=wrong-cwd-token")
    (elsewhere / "config.yaml").write_text("jira: wrong")
    monkeypatch.chdir(elsewhere)
    assert application_directory() == application
    assert load_config(application).defaults.project == "WWRAP"
    assert read_pat(application).get_secret_value() == "fixture-pat-never-print"


def test_unix_directory_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert application_directory() == tmp_path / "jira-cli"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert application_directory() == Path.home() / ".config/jira-cli"


def test_process_overrides_do_not_enter_yaml(
    application: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://override.example.test")
    monkeypatch.setenv("JIRA_PROJECT", "OTHER")
    monkeypatch.setenv("JIRA_PAT", "process-token")
    assert read_pat(application).get_secret_value() == "process-token"
    assert load_config(application).jira.base_url == "https://override.example.test"
    set_config(application, "output.format", "json")
    data = yaml.safe_load((application / "config.yaml").read_text())
    assert data["defaults"]["project"] == "WWRAP"
    assert data["jira"]["base_url"] == "https://jira.example.test/jira"
    assert "process-token" not in (application / "config.yaml").read_text()


@pytest.mark.parametrize(
    "text",
    [
        "[broken",
        "jira: token",
        "secret: PAT",
        "jira:\n  auth:\n    token: SECRET",
        "output:\n  format: unknown",
    ],
)
def test_invalid_yaml_fails_without_showing_values(
    application: Path, text: str
) -> None:
    (application / "config.yaml").write_text(text)
    with pytest.raises(UserError, match=r"Cannot read config\.yaml"):
        load_config(application)


def test_missing_and_empty_config_use_single_document_defaults(
    application: Path,
) -> None:
    (application / "config.yaml").unlink()
    assert load_config(application).jira.deployment == "data_center"
    set_config(application, "defaults.project", "OTHER")
    assert load_config(application).defaults.project == "OTHER"
    (application / "config.yaml").write_text("")
    assert load_config(application).defaults.project == "WWRAP"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("missing.key", "true"),
        ("jira.missing", "abc"),
        ("jira", "abc"),
        ("context.scope", "bad"),
        ("reporting.aging_threshold_days", "0"),
        ("defaults.project", "bad project"),
    ],
)
def test_config_set_rejects_unsupported_values_atomically(
    application: Path, key: str, value: str
) -> None:
    before = (application / "config.yaml").read_bytes()
    with pytest.raises(UserError):
        set_config(application, key, value)
    assert (application / "config.yaml").read_bytes() == before


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://jira.example.test",
        "https://user:secret@jira.example.test",
        "https://jira.example.test?token=secret",
        "https://jira.example.test#secret",
    ],
)
def test_url_validation_keeps_transport_secure(value: str) -> None:
    config = Config()
    config.jira.base_url = value
    with pytest.raises(UserError, match="HTTPS"):
        base_url(config)


@pytest.mark.parametrize(
    "value", ["", "WWRAP", "../1", "WWRAP-0", "wwrap-1", "WWRAP-1/attachment"]
)
def test_issue_keys_cannot_be_paths(value: str) -> None:
    with pytest.raises(UserError):
        issue_key(value)


def test_project_keys_cannot_inject_jql() -> None:
    with pytest.raises(UserError):
        project_key('WWRAP" OR project = "OTHER')


@pytest.mark.parametrize(
    "text", ["", "JIRA_PAT=", "JIRA_PAT='two words'", "OTHER=value"]
)
def test_pat_must_exist_without_whitespace(application: Path, text: str) -> None:
    (application / ".env").write_text(text)
    with pytest.raises(UserError, match="Set JIRA_PAT"):
        read_pat(application)


def test_directory_global_and_explicit_context_precedence(
    application: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ContextStore(application, Config())
    assert store.selection().issue is None
    store.update(Selection(issue="WWRAP-1", project="WWRAP"), global_scope=True)
    assert store.issue(None) == "WWRAP-1"
    store.update(Selection(issue="WWRAP-2"))
    before = store.path.read_bytes()
    assert store.issue("WWRAP-3") == "WWRAP-3"
    assert store.issue(None) == "WWRAP-2"
    assert store.path.read_bytes() == before
    assert store.selection().project == "WWRAP"
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert store.issue(None) == "WWRAP-1"
    assert "fixture-pat-never-print" not in store.path.read_text()


def test_context_options_and_corruption(application: Path) -> None:
    config = Config()
    config.context.scope = "global"
    store = ContextStore(application, config)
    store.update(Selection(issue="WWRAP-1"))
    assert store.issue(None) == "WWRAP-1"
    config.context.scope = "directory"
    config.context.fallback_to_global = False
    with pytest.raises(UserError, match="No active issue"):
        store.issue(None)
    config.context.persist = False
    with pytest.raises(UserError, match="disabled"):
        store.update(Selection())
    store.path.write_text('{"global_context":{"issue":"bad"}}')
    with pytest.raises(UserError, match="Expected an issue key"):
        store.read()
    store.path.write_text("[broken")
    with pytest.raises(UserError, match=r"Invalid state\.json"):
        store.read()


@pytest.mark.parametrize(
    "command",
    [
        ["config", "show"],
        ["config", "set", "defaults.project", "OTHER"],
        ["context", "show"],
        ["context", "set", "WWRAP-1"],
        ["context", "set", "--issue", "WWRAP-2", "--global"],
        ["context", "clear"],
        ["context", "use-project", "OTHER"],
        ["context", "use-project", "--project", "OTHER", "--global"],
    ],
)
def test_local_cli_commands_need_no_token(
    application: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    (application / ".env").unlink()
    assert main(["--format", "json", *command]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_context_project_missing_fails_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["context", "use-project"]) == 1
    assert "Provide a project" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        ["auth", "test"],
        ["doctor"],
        ["metadata", "project", "WWRAP"],
        ["metadata", "fields", "--type", "Story"],
        ["metadata", "transitions", "WWRAP-1"],
        ["issue", "view", "WWRAP-1"],
        ["issue", "view", "--issue", "WWRAP-1", "--detail"],
        ["issue", "list"],
        ["search", "--jql", "assignee = currentUser()"],
    ],
)
def test_readonly_cli_uses_bearer_and_redacts_output(
    application: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
    network: tuple[dict[str, Any], list[httpx.Request]],
) -> None:
    assert main([*command, "--format", "json"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "fixture-pat-never-print" not in output.out
    assert json.loads(output.out) is not None
    assert network[1]
    assert all(r.method == "GET" for r in network[1])
    assert all(r.url.path.startswith("/jira/rest/api/2/") for r in network[1])
    assert not (application / "state.json").exists()


def test_search_paginates_and_honors_project(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["issue", "list", "--project", "OTHER", "--format", "json"]) == 0
    assert [row["key"] for row in json.loads(capsys.readouterr().out)] == [
        "WWRAP-1",
        "WWRAP-2",
    ]
    searches = [r for r in network[1] if r.url.path.endswith("/search")]
    assert [r.url.params["startAt"] for r in searches] == ["0", "1"]
    assert 'project = "OTHER"' in searches[0].url.params["jql"]


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_http_failures_redact_bodies_and_do_not_retry(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
    status: int,
) -> None:
    network[0]["myself"] = httpx.Response(
        status,
        text="fixture-pat-never-print",
        headers={"Location": "https://other.example.test"},
    )
    assert main(["auth", "test"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "fixture-pat-never-print" not in output.err
    assert len(network[1]) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="HTML login page"),
        httpx.Response(200, json=[]),
        httpx.ConnectError("fixture-pat-never-print"),
    ],
)
def test_malformed_responses_and_transport_errors(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
    response: object,
) -> None:
    network[0]["myself"] = response
    assert main(["auth", "test"]) == 1
    output = capsys.readouterr()
    assert "fixture-pat-never-print" not in output.err
    assert output.out == ""


def test_metadata_fails_on_ambiguous_issue_type(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["issue/createmeta/WWRAP/issuetypes"] = {"values": [], "isLast": True}
    assert main(["metadata", "fields"]) == 1
    assert "unavailable or ambiguous" in capsys.readouterr().err


def test_browser_url_validates_key_and_keeps_context(
    application: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    urls: list[str] = []

    def open_browser(url: str) -> bool:
        urls.append(url)
        return True

    monkeypatch.setattr("jira_cli.cli.webbrowser.open", open_browser)
    assert main(["context", "set", "WWRAP-1"]) == 0
    capsys.readouterr()
    before = (application / "state.json").read_bytes()
    assert main(["issue", "open", "--format", "json"]) == 0
    assert urls == ["https://jira.example.test/jira/browse/WWRAP-1"]
    assert (application / "state.json").read_bytes() == before
    capsys.readouterr()
    monkeypatch.setattr("jira_cli.cli.webbrowser.open", lambda _: False)
    assert main(["issue", "open", "WWRAP-1"]) == 1
    assert "Browser did not open" in capsys.readouterr().err


def test_quiet_table_and_completion(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor", "--quiet", "--non-interactive"]) == 0
    assert capsys.readouterr().out == ""
    assert main(["config", "show"]) == 0
    assert "Field" in capsys.readouterr().out
    assert main(["completion", "powershell"]) == 0
    assert "Register-ArgumentCompleter" in capsys.readouterr().out
    assert main(["completion", "powershell", "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_cli_help_and_invalid_syntax() -> None:
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    with pytest.raises(SystemExit) as syntax_exit:
        main(["--format", "invalid", "doctor"])
    assert syntax_exit.value.code == 2


def test_file_failure_is_safe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture-pat-never-print")

    monkeypatch.setattr(ContextStore, "update", fail)
    assert main(["context", "set", "WWRAP-1"]) == 1
    assert "fixture-pat-never-print" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "page",
    [
        {"issues": [], "total": 1, "startAt": 0},
        {"issues": "bad", "total": 1, "startAt": 0},
        {"issues": [], "total": -1, "startAt": 0},
        {"issues": [1], "total": 1, "startAt": 0},
        {"issues": [], "total": 1, "startAt": 50},
    ],
)
def test_invalid_search_pages_are_rejected(
    application: Path, page: dict[str, Any]
) -> None:
    client = JiraClient(load_config(application), SecretStr("fixture-pat-never-print"))
    client.http.close()
    client.http = httpx.Client(
        base_url="https://jira.example.test/jira/rest/api/2/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=page)),
    )
    try:
        with pytest.raises(UserError, match="Jira"):
            client.search("project = WWRAP")
    finally:
        client.close()
