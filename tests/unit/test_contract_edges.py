from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_cli.cli import main, redact
from jira_cli.client import JiraClient
from jira_cli.config import Config
from jira_cli.context import ContextStore, Selection

pytestmark = pytest.mark.usefixtures("application")


def test_redaction_preserves_json_structure_and_unicode() -> None:
    value = {'quote"secreté': ['quote"secreté', {"token": 'quote"secreté'}, 3]}
    assert redact(value, 'quote"secreté') == {
        "[REDACTED]": ["[REDACTED]", {"token": "[REDACTED]"}, 3]
    }
    assert redact("unchanged", "") == "unchanged"


@pytest.mark.parametrize(
    "data",
    [
        {"values": "bad"},
        {"values": [], "startAt": 0, "total": 1},
        {"values": [{"name": "Story"}], "isLast": True},
    ],
)
def test_metadata_shape_and_missing_id_fail_safely(
    data: object,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["issue/createmeta/WWRAP/issuetypes"] = data
    assert main(["metadata", "fields"]) == 1
    assert "Jira" in capsys.readouterr().err


def test_metadata_pagination_reaches_later_issue_types(
    network: tuple[dict[str, Any], list[httpx.Request]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = JiraClient.object

    def pages(
        client: JiraClient,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        if path.endswith("/issuetypes"):
            start = (params or {})["startAt"]
            return {
                "startAt": start,
                "total": 2,
                "values": [{"id": "1", "name": "Task"}]
                if start == 0
                else [{"id": "10001", "name": "Story"}],
            }
        return original(client, path, params)

    monkeypatch.setattr(JiraClient, "object", pages)
    assert main(["metadata", "fields", "--format", "json"]) == 0
    assert "summary" in capsys.readouterr().out
    assert network[1][-1].url.path.endswith("/10001")


def test_issue_fields_must_be_an_object(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["issue/WWRAP-1"] = {"fields": []}
    assert main(["issue", "view", "WWRAP-1"]) == 1
    assert "invalid issue fields" in capsys.readouterr().err


def test_explicit_environment_project_precedes_saved_context(
    monkeypatch: pytest.MonkeyPatch,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["context", "use-project", "SAVED"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("JIRA_PROJECT", "ENVIRONMENT")
    assert main(["issue", "list", "--format", "json"]) == 0
    assert (
        'project = "ENVIRONMENT"'
        in next(r for r in network[1] if r.url.path.endswith("/search")).url.params[
            "jql"
        ]
    )
    capsys.readouterr()
    assert main(["issue", "list", "--project", "EXPLICIT", "--quiet"]) == 0
    assert 'project = "EXPLICIT"' in network[1][-1].url.params["jql"]


@pytest.mark.parametrize("server", [{}, {"version": "100", "deploymentType": "Cloud"}])
def test_doctor_does_not_attest_unknown_or_cloud_deployment(
    server: object,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["serverInfo"] = server
    assert main(["doctor"]) == 1
    assert capsys.readouterr().out == ""


def test_config_show_redacts_token_even_in_an_accidental_url_path(
    application: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (application / "config.yaml").write_text(
        "jira:\n  base_url: https://jira.example.test/fixture-pat-never-print\n"
    )
    assert main(["config", "show", "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert "fixture-pat-never-print" not in output
    assert "[REDACTED]" in output


def test_unicode_directory_state_round_trips_as_utf8(
    application: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working = tmp_path / "客户 工作"
    working.mkdir()
    monkeypatch.chdir(working)
    store = ContextStore(application, Config())
    store.update(Selection(issue="WWRAP-1"))
    assert store.issue(None) == "WWRAP-1"
    assert "客户 工作" in store.path.read_text(encoding="utf-8")
