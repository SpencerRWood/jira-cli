"""Jira Data Center REST v2 transport, with single-attempt writes."""

from typing import Any, BinaryIO
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from jira_cli import tls
from jira_cli.config import Config, UserError, base_url, issue_key, project_key


class WriteUncertainError(UserError):
    """A write may have succeeded; inspect Jira before any manual retry."""


class JiraClient:
    def __init__(self, config: Config, token: SecretStr) -> None:
        self.base_url = base_url(config)
        self.token = token
        self.field_metadata: list[dict[str, Any]] | None = None
        self.http = httpx.Client(
            base_url=self.base_url + "/rest/api/2/",
            headers={
                "Authorization": "Bearer " + token.get_secret_value(),
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            verify=tls.context(),
            # Explicit CA overrides and enterprise proxies remain supported.
            trust_env=True,
        )

    def close(self) -> None:
        self.http.close()

    def write(
        self,
        method: str,
        path: str,
        *,
        expected: int,
        body: dict[str, Any] | str | None = None,
        files: dict[str, tuple[str, BinaryIO, str]] | None = None,
    ) -> Any:
        try:
            response = self.http.request(
                method,
                path,
                json=body,
                files=files,
                headers={"X-Atlassian-Token": "no-check"} if files else None,
            )
        except (httpx.RequestError, OSError) as exc:
            raise WriteUncertainError(
                "Write outcome uncertain; inspect Jira before retrying. "
                "No retry was sent."
            ) from exc
        if response.status_code >= 500:
            raise WriteUncertainError(
                "Jira server error after a write; inspect Jira before retrying."
            )
        if response.status_code != expected and 200 <= response.status_code < 400:
            raise WriteUncertainError(
                "Unexpected write response; inspect Jira before retrying."
            )
        if response.status_code != expected:
            raise UserError(
                f"Jira rejected the write (HTTP {response.status_code}); "
                "check metadata and permissions. No retry was sent."
            )
        if expected == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise WriteUncertainError(
                "Write response was invalid; inspect Jira before retrying."
            ) from exc

    def get(self, path: str, params: dict[str, str | int] | None = None) -> Any:
        try:
            response = self.http.get(path, params=params)
        except httpx.RequestError as exc:
            raise UserError(
                "Jira connection failed; check corporate network, TLS, and proxy."
            ) from exc
        if response.status_code != 200:
            messages = {
                401: "PAT authentication failed.",
                403: "Jira permission denied.",
                404: "Jira resource unavailable or inaccessible.",
                429: "Jira rate limit reached; retry later.",
            }
            raise UserError(
                messages.get(
                    response.status_code,
                    f"Jira HTTP {response.status_code}; no response body was logged.",
                )
            )
        try:
            return response.json()
        except ValueError as exc:
            raise UserError(
                "Jira returned invalid JSON; check the base URL and login gateway."
            ) from exc

    def object(
        self, path: str, params: dict[str, str | int] | None = None
    ) -> dict[str, Any]:
        data = self.get(path, params)
        if not isinstance(data, dict):
            raise UserError("Jira returned an unexpected object shape.")
        return data

    def project(self, key: str) -> dict[str, Any]:
        return self.object("project/" + project_key(key))

    def issue(self, key: str) -> dict[str, Any]:
        return self.object(
            "issue/" + issue_key(key), {"fields": "*all", "expand": "names"}
        )

    def comments(self, key: str, limit: int) -> list[dict[str, Any]]:
        """Read accessible comments newest first, respecting server page caps."""
        rows: list[dict[str, Any]] = []
        start = 0
        while len(rows) < limit:
            page = self.object(
                "issue/" + issue_key(key) + "/comment",
                {
                    "startAt": start,
                    "maxResults": min(100, limit - len(rows)),
                    "orderBy": "-created",
                },
            )
            values, total = page.get("comments"), page.get("total")
            if (
                not isinstance(values, list)
                or any(not isinstance(row, dict) for row in values)
                or type(total) is not int
                or total < 0
                or page.get("startAt") != start
            ):
                raise UserError("Jira returned an invalid comment page.")
            rows.extend(values)
            start += len(values)
            if start >= total:
                break
            if not values:
                raise UserError(
                    "Jira comment pagination ended before the reported total."
                )
        return rows[:limit]

    def search(
        self,
        jql: str,
        fields: str = "summary,status,priority,assignee,issuetype,updated",
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self.object(
                "search",
                {
                    "jql": jql,
                    "startAt": start,
                    "maxResults": 100,
                    "fields": fields,
                },
            )
            rows = page.get("issues")
            total = page.get("total")
            if (
                not isinstance(rows, list)
                or any(not isinstance(row, dict) for row in rows)
                or type(total) is not int
                or total < 0
                or page.get("startAt") != start
            ):
                raise UserError("Jira returned an invalid search page.")
            issues.extend(rows)
            if len(issues) >= total:
                return issues
            if not rows:
                raise UserError("Jira pagination ended before the reported total.")
            start += len(rows)

    def fields(self, project: str, name: str) -> dict[str, Any]:
        path = "issue/createmeta/" + project_key(project) + "/issuetypes"
        matches = [t for t in self.metadata_values(path) if t.get("name") == name]
        if len(matches) != 1:
            raise UserError(
                "Issue type unavailable or ambiguous in project creation metadata."
            )
        if not matches[0].get("id"):
            raise UserError("Jira issue type metadata is missing an ID.")
        type_id = quote(str(matches[0]["id"]), safe="")
        return {
            "issueType": matches[0],
            "values": self.metadata_values(path + "/" + type_id),
        }

    def metadata_values(self, path: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self.object(path, {"startAt": start, "maxResults": 100})
            rows = page.get("values")
            if not isinstance(rows, list) or any(not isinstance(v, dict) for v in rows):
                raise UserError("Jira returned invalid metadata values.")
            values.extend(rows)
            if page.get("isLast") is True:
                return values
            total = page.get("total")
            if type(total) is int and total >= 0 and len(values) >= total:
                return values
            if not rows or page.get("startAt") != start:
                raise UserError("Jira metadata pagination is incomplete.")
            start += len(rows)

    def url(self, key: str) -> str:
        return self.base_url + "/browse/" + issue_key(key)
