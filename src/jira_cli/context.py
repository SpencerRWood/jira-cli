"""Working state stays separate from configuration, credentials, and repositories."""

import os
import tempfile
from pathlib import Path

from pydantic import Field, ValidationError

from jira_cli.config import Config, Model, UserError, issue_key, project_key


class Selection(Model):
    issue: str | None = None
    project: str | None = None


class State(Model):
    global_context: Selection = Field(default_factory=Selection)
    directories: dict[str, Selection] = Field(default_factory=dict)


class ContextStore:
    def __init__(self, directory: Path, config: Config) -> None:
        self.path = directory / "state.json"
        self.config = config.context

    def read(self) -> State:
        try:
            state = (
                State.model_validate_json(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else State()
            )
            for entry in [state.global_context, *state.directories.values()]:
                if entry.issue:
                    issue_key(entry.issue)
                if entry.project:
                    project_key(entry.project)
            return state
        except (OSError, ValidationError) as exc:
            raise UserError(
                "Invalid state.json; repair or remove the application state file."
            ) from exc

    @staticmethod
    def directory_key() -> str:
        return os.path.normcase(str(Path.cwd().resolve()))

    def selection(self, *, global_scope: bool = False) -> Selection:
        state = self.read()
        if global_scope or self.config.scope == "global":
            return state.global_context
        local = state.directories.get(self.directory_key(), Selection())
        fallback = (
            state.global_context if self.config.fallback_to_global else Selection()
        )
        return Selection(
            issue=local.issue or fallback.issue,
            project=local.project or fallback.project,
        )

    def update(self, selection: Selection, *, global_scope: bool = False) -> None:
        if not self.config.persist:
            raise UserError("Context persistence is disabled in config.yaml.")
        state = self.read()
        if global_scope or self.config.scope == "global":
            state.global_context = selection
        else:
            state.directories[self.directory_key()] = selection
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement prevents interrupted writes from corrupting the state.
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(state.model_dump_json(indent=2))
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def issue(self, explicit: str | None) -> str:
        value = explicit or self.selection().issue
        if not value:
            raise UserError("No active issue; use --issue or jira context set KEY.")
        return issue_key(value)
