import copy
import csv
import io
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from jira_cli import report_cli, reporting
from jira_cli.cli import main
from jira_cli.client import JiraClient
from jira_cli.config import UserError, load_config, set_config

pytestmark = pytest.mark.usefixtures("application")
type Network = tuple[dict[str, Any], list[httpx.Request]]
END = datetime(2026, 10, 26, 12, tzinfo=UTC)
CREATED = "2026-10-01T00:00:00+00:00"
STATES = {
    "1": {"id": "1", "name": "Open", "statusCategory": {"key": "new"}},
    "2": {"id": "2", "name": "Working", "statusCategory": {"key": "indeterminate"}},
    "3": {"id": "3", "name": "Resolved", "statusCategory": {"key": "done"}},
    "4": {"id": "4", "name": "Closed", "statusCategory": {"key": "done"}},
}


def issue(
    number: int,
    state: str = "2",
    updated: str = "2026-10-25T10:00:00+00:00",
    created: str = CREATED,
) -> dict[str, Any]:
    return {
        "key": f"WWRAP-{number}",
        "fields": {
            "summary": "A safe summary",
            "status": copy.deepcopy(STATES[state]),
            "created": created,
            "updated": updated,
            "priority": {"name": "High"},
            "assignee": {"name": "fixture-user"},
            "duedate": "2026-10-25",
            "issuelinks": [],
            "description": "DO-NOT-EXPORT-BODY",
            "comment": {"comments": [{"body": "DO-NOT-EXPORT-COMMENT"}]},
        },
    }


def event(number: int, when: str, before: str, after: str) -> dict[str, Any]:
    return {
        "id": str(number),
        "created": when,
        "author": {"name": "PRIVATE-AUTHOR"},
        "items": [
            {
                "field": "status",
                "from": before,
                "to": after,
                "fromString": "not-used",
                "toString": "not-used",
            }
        ],
    }


def setup(network: Network, rows: list[dict[str, Any]]) -> None:
    routes, _ = network
    routes["status"] = list(STATES.values())
    routes["search"] = lambda _: httpx.Response(
        200, json={"startAt": 0, "total": len(rows), "issues": rows}
    )
    for row in rows:
        routes["issue/" + row["key"]] = {
            "key": row["key"],
            "fields": {k: row["fields"][k] for k in ["status", "created", "updated"]},
            "changelog": {"startAt": 0, "total": 0, "histories": []},
        }


def history(network: Network, number: int, events: list[dict[str, Any]]) -> None:
    network[0][f"issue/WWRAP-{number}"]["changelog"].update(
        {"total": len(events), "histories": events}
    )


def build(
    application: Path, kind: str = "weekly", since: str | None = None
) -> dict[str, Any]:
    client = JiraClient(load_config(application), SecretStr("fixture-pat-never-print"))
    try:
        return reporting.build(
            client,
            load_config(application),
            reporting.Request(kind, "WWRAP", since=since),
            now=END,
        )
    finally:
        client.close()


def test_weekly_history_reopened_done_active_and_carried(
    network: Network,
    application: Path,
) -> None:
    setup(
        network,
        [
            issue(1, "3"),
            issue(2, updated="2026-10-10T00:00:00Z"),
            issue(3),
            issue(4),
            issue(5, created="2026-10-23T00:00:00Z"),
            issue(6, "3", updated="2026-10-10T00:00:00Z"),
            issue(7, "4"),
        ],
    )
    history(network, 1, [event(1, "2026-10-20T12:00:00Z", "2", "3")])
    history(
        network,
        3,
        [
            event(1, "2026-10-20T12:00:00Z", "2", "3"),
            event(2, "2026-10-24T12:00:00Z", "3", "2"),
        ],
    )
    history(
        network,
        4,
        [
            event(1, "2026-10-18T12:00:00Z", "2", "3"),
            event(2, "2026-10-24T12:00:00Z", "3", "2"),
        ],
    )
    history(network, 7, [event(1, "2026-10-24T12:00:00Z", "3", "4")])
    result = build(application)
    rows = {row["key"]: row for row in result["issues"]}
    assert set(rows) == {f"WWRAP-{i}" for i in range(1, 6)}
    assert result["counts"]["completed_during_window"] == 2
    assert result["counts"]["currently_active"] == 4
    assert result["counts"]["carried_over"] == 2
    assert rows["WWRAP-3"]["currently_active"] is True
    assert rows["WWRAP-3"]["completed_at"]
    assert rows["WWRAP-4"]["carried_over"] is False
    assert rows["WWRAP-5"]["carried_over"] is False
    assert rows["WWRAP-1"]["overdue"] is False
    assert result["window_start"] == "2026-10-19T12:00:00+00:00"
    assert result["window_end_exclusive"] == END.isoformat()
    assert result["time_zone"] == "UTC"
    assert all(request.method == "GET" for request in network[1])
    assert all(
        request.url.params["expand"] == "changelog"
        for request in network[1]
        if "/issue/" in request.url.path
    )
    encoded = json.dumps(result)
    assert "DO-NOT-EXPORT" not in encoded
    assert "PRIVATE-AUTHOR" not in encoded


def test_window_boundary_and_status_ids_not_display_names(
    network: Network,
    application: Path,
) -> None:
    setup(network, [issue(1, "3")])
    history(network, 1, [event(1, "2026-10-19T08:00:00-0400", "2", "3")])
    assert build(application)["counts"]["completed_during_window"] == 1
    history(network, 1, [event(1, "2026-10-19T11:59:59Z", "2", "3")])
    assert build(application)["counts"]["completed_during_window"] == 0


def test_daily_blockers_and_inferred_risks(network: Network, application: Path) -> None:
    row = issue(1, updated="2026-10-10T00:00:00Z")
    row["fields"]["issuelinks"] = [
        {"type": {"inward": "is blocked by"}, "inwardIssue": issue(2)},
        {"type": {"inward": "is blocked by"}, "inwardIssue": issue(3, "3")},
        {"type": {"inward": "is blocked by"}, "inwardIssue": {"key": "OTHER-1"}},
    ]
    setup(network, [row, issue(4, "3")])
    result = build(application, "daily")
    assert result["counts"]["issues"] == 1
    assert result["counts"]["overdue"] == 1
    assert result["counts"]["blocked"] == 1
    assert result["issues"][0]["blockers"] == ["WWRAP-2"]
    assert len(result["issues"][0]["inferred_risks"]) == 3
    assert not any(r.url.path.endswith("status") for r in network[1])
    assert build(application, "daily", "2026-10-11")["issues"] == []


def test_aging_threshold_exact_and_configurable(
    network: Network, application: Path
) -> None:
    setup(
        network,
        [
            issue(1, updated=(END - timedelta(days=14)).isoformat()),
            issue(
                2, updated=(END - timedelta(days=14) + timedelta(seconds=1)).isoformat()
            ),
            issue(3, "3", updated="2026-10-10T00:00:00Z"),
        ],
    )
    assert [r["key"] for r in build(application, "aging")["issues"]] == ["WWRAP-1"]
    set_config(application, "reporting.aging_threshold_days", "13")
    assert build(application, "aging")["counts"]["issues"] == 2
    assert build(application, "aging", "2026-10-13")["issues"] == []


@pytest.mark.parametrize(
    "log",
    [
        None,
        {},
        {"startAt": 1, "total": 0, "histories": []},
        {"startAt": 0, "total": 2, "histories": []},
        {"startAt": 0, "total": True, "histories": []},
        {"startAt": 0, "total": 1, "histories": [None]},
        {"startAt": 0, "total": 2, "histories": [event(1, CREATED, "1", "2")] * 2},
    ],
)
def test_incomplete_history_does_not_emit_partial_export(
    network: Network,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    log: Any,
) -> None:
    setup(network, [issue(1)])
    network[0]["issue/WWRAP-1"]["changelog"] = log
    path = tmp_path / "must not exist.json"
    assert (
        main(
            [
                "report",
                "weekly",
                "--output",
                str(path),
                "--format",
                "json",
                "--since",
                "2026-10-19",
            ]
        )
        == 1
    )
    assert not path.exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "change",
    [
        event(1, "2026-10-24T12:00:00Z", "deleted", "2"),
        event(1, "2026-10-24T12:00:00Z", "2", "3"),
        event(1, "2026-10-26T12:00:00Z", "1", "2"),
        event(1, "2026-09-01T12:00:00Z", "1", "2"),
    ],
)
def test_unprovable_completion_fails(
    network: Network, application: Path, change: Any
) -> None:
    setup(network, [issue(1)])
    history(network, 1, [change])
    with pytest.raises(UserError, match="history"):
        build(application)


def test_changed_issue_and_history_permissions_fail(
    network: Network, application: Path
) -> None:
    setup(network, [issue(1)])
    network[0]["issue/WWRAP-1"]["fields"]["updated"] = CREATED
    with pytest.raises(UserError, match="changed"):
        build(application)
    network[0]["issue/WWRAP-1"] = httpx.Response(403, text="DO-NOT-LOG")
    with pytest.raises(UserError, match="permission denied"):
        build(application)


@pytest.mark.parametrize(
    "value", ["", "2026-1-01", "2026-02-30", "not-a-date", "2026-10-27"]
)
def test_invalid_since_before_network(
    network: Network, application: Path, value: str
) -> None:
    with pytest.raises(UserError):
        build(application, since=value)
    assert network[1] == []


@pytest.mark.parametrize("value", [None, "bad", "2026-10-01T12:00:00"])
def test_timestamp_requires_zone(value: Any) -> None:
    with pytest.raises(UserError, match="timestamps"):
        reporting.timestamp(value)


@pytest.mark.parametrize("format_name", ["json", "csv", "markdown", "table"])
def test_all_formats_redact_whitelist_and_avoid_terminal_injection(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    format_name: str,
) -> None:
    row = issue(1)
    row["fields"]["summary"] = (
        "fixture-pat-never-print\x1b[31m\n![x](https://invalid.test) | =1"
    )
    setup(network, [row])
    assert main(["report", "daily", "--format", format_name]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "fixture-pat-never-print" not in output.out
    assert "DO-NOT-EXPORT" not in output.out
    assert "\x1b" not in output.out
    assert "REDACTED" in output.out
    if format_name == "json":
        data = json.loads(output.out)
        assert data["issues"][0]["key"] == "WWRAP-1"
    if format_name == "csv":
        rows = list(csv.DictReader(io.StringIO(output.out)))
        assert rows[0]["record_type"] == "metadata"
        assert json.loads(rows[0]["metadata"])["time_zone"] == "UTC"
        assert rows[1]["key"] == "WWRAP-1"
    if format_name == "markdown":
        assert "![x](" not in output.out
        assert "\\|" in output.out


@pytest.mark.parametrize(
    "value", ["=1+1", "+CMD", "-CMD", "@SUM(1)", "  =1", "\t=1", "\nCMD"]
)
def test_csv_formula_cells_are_neutralized(value: str) -> None:
    assert report_cli.spreadsheet(value).startswith("'")


def test_explicit_output_paths_quiet_no_clobber_and_private_mode(
    network: Network,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network, [issue(1)])
    destination = tmp_path / "my private export.json"
    assert (
        main(
            [
                "report",
                "daily",
                "--format",
                "json",
                "--output",
                str(destination),
                "--quiet",
            ]
        )
        == 0
    )
    assert json.loads(destination.read_text())["counts"]["issues"] == 1
    assert capsys.readouterr().out == ""
    before = destination.read_bytes()
    assert main(["report", "daily", "--output", str(destination)]) == 1
    assert destination.read_bytes() == before
    assert main(["report", "daily", "--output", str(tmp_path / "absent" / "file")]) == 1
    assert not (tmp_path / "absent").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "App Data",
        "my private export.json",
    ]


def test_report_scope_flags_and_no_saved_context(
    network: Network,
    application: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup(network, [])
    (application / "state.json").write_text("invalid JSON")
    set_config(application, "reporting.default_project", "REPORTS")
    assert main(["--format", "csv", "report", "daily", "--mine"]) == 0
    assert (
        'project = "REPORTS" AND assignee = currentUser()'
        in network[1][-1].url.params["jql"]
    )
    assert "metadata" in capsys.readouterr().out
    monkeypatch.setenv("JIRA_PROJECT", "ENV")
    assert main(["report", "daily", "--quiet"]) == 0
    assert 'project = "ENV"' in network[1][-1].url.params["jql"]
    assert capsys.readouterr().out == ""
    assert main(["--project", "WWRAP", "report", "daily", "--quiet"]) == 0
    assert 'project = "WWRAP"' in network[1][-1].url.params["jql"]
    assert main(["report", "daily", "--issue", "WWRAP-1"]) == 1
    assert main(["issue", "list", "--format", "csv"]) == 1


def test_empty_reports_keep_metadata(network: Network, application: Path) -> None:
    setup(network, [])
    result = build(application)
    assert result["issues"] == []
    assert result["counts"]["completed_during_window"] == 0
    assert "definitions" in json.loads(
        next(csv.DictReader(io.StringIO(report_cli.render_csv(result))))["metadata"]
    )


def test_weekly_custom_lookback_and_urls(network: Network, application: Path) -> None:
    setup(network, [issue(1)])
    set_config(application, "reporting.weekly_lookback_days", "3")
    set_config(application, "output.show_issue_urls", "false")
    result = build(application)
    assert result["window_start"] == (END - timedelta(days=3)).isoformat()
    assert result["issues"][0]["url"] == ""


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [None],
        [{"id": "2"}],
        [{"id": "2", "statusCategory": {"key": []}}],
        [STATES["2"], {"id": "2", "statusCategory": {"key": "done"}}],
    ],
)
def test_invalid_status_metadata_does_not_guess(
    network: Network,
    application: Path,
    metadata: Any,
) -> None:
    setup(network, [issue(1)])
    network[0]["status"] = metadata
    with pytest.raises(UserError, match=r"status|invalid JSON"):
        build(application)


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "1", "created": CREATED, "items": None},
        {"id": "1", "created": CREATED, "items": [None]},
        {
            "id": "1",
            "created": CREATED,
            "items": [{"fieldId": "status", "from": None, "to": "2"}],
        },
    ],
)
def test_invalid_history_items(
    network: Network, application: Path, changes: Any
) -> None:
    setup(network, [issue(1)])
    history(network, 1, [changes])
    with pytest.raises(UserError, match="history"):
        build(application)


def test_repeated_completion_counts_unique_issues(
    network: Network, application: Path
) -> None:
    row = issue(1)
    setup(network, [row, row])
    history(
        network,
        1,
        [
            event(1, "2026-10-20T00:00:00Z", "2", "3"),
            event(2, "2026-10-21T00:00:00Z", "3", "2"),
            event(3, "2026-10-22T00:00:00Z", "2", "3"),
            event(4, "2026-10-24T00:00:00Z", "3", "2"),
        ],
    )
    result = build(application)
    assert result["counts"]["completed_during_window"] == 1
    assert len(result["issues"][0]["completed_at"]) == 2


def test_report_paginated_search(network: Network, application: Path) -> None:
    pages = []

    def search(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startAt"])
        pages.append(start)
        return httpx.Response(
            200, json={"startAt": start, "total": 2, "issues": [issue(start + 1)]}
        )

    network[0]["search"] = search
    result = build(application, "daily")
    assert result["counts"]["issues"] == 2
    assert pages == [0, 1]


def test_output_link_is_not_replaced(tmp_path: Path) -> None:
    original = tmp_path / "original"
    original.write_text("keep")
    target = tmp_path / "export"
    # Existing hard links need no Windows symlink privilege.
    os.link(original, target)
    with pytest.raises(UserError, match="Cannot create report"):
        report_cli.save(target, "replace")
    assert original.read_text() == "keep"
    assert target.read_text() == "keep"


def test_daily_snapshot_is_deterministic_and_utc(
    network: Network, application: Path
) -> None:
    setup(network, [issue(2), issue(1)])
    first = build(application, "daily")
    assert first == build(application, "daily")
    assert [row["key"] for row in first["issues"]] == ["WWRAP-1", "WWRAP-2"]
    assert (
        reporting.timestamp("2026-10-25T23:00:00-0400").isoformat()
        == "2026-10-26T03:00:00+00:00"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created", "2026-10-26T11:59:59+00:00"),
        ("updated", "2026-10-26T12:00:00+00:00"),
        ("summary", None),
        ("priority", "High"),
        ("assignee", {}),
    ],
)
def test_invalid_snapshot_does_not_export(
    network: Network,
    application: Path,
    field: str,
    value: Any,
) -> None:
    row = issue(1)
    row["fields"][field] = value
    setup(network, [row])
    with pytest.raises(UserError):
        build(application, "daily")
