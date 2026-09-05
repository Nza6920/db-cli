# Repository Guidelines

## Project Structure

`src/db_query/` contains the CLI, profile configuration, SQL safety checks,
MySQL runner, and result encoding/formatting. Keep SQL policy in the safety
module and connection behavior in the runner. `tests/` covers configuration,
CLI behavior, and disposable MySQL integration. Specifications: `docs/specs/`. Configuration examples: `config.example.toml`.

## Development and Testing

Use Python 3.11+ and install the project in a virtual environment:

```bash
python3 -m pip install -e .
db-query --help
python3 -m unittest discover -s tests -p test_cli.py -v
python3 -m unittest discover -s tests -v
DB_QUERY_RUN_MYSQL_INTEGRATION=1 python3 -m unittest discover -s tests -p test_mysql_integration.py -v
```

The final command requires working Docker access and creates disposable local
MySQL containers. Report skipped tests separately from passing tests.

Name tests `test_*`. Prefer the existing CLI subprocess and simulated database
driver boundary for SQL-policy regressions: verify structured errors, rejection
before database access, and unchanged SQL delivery. Run focused tests during
implementation and the full suite before completion.

## Coding and Contribution Style

Follow existing four-space indentation, type annotations, `snake_case` functions,
and `PascalCase` classes. No formatter or typechecker is configured in the repo.
Use imperative commit subjects.
PR descriptions should explain behavior changes, reference issues, and report
validation. Update both READMEs when user-facing behavior changes.

## SQL and Credentials

Preserve read-only guards and profile-specific `max_rows` behavior; consult the
README before changing exemptions. Keep credentials in the configured environment
variables. Use local fixtures for tests. For database investigations, read
`.agents/skills/db-query/SKILL.md` before execution.

<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tool** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them, including dynamic-dispatch hops grep can't follow. Name a file or symbol in the query to read its current line-numbered source. If it's listed but deferred, load it by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` prints the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->

## Agent skills

- **Issues and specs:** read `docs/agents/issue-tracker.md` before tracker operations.
- **Triage:** read `docs/agents/triage-labels.md` before categorizing or changing states.
- **Domain:** read `docs/agents/domain.md` before code exploration; it defines when
  to consult the glossary and ADRs.
