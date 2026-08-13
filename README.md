# db-query

English | [简体中文](README.zh-CN.md)

`db-query` is a profile-aware, read-only MySQL wrapper around
[`usql`](https://github.com/xo/usql). It accepts MySQL or JDBC MySQL URLs while
keeping passwords out of persistent configuration files and process arguments.

## Install

Install `usql` first, then install this CLI from the repository root:

```bash
pipx install ./db-query
db-query --help
```

## Configure

Copy [`config.example.toml`](config.example.toml) to
`${XDG_CONFIG_HOME:-~/.config}/db-query/config.toml`, edit its profiles, and
export each profile's password in the named environment variable:

```bash
export DB_QUERY_PROD_PASSWORD='<password>'
db-query profiles
db-query validate
```

Configuration lookup order is `--config`, `DB_QUERY_CONFIG`,
`XDG_CONFIG_HOME/db-query/config.toml`, then `~/.config/db-query/config.toml`.
File ownership, symlink, and broad-permission findings are warnings. Plaintext
`password` and `pass` fields are rejected.

Supported JDBC URL options are `connectTimeout`, `socketTimeout`, and `useSSL`.
Explicit profile timeout and TLS fields take precedence, followed by JDBC URL
options, then the 5-second connection and 30-second query defaults. Unknown
JDBC options are rejected instead of being silently ignored.

TLS defaults to `required`, including for production. A production profile may
explicitly select `preferred` or `disabled`; `db-query` allows it but emits a
warning because database traffic may be unencrypted.

`db-query` writes a complete, URL-encoded DSN to a temporary `0600` usql
configuration. This preserves the case-sensitive `readTimeout` and
`writeTimeout` MySQL driver options. The temporary directory is removed after
the command finishes.

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

The wrapper adds a terminating semicolon when one is absent because
`usql --file` otherwise exits successfully without executing the buffered
statement.

Production queries require an exact, explicit confirmation flag after the SQL
has been reviewed:

```bash
db-query query --profile prod --confirm-profile prod --stdin
```

Use `--format table` or `--format csv` for human-readable passthrough output.
JSON is the default and includes profile, environment, duration, columns,
row count, and rows.

The safety scanner accepts one `SELECT`, read-only `WITH`, `SHOW`,
`DESC`/`DESCRIBE`, or `EXPLAIN` statement. Detail queries require an outer,
literal `LIMIT` no greater than 1000. It rejects writes, usql meta-commands,
multiple statements, export clauses, locking reads, advisory locks, and MySQL
executable comments. Constant queries without a top-level `FROM`, such as
`SELECT 1`, do not require `LIMIT`. Use a database account whose grants are
read-only: client validation is defense in depth, not a database authorization
boundary.

Warnings are scoped to the profiles used by the current command. Listing all
profiles may therefore show warnings that a single-profile query does not.

Validate configuration offline by default, or explicitly test one connection:

```bash
db-query validate --profile uat --connect
db-query validate --profile prod --connect --confirm-profile prod
```

## Develop

```bash
cd db-query
python3 -m unittest discover -s tests -v
```
