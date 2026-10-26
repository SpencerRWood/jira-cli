# Repository guidance

Follow root [tool.wood.branching]: working branches start from development and Story PRs target development. Protect main and development through the separately reviewed Wood enforcement plan and verify readback.

Reviewed release PRs target main after accumulated validation. Development never publishes or deploys. Hotfixes need incident and approval references and verified main back-integration before release review.

Retain application guidance and use wood repo validate --json.

## Approved baseline guidance

## Development lifecycle

Use root [tool.wood.branching]: feature branches and Story PRs start from and target development. Development validates without publication/deployment. Main requires a separately approved release PR and pinned release workflow. Emergency hotfixes require incident/approval references and reconciliation into development.
