"""Terminal link contracts; every Jira response and URL is synthetic."""

import argparse
import io
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console

from jira_cli import report_cli
from jira_cli.cli import emit, main
from jira_cli.config import load_config
from jira_cli.context import ContextStore, Selection
from jira_cli.presentation import cell, linked
from jira_cli.views import EPIC_SCHEMA

pytestmark = pytest.mark.usefixtures("application")
type Network = tuple[dict[str, Any], list[httpx.Request]]
BASE = "https://jira.example.test/jira"
LINK = "customfield_98761"


def targets(output: str) -> set[str]:
    return set(re.findall(r"\x1b\]8;[^;]*;([^\x1b]+)\x1b\\", output))


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    def console(**kwargs: Any) -> Console:
        kwargs.setdefault("force_terminal", True)
        kwargs.setdefault("color_system", "standard")
        kwargs["legacy_windows"] = False
        kwargs["width"] = 240
        return Console(**kwargs)

    for module in ["presentation", "cli", "personal_cli", "report_cli", "epic_context"]:
        monkeypatch.setattr(f"jira_cli.{module}.Console", console)


@pytest.fixture
def issues(network: Network, application: Path) -> None:
    def issue(key: str, kind: str) -> dict[str, Any]:
        return {
            "key": key,
            "fields": {
                "summary": "Fixture summary",
                "issuetype": {"name": kind},
                "status": {
                    "name": "Working",
                    "statusCategory": {"key": "indeterminate"},
                },
                "priority": {"name": "Expedite"},
                "assignee": {"name": "fixture-user", "displayName": "Smith, Jane"},
                LINK: "WWRAP-100" if kind == "Story" else None,
                "issuelinks": [
                    {
                        "outwardIssue": {"key": "OTHER-22"},
                        "type": {"outward": "relates to"},
                    }
                ],
                "comment": {"comments": [{"body": "See OTHER-23"}]},
            },
        }

    story, epic = issue("WWRAP-10", "Story"), issue("WWRAP-100", "Epic")
    network[0].update(
        {
            "field": [{"id": LINK, "schema": {"custom": EPIC_SCHEMA}}],
            "issue/WWRAP-10": story,
            "issue/WWRAP-100": epic,
        }
    )

    def search(request: httpx.Request) -> httpx.Response:
        rows = [epic] if 'issuetype = "Epic"' in request.url.params["jql"] else [story]
        return httpx.Response(200, json={"startAt": 0, "total": 1, "issues": rows})

    network[0]["search"] = search
    ContextStore(application, load_config(application)).update(
        Selection(issue="WWRAP-10")
    )


def test_shared_links_use_literal_keys_and_correct_base() -> None:
    label = "[bold]WWRAP-10[/bold], OTHER-22 and —"
    value = linked(label, BASE)
    assert value.plain == label
    stream = io.StringIO()
    Console(
        file=stream,
        force_terminal=True,
        legacy_windows=False,
        color_system="standard",
        markup=False,
    ).print(value)
    assert targets(stream.getvalue()) == {
        BASE + "/browse/WWRAP-10",
        BASE + "/browse/OTHER-22",
    }
    assert linked(cell(None), BASE).spans == []
    assert linked("WWRAP-0, prefix-WWRAP-10, WWRAP-10suffix", BASE).spans == []


@pytest.mark.usefixtures("terminal")
def test_links_escape_label_controls_and_redact_url_secrets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    value = linked("\x1b\x9b WWRAP-10", BASE)
    assert "\x1b" not in value.plain
    assert "\x9b" not in value.plain
    assert value.spans[0].start == value.plain.index("WWRAP-10")
    args = argparse.Namespace(
        command="issue", action="view", detail=False, format="table", quiet=False
    )
    emit(
        {"key": "WWRAP-10", "type": "Story"},
        args,
        "fixture-pat-never-print",
        jira_url=BASE + "/fixture-pat-never-print",
    )
    output = capsys.readouterr().out
    assert "fixture-pat-never-print" not in output
    assert targets(output)


@pytest.mark.parametrize(
    "base",
    [
        None,
        "",
        "javascript:alert(1)",
        "http://jira.example.test",
        "https://user:secret@jira.example.test",
        "https://jira.example.test/?x=1",
        "https://jira.example.test/#fragment",
        "https://[invalid",
        "https://jira.example.test:invalid",
        "https://jira.example.test/\x1b]8;;evil",
        "https://jira.example.test/\n",
        "https://jira.example.test/\x9b",
    ],
)
def test_unsafe_or_missing_base_falls_back_to_plain_key(base: str | None) -> None:
    value = linked("WWRAP-10", base)
    assert value.plain == "WWRAP-10"
    assert value.spans == []


@pytest.mark.usefixtures("terminal", "issues")
@pytest.mark.parametrize(
    ("args", "key"),
    [
        (["issue", "list"], "WWRAP-10"),
        (["story", "list", "--no-epic"], "WWRAP-10"),
        (["epic", "list"], "WWRAP-100"),
        (["my", "today"], "WWRAP-10"),
        (["issue", "view", "WWRAP-10"], "WWRAP-10"),
        (["story", "view", "WWRAP-10"], "WWRAP-10"),
        (["story", "view", "WWRAP-10", "--detail"], "WWRAP-10"),
        (["epic", "view", "WWRAP-100"], "WWRAP-100"),
        (["epic", "view", "WWRAP-100", "--detail"], "WWRAP-100"),
        (["context", "set", "WWRAP-10"], "WWRAP-10"),
        (["context", "show"], "WWRAP-10"),
        (["search", "--jql", 'project = "WWRAP"'], "WWRAP-10"),
    ],
)
def test_cli_tables_link_issue_keys(
    capsys: pytest.CaptureFixture[str],
    args: list[str],
    key: str,
) -> None:
    assert main(args) == 0
    output = capsys.readouterr().out
    assert BASE + "/browse/" + key in targets(output)
    if args[:2] == ["story", "view"]:
        assert BASE + "/browse/WWRAP-100" in targets(output)
        assert BASE + "/browse/OTHER-23" in targets(output)
        if "--detail" in args:
            assert BASE + "/browse/OTHER-22" in targets(output)
    if args[:2] == ["epic", "view"] and "--detail" in args:
        assert BASE + "/browse/WWRAP-10" in targets(output)


@pytest.mark.usefixtures("terminal", "issues")
@pytest.mark.parametrize("command", ["story", "epic"])
def test_pickers_link_keys(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "1")
    assert main([command, "select"]) == 0
    output = capsys.readouterr()
    key = "WWRAP-100" if command == "epic" else "WWRAP-10"
    assert BASE + "/browse/" + key in targets(output.err)


@pytest.mark.usefixtures("issues")
@pytest.mark.parametrize(
    "args",
    [
        ["story", "list", "--no-epic"],
        ["my", "today"],
        ["story", "view", "WWRAP-10"],
        ["context", "show"],
    ],
)
def test_redirected_tables_and_json_remain_plain(
    capsys: pytest.CaptureFixture[str],
    args: list[str],
) -> None:
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "WWRAP-10" in output
    assert "\x1b" not in output
    assert main([*args, "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\\u001b" not in output
    assert json.loads(output)
    assert main([*args, "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def report() -> dict[str, Any]:
    row = dict.fromkeys(report_cli.COLUMNS)
    row.update(
        key="WWRAP-10",
        summary="Fixture summary",
        status="Working",
        priority="Expedite",
        url=BASE + "/browse/WWRAP-10",
    )
    return {"report": "daily", "issues": [row]}


@pytest.mark.usefixtures("terminal")
def test_terminal_report_links_and_saved_table_is_plain(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    args = argparse.Namespace(format="table", output=None, quiet=False)
    report_cli.emit(report(), args, jira_url=BASE)
    assert BASE + "/browse/WWRAP-10" in targets(capsys.readouterr().out)
    args.output = tmp_path / "report.txt"
    report_cli.emit(report(), args, jira_url=BASE)
    assert "\x1b" not in args.output.read_text(encoding="utf-8")
    assert capsys.readouterr().out == ""
    data = report()
    data["issues"][0]["url"] = ""
    report_cli.emit(
        data,
        argparse.Namespace(format="table", output=None, quiet=False),
        jira_url=BASE,
    )
    assert targets(capsys.readouterr().out) == set()


@pytest.mark.usefixtures("terminal")
@pytest.mark.parametrize("format_name", ["json", "csv", "markdown"])
def test_exports_never_include_terminal_hyperlinks(
    capsys: pytest.CaptureFixture[str],
    format_name: str,
) -> None:
    args = argparse.Namespace(format=format_name, output=None, quiet=False)
    report_cli.emit(report(), args, jira_url=BASE)
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\\u001b" not in output
