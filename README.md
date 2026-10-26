# Jira CLI

A local-first Windows CLI for Jira Data Center. This repository is being
bootstrapped from the existing `template-python-library` template; application
commands are not implemented in this baseline.

## Planning

OpenProject project: [Jira CLI](https://projects.woodhost.cloud/projects/jira-cli)
(ID 9). Planning Increment: R1 — Jira CLI Initial Release (Version ID 28).
Owning Initiative: [Jira CLI](https://projects.woodhost.cloud/work_packages/570)
(ID 570).
First Story: [S01 — CLI Foundation and Jira Data Center Client](https://projects.woodhost.cloud/work_packages/565).

[Approved implementation workbook](https://docs.google.com/spreadsheets/d/1I8nfLlJfabjrG7c9QThGA1X3Xf1RvKf_uTAmzkTFud8/edit).
[Requirements](https://docs.google.com/document/d/1vuodIc88J8FoNRge8-VOiwapaIwMXtVpAjK8FlyYl6k/edit).

R1 is a Planning Increment, independent of repository semantic versions. The
initial package version is an unreleased placeholder. Released In remains blank.

## Development

Python 3.14 and uv are inherited from the template baseline. Use the typed
`src/jira_cli` layout and retain strict typing and the template quality gates.

```sh
uv sync --frozen --group dev
wood repo validate --json
uv build
```

Story branches start from development and target development. Development merges
do not authorize publication. Accumulated validation and explicit release approval
precede development → protected main integration and its pinned release workflow.
Remote creation, branch protection, and first publication are separate gates;
the prepared local scaffold does not attest to any of them.

## Acceptance boundary

Development acceptance uses mocked Jira Data Center responses and Windows package
checks. Live PAT authentication, WWRAP access, and authorized Jira reads and writes
remain a separate corporate-network validation gate. Mocked tests do not verify
live Jira integration. Never store credentials in the repository.

## Template provenance

Source: `SpencerRWood/template-python-library`, fetched `origin/main` revision
`216687b41193bc1cb215a21cd93117a03eaca447` on 2026-10-26. The source template
checkout and its local commits are preserved.
