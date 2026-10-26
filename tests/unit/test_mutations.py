import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from jira_cli.cli import main
from jira_cli.config import Config, UserError
from jira_cli.context import ContextStore
from jira_cli.metadata import coerce, field_map, find_field, prepare

pytestmark = pytest.mark.usefixtures("application", "write_metadata")


@pytest.fixture
def write_metadata(
    network: tuple[dict[str, Any], list[httpx.Request]],
) -> dict[str, Any]:
    routes, _ = network
    specs: dict[str, Any] = {
        "summary": {"name": "Summary", "required": True, "schema": {"type": "string"}},
        "description": {"name": "Description", "schema": {"type": "string"}},
        "priority": {
            "name": "Priority",
            "schema": {"type": "priority"},
            "allowedValues": [{"id": "3", "name": "High"}],
        },
        "assignee": {"name": "Assignee", "schema": {"type": "user"}},
        "customfield_12900": {
            "name": "Epic Link",
            "schema": {
                "type": "any",
                "custom": "com.pyxis.greenhopper.jira:gh-epic-link",
            },
        },
        "labels": {"name": "Labels", "schema": {"type": "array", "items": "string"}},
        "duedate": {"name": "Due Date", "schema": {"type": "date"}},
        "status": {"name": "Status", "schema": {"type": "string"}},
    }
    routes["mypermissions"] = {
        "permissions": {
            key: {"havePermission": True}
            for key in [
                "CREATE_ISSUES",
                "EDIT_ISSUES",
                "ADD_COMMENTS",
                "CREATE_ATTACHMENTS",
                "TRANSITION_ISSUES",
                "ASSIGN_ISSUES",
            ]
        }
    }
    routes["issue/createmeta/WWRAP/issuetypes"] = {
        "isLast": True,
        "values": [{"id": "10001", "name": "Story"}, {"id": "10002", "name": "Epic"}],
    }
    rows = [{"fieldId": key, **spec} for key, spec in specs.items() if key != "status"]
    routes["issue/createmeta/WWRAP/issuetypes/10001"] = {"isLast": True, "values": rows}
    routes["issue/createmeta/WWRAP/issuetypes/10002"] = {
        "isLast": True,
        "values": [
            *rows,
            {
                "fieldId": "customfield_13000",
                "name": "Epic Name",
                "required": True,
                "schema": {"type": "string"},
            },
        ],
    }
    routes["issue/WWRAP-1/editmeta"] = {"fields": specs}
    routes["issue/WWRAP-200"] = {
        "key": "WWRAP-200",
        "fields": {"issuetype": {"name": "Epic"}},
    }
    routes["issue/WWRAP-1/transitions"] = {
        "transitions": [
            {
                "id": "31",
                "name": "Start",
                "to": {"name": "In Progress"},
                "fields": {
                    "resolution": {
                        "name": "Resolution",
                        "required": True,
                        "schema": {"type": "resolution"},
                        "allowedValues": [{"id": "1", "name": "Fixed"}],
                    }
                },
            }
        ]
    }
    routes["user/assignable/search"] = [{"name": "bob", "active": True}]
    routes["attachment/meta"] = {"enabled": True, "uploadLimit": 10000}
    routes["POST issue"] = httpx.Response(201, json={"id": "100", "key": "WWRAP-100"})
    routes["PUT issue/WWRAP-1"] = httpx.Response(204)
    routes["POST issue/WWRAP-1/comment"] = httpx.Response(201, json={"id": "700"})
    routes["POST issue/WWRAP-1/attachments"] = httpx.Response(200, json=[{"id": "800"}])
    routes["POST issue/WWRAP-1/transitions"] = httpx.Response(204)
    return specs


def writes(network: tuple[dict[str, Any], list[httpx.Request]]) -> list[httpx.Request]:
    return [r for r in network[1] if r.method != "GET"]


def test_creation_uses_server_key_metadata_and_confirmed_context(
    application: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "create",
                "story",
                "--summary",
                "New issue",
                "--priority",
                "High",
                "--assignee",
                "bob",
                "--epic",
                "WWRAP-200",
                "--format",
                "json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "key": "WWRAP-100",
        "url": "https://jira.example.test/jira/browse/WWRAP-100",
        "context_updated": True,
    }
    fields = json.loads(writes(network)[0].content)["fields"]
    assert fields["project"] == {"key": "WWRAP"}
    assert fields["issuetype"] == {"id": "10001"}
    assert fields["priority"] == {"id": "3"}
    assert fields["assignee"] == {"name": "bob"}
    assert fields["customfield_12900"] == "WWRAP-200"
    assert "key" not in fields
    assert ContextStore(application, Config()).issue(None) == "WWRAP-100"


def test_epic_creation_requires_its_custom_name(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "create",
                "epic",
                "--summary",
                "Epic summary",
                "--field",
                "Epic Name=Epic label",
                "--no-context",
            ]
        )
        == 0
    )
    capsys.readouterr()
    fields = json.loads(writes(network)[0].content)["fields"]
    assert fields["issuetype"] == {"id": "10002"}
    assert fields["customfield_13000"] == "Epic label"


@pytest.mark.parametrize(
    "response",
    [
        httpx.ReadTimeout("fixture-pat-never-print"),
        httpx.Response(500),
        httpx.Response(201, text="bad"),
        httpx.Response(201, json={}),
        httpx.Response(201, json={"id": "1", "key": "invalid"}),
        httpx.Response(204),
        httpx.Response(302),
    ],
)
def test_uncertain_create_is_not_retried_or_saved(
    response: object,
    application: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["POST issue"] = response
    assert main(["context", "set", "WWRAP-1"]) == 0
    capsys.readouterr()
    before = (application / "state.json").read_bytes()
    assert main(["create", "story", "--summary", "Uncertain", "--non-interactive"]) == 1
    output = capsys.readouterr()
    assert "before retrying" in output.err
    assert "fixture-pat-never-print" not in output.err
    assert len(writes(network)) == 1
    assert (application / "state.json").read_bytes() == before


@pytest.mark.parametrize("options", [["--no-context"], []])
def test_creation_can_disable_context_updates(
    options: list[str],
    application: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if not options:
        (application / "config.yaml").write_text(
            "jira:\n  base_url: https://jira.example.test/jira\n"
            "creation:\n  set_context_after_create: false\n"
        )
    assert (
        main(
            ["create", "story", "--summary", "No context", "--format", "json", *options]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["context_updated"] is False
    assert not (application / "state.json").exists()


def test_creation_returns_confirmed_key_if_local_save_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture-pat-never-print")

    monkeypatch.setattr(ContextStore, "update", fail)
    assert main(["create", "story", "--summary", "Confirmed", "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["key"] == "WWRAP-100"
    assert "Do not create it again" in output
    assert "fixture-pat-never-print" not in output


@pytest.mark.parametrize(
    "server",
    [
        {"version": "7.13", "deploymentType": "Server"},
        {"version": "10.3", "deploymentType": "Cloud"},
        {},
        {"version": "unknown", "deploymentType": "Data Center"},
    ],
)
def test_writes_require_verified_server_version(
    server: object,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["serverInfo"] = server
    assert main(["create", "story", "--summary", "Denied"]) == 1
    assert not writes(network)
    assert "PAT-capable" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        ["create", "story", "--summary", "Missing rights"],
        ["comment", "add", "Hello", "--issue", "WWRAP-1"],
        ["set", "field", "summary", "Edited", "--issue", "WWRAP-1"],
        ["set", "status", "Start", "--issue", "WWRAP-1"],
    ],
)
def test_permissions_fail_before_mutation(
    command: list[str],
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["mypermissions"] = {"permissions": {}}
    assert main(command) == 1
    assert not writes(network)
    assert "permission" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command",
    [
        ["create", "story"],
        ["set", "status", "--issue", "WWRAP-1"],
        ["set", "field", "--issue", "WWRAP-1"],
        ["set", "epic", "--issue", "WWRAP-1"],
        ["comment", "add", "--issue", "WWRAP-1"],
    ],
)
def test_missing_noninteractive_values_never_prompt(
    command: list[str],
    monkeypatch: pytest.MonkeyPatch,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("Unexpected input"))
    assert main([*command, "--non-interactive"]) == 1
    assert not writes(network)
    assert "No prompt" in capsys.readouterr().err


def test_interactive_create_and_field_choices(
    monkeypatch: pytest.MonkeyPatch,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    replies = iter(["Interactive summary", "summary", "Edited summary"])
    monkeypatch.setattr("builtins.input", lambda: next(replies))
    assert main(["create", "story", "--no-context", "--format", "json"]) == 0
    assert main(["set", "field", "--issue", "WWRAP-1", "--format", "json"]) == 0
    output = capsys.readouterr()
    assert "Value:" not in output.out
    assert "Value:" in output.err
    assert json.loads(writes(network)[1].content)["fields"] == {
        "summary": "Edited summary"
    }


def test_creation_yaml_and_explicit_file_overrides(
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "creation.yaml"
    source.write_text("summary: From YAML\ndescription: Original\nlabels: [one, two]\n")
    description = tmp_path / "description with spaces.txt"
    description.write_text("Body from file", encoding="utf-8")
    assert (
        main(
            [
                "create",
                "story",
                "--from",
                str(source),
                "--summary",
                "Explicit",
                "--description-file",
                str(description),
            ]
        )
        == 0
    )
    capsys.readouterr()
    fields = json.loads(writes(network)[0].content)["fields"]
    assert fields["summary"] == "Explicit"
    assert fields["description"] == "Body from file"
    assert fields["labels"] == ["one", "two"]


@pytest.mark.parametrize(
    "command",
    [
        ["set", "epic", "WWRAP-200"],
        ["set", "epic", "--clear"],
        ["set", "field", "Epic Link", "WWRAP-200"],
    ],
)
def test_epic_reassignment_and_removal(
    command: list[str],
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([*command, "--issue", "WWRAP-1"]) == 0
    capsys.readouterr()
    fields = json.loads(writes(network)[0].content)["fields"]
    assert fields == {
        "customfield_12900": None if "--clear" in command else "WWRAP-200"
    }


def test_epic_target_type_is_checked(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["issue/WWRAP-200"]["fields"]["issuetype"]["name"] = "Story"
    assert main(["set", "epic", "WWRAP-200", "--issue", "WWRAP-1"]) == 1
    assert not writes(network)
    assert "not an accessible Epic" in capsys.readouterr().err


def test_status_uses_transition_and_required_fields(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "set",
                "status",
                "In Progress",
                "--field",
                "Resolution=Fixed",
                "--issue",
                "WWRAP-1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    request = writes(network)[0]
    assert request.url.path.endswith("/transitions")
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "transition": {"id": "31"},
        "fields": {"resolution": {"id": "1"}},
    }


@pytest.mark.parametrize(
    "command",
    [
        ["set", "status", "Unknown"],
        ["set", "status", "Start"],
        ["set", "field", "status", "Done"],
        ["set", "field", "Missing", "Value"],
        ["set", "field", "priority", "Unknown"],
    ],
)
def test_invalid_changes_do_not_write(
    command: list[str],
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([*command, "--issue", "WWRAP-1", "--non-interactive"]) == 1
    assert not writes(network)
    assert capsys.readouterr().err


@pytest.mark.parametrize("file_input", [False, True])
def test_comments_and_description_files(
    file_input: bool,
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "comment body.txt"
    source.write_text("Selected body", encoding="utf-8")
    args = ["--file", str(source)] if file_input else ["Selected body"]
    assert main(["comment", "add", *args, "--issue", "WWRAP-1"]) == 0
    capsys.readouterr()
    assert json.loads(writes(network)[0].content) == {"body": "Selected body"}
    assert (
        main(
            [
                "set",
                "field",
                "description",
                "--from-file",
                str(source),
                "--issue",
                "WWRAP-1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        json.loads(writes(network)[1].content)["fields"]["description"]
        == "Selected body"
    )


def test_attach_selected_files_with_spaces_and_stop_after_uncertain_result(
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = [tmp_path / f"selected file {i}.txt" for i in range(3)]
    for path in paths:
        path.write_bytes(b"selected content")
    count = 0

    def upload(_request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 2:
            raise httpx.ReadTimeout("fixture-pat-never-print")
        return httpx.Response(200, json=[{"id": "800"}])

    network[0]["POST issue/WWRAP-1/attachments"] = upload
    assert (
        main(["attach", *map(str, paths), "--issue", "WWRAP-1", "--format", "json"])
        == 1
    )
    output = capsys.readouterr()
    assert [f["status"] for f in json.loads(output.out)["files"]] == [
        "uploaded",
        "uncertain",
        "not_attempted",
    ]
    assert len(writes(network)) == 2
    assert writes(network)[0].headers["X-Atlassian-Token"] == "no-check"
    assert b"selected file 0.txt" in writes(network)[0].content
    assert "fixture-pat-never-print" not in output.out + output.err


def test_attachment_preflight_does_not_upload_directories_or_oversize_files(
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["attach", str(tmp_path), "--issue", "WWRAP-1"]) == 1
    capsys.readouterr()
    large = tmp_path / "large.txt"
    large.write_bytes(b"x" * 10001)
    assert main(["attach", str(large), "--issue", "WWRAP-1"]) == 1
    assert not writes(network)
    assert "upload limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("string", "text", "text"),
        ("number", "3", 3.0),
        ("boolean", "false", False),
        ("boolean", True, True),
        ("date", "2026-10-26", "2026-10-26"),
        ("datetime", "2026-10-26T12:00:00+00:00", "2026-10-26T12:00:00+00:00"),
        ("array", '["one"]', ["one"]),
    ],
)
def test_supported_field_shapes(kind: str, value: object, expected: object) -> None:
    assert coerce(value, {"schema": {"type": kind, "items": "string"}}) == expected


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("number", "bad"),
        ("number", "nan"),
        ("boolean", "yes"),
        ("array", "bad"),
        ("array", {}),
        ("date", "bad"),
        ("string", 1),
        ("unknown", "value"),
    ],
)
def test_unsupported_or_invalid_field_shapes_fail(kind: str, value: object) -> None:
    with pytest.raises(UserError):
        coerce(value, {"schema": {"type": kind}})


def test_field_metadata_rejects_ambiguity_readonly_and_required_clear() -> None:
    with pytest.raises(UserError):
        field_map([{"fieldId": "x"}, {"fieldId": "x"}])
    with pytest.raises(UserError):
        field_map([{}])
    with pytest.raises(UserError):
        find_field("Label", {"a": {"name": "Label"}, "b": {"name": "Label"}})
    with pytest.raises(UserError):
        coerce(None, {"operations": []})
    with pytest.raises(UserError):
        coerce(None, {"required": True})
    with pytest.raises(UserError):
        prepare(
            {"a": "one", "Label": "two"},
            {"a": {"name": "Label", "schema": {"type": "string"}}},
            lambda *_: "unused",
            required=False,
        )


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429])
def test_rejected_write_has_no_retry_or_body_disclosure(
    status: int,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["POST issue"] = httpx.Response(status, text="fixture-pat-never-print")
    assert main(["create", "story", "--summary", "Rejected"]) == 1
    output = capsys.readouterr()
    assert len(writes(network)) == 1
    assert "fixture-pat-never-print" not in output.out + output.err


@pytest.mark.parametrize(
    "command",
    [
        ["create", "story", "--summary", "Bad", "--issue", "WWRAP-1"],
        ["create", "story", "--summary", "Bad", "--field", "key=WWRAP-55"],
        ["create", "story", "--summary", "Bad", "--field", "invalid"],
        [
            "create",
            "story",
            "--summary",
            "Bad",
            "--field",
            "labels=[]",
            "--field",
            "labels=[]",
        ],
        ["set", "epic", "WWRAP-200", "--clear", "--issue", "WWRAP-1"],
        ["set", "field", "summary", "text", "--clear", "--issue", "WWRAP-1"],
        ["comment", "add", "", "--issue", "WWRAP-1"],
    ],
)
def test_input_conflicts_fail_without_writes(
    command: list[str],
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(command) == 1
    assert not writes(network)
    assert capsys.readouterr().err


@pytest.mark.parametrize("content", ["[broken", "[one, two]", "1: value"])
def test_invalid_creation_files_do_not_write(
    content: str,
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "invalid.yaml"
    source.write_text(content)
    assert main(["create", "story", "--from", str(source)]) == 1
    assert not writes(network)
    assert capsys.readouterr().err


def test_assignee_must_be_assignable_and_active(
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["user/assignable/search"] = [{"name": "bob", "active": False}]
    assert main(["create", "story", "--summary", "New", "--assignee", "bob"]) == 1
    assert not writes(network)
    assert "assignable" in capsys.readouterr().err


@pytest.mark.parametrize(
    "operation", ["success", "disabled", "rejected", "incomplete", "duplicate"]
)
def test_attachment_confirmations_and_gates(
    operation: str,
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "chosen.txt"
    path.write_text("content")
    if operation == "disabled":
        network[0]["attachment/meta"] = {"enabled": False}
    elif operation == "rejected":
        network[0]["POST issue/WWRAP-1/attachments"] = httpx.Response(403)
    elif operation == "incomplete":
        network[0]["POST issue/WWRAP-1/attachments"] = httpx.Response(200, json=[])
    paths = [str(path)] * (2 if operation == "duplicate" else 1)
    assert main(["attach", *paths, "--issue", "WWRAP-1", "--format", "json"]) == (
        0 if operation == "success" else 1
    )
    output = capsys.readouterr()
    if operation in {"disabled", "duplicate"}:
        assert not writes(network)
    else:
        assert json.loads(output.out)["success"] is (operation == "success")


@pytest.mark.parametrize(
    "result", [[], {"transitions": "invalid"}, {"transitions": [{"name": "No ID"}]}]
)
def test_invalid_transition_metadata_prevents_writes(
    result: object,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network[0]["issue/WWRAP-1/transitions"] = result
    assert main(["set", "status", "Start", "--issue", "WWRAP-1"]) == 1
    assert not writes(network)
    assert capsys.readouterr().err


def test_corrupt_local_context_prevents_creation(
    application: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    (application / "state.json").write_text("broken")
    assert main(["create", "story", "--summary", "New"]) == 1
    assert not writes(network)
    assert capsys.readouterr().err


@pytest.mark.parametrize("reply", ["", EOFError(), KeyboardInterrupt()])
def test_interactive_cancellation_never_creates(
    reply: object,
    monkeypatch: pytest.MonkeyPatch,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def answer() -> str:
        if isinstance(reply, BaseException):
            raise reply
        return str(reply)

    monkeypatch.setattr("builtins.input", answer)
    assert main(["create", "story"]) == 1
    assert not writes(network)
    assert capsys.readouterr().err


def test_text_allowed_values_are_enforced() -> None:
    spec = {"schema": {"type": "string"}, "allowedValues": ["approved"]}
    assert coerce("approved", spec) == "approved"
    with pytest.raises(UserError, match="allowed field value"):
        coerce("other", spec)


def test_file_stream_failure_during_upload_is_uncertain(
    tmp_path: Path,
    network: tuple[dict[str, Any], list[httpx.Request]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "selected.txt"
    path.write_text("content")
    network[0]["POST issue/WWRAP-1/attachments"] = OSError("private file path")
    assert main(["attach", str(path), "--issue", "WWRAP-1", "--format", "json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["files"][0]["status"] == "uncertain"
    assert "private file path" not in output.out + output.err
