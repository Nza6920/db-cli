# db-cli

English | [简体中文](README.zh-CN.md)

`db-cli` provides the profile-aware, read-only `db-query` command for MySQL.
Version 0.2.1 connects directly through pinned Python dependencies, so a normal
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
pipx install "git+https://github.com/Nza6920/db-cli.git@v0.2.1"
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
aliases remain JSON numbers; dates use `YYYY-MM-DD`; datetimes use timezone-free
ISO 8601; signed MySQL times retain their sign and microseconds; and binary data
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

```bash
python3 -m unittest discover -s tests -v
DB_QUERY_RUN_MYSQL_INTEGRATION=1 python3 -m unittest tests.test_mysql_integration -v
```

The integration suite creates disposable local MySQL containers and never
accesses UAT or production.
