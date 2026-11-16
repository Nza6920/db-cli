# db-cli

English | [简体中文](README.zh-CN.md)

`db-cli` provides the profile-aware, read-only `db-query` command for MySQL.
Version 0.3.1 connects directly through pinned Python dependencies, so a normal
pipx installation does not require a separate SQL client executable. It accepts
MySQL and JDBC MySQL URLs and keeps passwords out of persistent configuration.

## Capabilities

- Select one named development, test, staging, or production profile per query.
- Accept `mysql://` and `jdbc:mysql://` URLs, including IPv6 hosts and
  percent-encoded database names.
- Read passwords only from profile-specific environment variables and reject
  plaintext `password` or `pass` fields.
- Allow one `SELECT`, read-only `WITH`, `SHOW`, `DESC`/`DESCRIBE`, or `EXPLAIN`
  statement while rejecting writes, multiple statements, client meta-commands,
  exports, locking reads, advisory locks, and executable comments.
- Require an outer literal `LIMIT` for non-exempt detail and combined queries,
  bounded by the selected profile's `max_rows` (default 1000).
- Require an exact `--confirm-profile` match for production queries and
  production connection tests.
- Open every connection with autocommit enabled, local infile disabled, and a
  session transaction mode that is set to and verified as read-only before SQL
  executes.
- Support `required`, `preferred`, and `disabled` TLS modes and expose the
  negotiated state from `validate --connect` as `tls_active`.
- Return stable JSON by default, standard CSV, or SQL-style tables.
- Return structured errors for configuration, credentials, connection, query,
  socket timeout, and result encoding failures.

## Install

Install the fixed GitHub release with pipx. PyMySQL, its RSA support, and
tabulate are installed with the application; `usql` is not required.

```bash
pipx install "git+https://github.com/Nza6920/db-cli.git@v0.3.1"
db-query --version
db-query --help
```

For a local checkout under development, use `pipx install --force .` from this
repository root.

## Compatibility and rollback

| db-query | Execution and formatting dependencies | Runtime SQL client |
| --- | --- | --- |
| v0.1.2 | usql 0.21.4 | separately installed `usql` required |
| v0.2.0 | PyMySQL 1.2.0, tabulate 0.10.0 | none |
| v0.2.1 | PyMySQL 1.2.0, tabulate 0.10.0 | none |
| v0.3.0 | PyMySQL 1.2.0, tabulate 0.10.0 | none |
| v0.3.1 | PyMySQL 1.2.0, tabulate 0.10.0 | none |

To roll back the Windows configuration-path changes, install v0.2.0:

```bash
pipx install --force "git+https://github.com/Nza6920/db-cli.git@v0.2.0"
```

To restore the legacy usql runtime, install the fixed v0.1.2 tag and usql
0.21.4:

```bash
pipx install --force "git+https://github.com/Nza6920/db-cli.git@v0.1.2"
```

## Configure

On Linux and macOS, copy [`config.example.toml`](config.example.toml) to
`${XDG_CONFIG_HOME:-~/.config}/db-cli/config.toml`. On Windows, the default is
`%APPDATA%\db-cli\config.toml`. Edit its profiles, then provide each password
through the environment variable named by its profile.

Bash:

```bash
export DB_QUERY_PROD_PASSWORD='<password>'
db-query profiles
db-query validate
```

PowerShell:

```powershell
$configPath = Join-Path $env:APPDATA 'db-cli\config.toml'
New-Item -ItemType Directory -Force (Split-Path $configPath) | Out-Null
Copy-Item .\config.example.toml $configPath
$env:DB_QUERY_PROD_PASSWORD = '<password>'
db-query profiles
db-query validate
```

Configuration lookup order is `--config`, `DB_QUERY_CONFIG`,
`XDG_CONFIG_HOME/db-cli/config.toml`, then the platform default above. If
`APPDATA` is unavailable on Windows, the fallback is
`%USERPROFILE%\AppData\Roaming\db-cli\config.toml`. The legacy Windows path
`%USERPROFILE%\.config\db-cli\config.toml` is not searched automatically; move
the file or select it with `DB_QUERY_CONFIG`, `XDG_CONFIG_HOME`, or `--config`.
Symlinks produce warnings on every platform. Ownership and broad POSIX-mode
findings are warnings on POSIX systems only; this command does not audit
Windows ACLs. Plaintext password fields are rejected. TOML must be UTF-8; when
using Windows PowerShell 5.1, preserve the example file's encoding or use an
editor that saves UTF-8.

Supported JDBC URL options are `connectTimeout`, `socketTimeout`, and `useSSL`.
Explicit profile timeout and TLS fields take precedence, followed by JDBC URL
options, then the five-second connection and 30-second query defaults. Unknown
JDBC options are rejected. Connection timeout maps to the driver connection
timeout; query timeout maps to its read and write socket timeouts. A
`QUERY_TIMEOUT` therefore means a driver/socket timeout, not a strict total
wall-clock deadline.

TLS defaults to `required`, which validates the system trust chain and hostname.
`preferred` uses TLS when the server offers it and permits plaintext fallback
only when TLS is unavailable. `disabled` prohibits TLS. A production profile
may explicitly choose a weaker mode, but the command emits a scoped warning.

## PostgreSQL queries (development checkout)

Install the optional driver from this checkout with `pip install '.[postgresql]'`
(or `pipx install --force '.[postgresql]'`). MySQL-only installations need no
PostgreSQL driver. Selecting PostgreSQL without the extra returns a structured
`DRIVER_NOT_INSTALLED` error with installation instructions.

Profiles default to `driver = "mysql"` when omitted. Set `driver = "postgresql"`
explicitly with a `postgresql://host[:port]/database` URL; port defaults to 5432
and the database is mandatory. Driver/URL mismatches, embedded credentials,
Unix sockets, multiple hosts and URL options are rejected. MySQL database names
remain optional. Both backends use `username` and `password_env`.

```toml
[profiles.pg]
driver = "postgresql"
url = "postgresql://db.example.cn:5432/analytics"
username = "readonly_user"
password_env = "DB_QUERY_PG_PASSWORD"
environment = "staging"
tls = "required"
# Optional PostgreSQL CA bundle for private certificates:
# tls_ca_file = "/path/to/ca.pem"
```

`profiles`, offline `validate`, `validate --profile pg --connect`, and `query`
support PostgreSQL profiles. PostgreSQL 16, 17 and 18 are integration-test targets;
skipped container tests do not establish runtime support. Connection checks and
queries enable and verify a read-only session. Connection checks report actual
`tls_active`; queries retain exact production-profile confirmation.

PostgreSQL accepts native `SELECT`, read-only `WITH` (including recursive,
nested and materialized CTEs), `SHOW`, and nonexecuting `EXPLAIN`. Accepted SQL
is delivered unchanged. Table and set queries require an outer integer
`LIMIT` within `max_rows`; `LIMIT ALL`, expressions, comma syntax and `FETCH FIRST`
are not substitutes. Simple aggregate projections and queries without outer
`FROM` retain their exemptions; this is not a universal output-row hard cap.
Dollar strings, escape strings, quoted identifiers and nested comments are
recognized. CTE bodies do not need independent row limits; their final query
follows the outer-result policy. `SHOW` and `EXPLAIN` are exempt from row limits.
`EXPLAIN` still validates the wrapped query for writes, locks and dangerous calls.
Every explicit `ANALYZE`/`ANALYSE` option (including false), `SERIALIZE`, duplicate
or unknown EXPLAIN options fails closed. Supported options are `VERBOSE`, `COSTS`,
`SETTINGS`, `BUFFERS`, `WAL`, `TIMING`, `SUMMARY`, `GENERIC_PLAN`, `MEMORY` and
`FORMAT TEXT/XML/JSON/YAML`; option availability also depends on server version.
Unicode escaped identifiers and escape-string continuations fail
closed. Multiple statements, writes, DDL, row locks, psql commands and known
session/sequence/advisory-lock or administrative mutation functions are rejected
before driver access, including quoted/schema-qualified function calls. Arbitrary
user-defined function side effects still depend on database privileges.

Integer, text, Decimal, naive date/time, binary and NULL results use the existing
JSON/CSV/table envelopes and representations; duplicate columns are rejected.
PostgreSQL booleans remain JSON booleans, timezone-aware datetime/time values use
ISO 8601 strings with offsets, and UUIDs become strings. JSON/JSONB and arrays
retain objects/arrays, including nested values; array elements use the same
scalar rules. SQL NULL and JSON null both become JSON null without type wrappers.
CSV/table render objects and arrays as compact JSON text. Unsupported values or
object keys that are not strings fail with `RESULT_ENCODING_FAILED`.

PostgreSQL TLS `required` uses certificate and hostname verification (`verify-full`)
with system trust by default, or `tls_ca_file` for a private CA. `preferred` requests
TLS with plaintext fallback, without certificate verification; `disabled` prohibits
TLS. MySQL TLS behavior is unchanged. `connect_timeout_seconds` controls connection
establishment. PostgreSQL `query_timeout_seconds` sets the server-side
`statement_timeout` in milliseconds, with cancellation reported as `QUERY_TIMEOUT`.
MySQL `query_timeout_seconds` continues to control read/write socket timeouts.
Neither setting is an end-to-end deadline.

## Query

Pass agent-generated SQL over stdin so it does not appear in process arguments:

```bash
db-query query --profile uat --stdin <<'SQL'
SELECT id, status
FROM logistics.t_waybill
ORDER BY id DESC
LIMIT 20;
SQL
```

PowerShell can send the same SQL through a here-string:

```powershell
@'
SELECT id, status
FROM logistics.t_waybill
ORDER BY id DESC
LIMIT 20;
'@ | db-query query --profile uat --stdin
```

Production queries require review of the exact profile and SQL followed by an
exact confirmation:

```bash
db-query query --profile prod --confirm-profile prod --stdin
```

JSON is the default and includes profile, environment, duration, ordered
columns, row count, and rows. `--format csv` uses standard CSV quoting, renders
null as `\N`, and retains an empty string as an empty field. `--format table`
uses a SQL-style `psql` layout, renders null as `NULL`, and disables numeric
parsing so leading-zero and precision-preserving strings stay unchanged.

Across all formats, Decimal values are strings; integers and MySQL boolean
aliases remain JSON numbers; dates use `YYYY-MM-DD`; naive datetimes use
timezone-free ISO 8601; signed MySQL times retain their sign and microseconds; and binary data
uses reversible lowercase `0x`-prefixed hexadecimal. Duplicate result column
names fail with `RESULT_ENCODING_FAILED`; provide unique SQL aliases. There is
no total output-byte limit, so avoid selecting large binary fields even though
detail row counts are bounded.

Each profile can set `max_rows = 2000` in its TOML table. This is the maximum
allowed SQL LIMIT count, not an automatically inserted LIMIT. It defaults to
1000 and accepts any positive integer, smaller or larger than the default;
booleans, strings, floats, zero, and negative values are configuration errors.
Profiles have independent limits; there is no global, environment-variable, or
CLI override. An over-limit error reports the effective profile limit.

The safety scanner accepts one supported read-only statement. Detail reads need
an outer literal `LIMIT`; both `LIMIT count OFFSET offset` and `LIMIT offset,count`
check the count against `max_rows`. Only simple projections consisting entirely
of `COUNT`, `SUM`, `AVG`, `MIN`, or `MAX` calls are exempt as single-row aggregates;
aliases and multiple aggregate calls are allowed. Grouped queries, window
functions, mixed projections, and expressions wrapping aggregate calls require
LIMIT. An identifier named `max` does not grant an exemption.

UNION and other recognized set operations require a LIMIT on the whole result,
including when branches are constants or aggregates. A branch, CTE, or subquery
LIMIT does not substitute for that outer limit. Parentheses enclosing the whole
query are supported; ambiguous scope is rejected with a structured error. SQL
is never rewritten. Supply an explicit, recognizable outer LIMIT when needed.

`SHOW`, `DESC`/`DESCRIBE`, `EXPLAIN`, and simple constant queries without a top-level
`FROM`, such as `SELECT 1`, retain their exemptions. This is not a universal
output cap or a bound on database scans or computation, and there is no runtime
row-count fallback. Use a database account whose grants are read-only: static
scanning and session read-only mode are defense in depth, not replacements for
database authorization.

Validate configuration offline by default, or explicitly test one connection:

```bash
db-query validate --profile uat --connect
db-query validate --profile prod --connect --confirm-profile prod
```

## Skill

The explicitly invoked `$db-query` skill lives at
[`.agents/skills/db-query/SKILL.md`](.agents/skills/db-query/SKILL.md):

```text
$db-query use the prod profile to inspect the latest 20 waybills for project 252143
```

It selects one profile and executes one bounded read-only statement at a time.
For production it shows the exact profile and SQL and waits for explicit
approval of that unchanged pair. Every profile or SQL change requires fresh
approval. Returned database evidence stays separate from inference, and write
execution remains outside the skill boundary.

To use the skill elsewhere, install or link `.agents/skills/db-query` into that
repository's skills directory and ensure only `db-query` is available.

## Develop

Use Python 3.11+ and install the development dependencies in a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev,postgresql]'
python3 -m mypy
python3 -m unittest discover -s tests -v
DB_QUERY_RUN_MYSQL_INTEGRATION=1 python3 -m unittest discover -s tests -p test_mysql_integration.py -v
DB_QUERY_RUN_POSTGRESQL_INTEGRATION=1 python3 -m unittest discover -s tests -p test_postgresql_integration.py -v
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

Mypy checks `src/db_query` and `tests`, including functions without type
annotations. Its version and configuration live in `pyproject.toml`; the
configuration scopes its missing-import exemption to PyMySQL. GitHub Actions runs
the same type check on pushes and pull requests using Python 3.11.

The integration suites create disposable local MySQL and PostgreSQL 16/17/18
containers and never access UAT or production. PostgreSQL tests require Docker and
OpenSSL, generate disposable CA/server certificates, and cover trusted TLS, bad CA,
hostname mismatch, authentication, and read-only connection initialization. Report
skipped integration tests separately; a skip does not establish version support.
