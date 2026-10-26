from pathlib import Path
from typing import Any

import httpx
import pytest


@pytest.fixture
def application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ["JIRA_PAT", "JIRA_BASE_URL", "JIRA_PROJECT", "XDG_CONFIG_HOME"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "App Data"))
    directory = tmp_path / "App Data" / "jira-cli"
    directory.mkdir(parents=True)
    (directory / "config.yaml").write_text(
        "jira:\n  base_url: https://jira.example.test/jira\n"
    )
    (directory / ".env").write_text("JIRA_PAT=fixture-pat-never-print\n")
    monkeypatch.chdir(tmp_path)
    return directory


@pytest.fixture(autouse=True)
def network(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], list[httpx.Request]]:
    """Every HTTP request stays in-process; tests cannot access a live Jira."""
    routes: dict[str, Any] = {
        "serverInfo": {"version": "10.3.0", "deploymentType": "Data Center"},
        "myself": {"name": "fixture-user"},
        "project/WWRAP": {"key": "WWRAP"},
        "issue/WWRAP-1": {
            "key": "WWRAP-1",
            "fields": {
                "summary": "Fixture issue",
                "description": "fixture-pat-never-print",
                "attachment": [],
            },
        },
        "issue/WWRAP-1/transitions": {"transitions": []},
        "issue/createmeta/WWRAP/issuetypes": {
            "values": [{"id": "10001", "name": "Story"}],
            "isLast": True,
        },
        "issue/createmeta/WWRAP/issuetypes/10001": {
            "values": [{"fieldId": "summary", "required": True}],
            "isLast": True,
        },
    }
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer fixture-pat-never-print"
        path = request.url.path.removeprefix("/jira/rest/api/2/")
        if path == "search":
            start = int(request.url.params["startAt"])
            return httpx.Response(
                200,
                json={
                    "startAt": start,
                    "total": 2,
                    "issues": [{"key": f"WWRAP-{start + 1}"}],
                },
            )
        route = path if request.method == "GET" else request.method + " " + path
        value = routes.get(route, httpx.Response(404, text="fixture-pat-never-print"))
        if callable(value):
            response = value(request)
            assert isinstance(response, httpx.Response)
            return response
        if isinstance(value, Exception):
            raise value
        return (
            value
            if isinstance(value, httpx.Response)
            else httpx.Response(200, json=value)
        )

    original = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original(**kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    return routes, requests
