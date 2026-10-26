import tomllib
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_package_can_be_imported() -> None:
    package = import_module("jira_cli")

    assert package.__doc__ == "Local-first Jira Data Center command-line application."


def test_project_metadata_describes_baseline() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]

    assert project["name"] == "jira-cli"
    assert project["description"] == "A local-first Windows CLI for Jira Data Center."
    assert project["requires-python"] == ">=3.14"
    assert project["dependencies"] == []


def test_project_declares_typed_src_package() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]
    tool = pyproject["tool"]

    assert (ROOT / "src" / "jira_cli" / "py.typed").is_file()
    assert "Typing :: Typed" in project["classifiers"]
    assert tool["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/jira_cli",
    ]
