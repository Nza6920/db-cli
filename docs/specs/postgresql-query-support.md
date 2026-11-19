# 支持 PostgreSQL 只读查询并保持 MySQL 兼容

## Problem Statement

使用者目前只能通过本工具连接 MySQL，无法用既有 profile、只读防护和结构化输出流程查询 PostgreSQL。仅更换连接驱动不足以解决 SQL 方言、只读会话、TLS、超时和结果类型差异。使用者需要在同一 CLI 中明确选择目标数据库，同时保持现有 MySQL 配置及自动化调用兼容。

## Solution

为现有 profile 增加 PostgreSQL 支持，覆盖 query、profiles 和 validate --connect。省略 driver 时继续使用 MySQL；PostgreSQL 显式选择 driver，并提供包含数据库名的单主机连接 URL。执行用户提供的原生 SQL，在 SQL 校验和已启用并确认的只读会话双重约束下返回既有 JSON、CSV、table 输出。

## User Stories

1. As an existing MySQL user, I want omitted driver settings to default to MySQL, so that my existing profiles continue working.
2. As a PostgreSQL user, I want to select PostgreSQL explicitly in a profile, so that the CLI connects through the correct backend.
3. As an operator, I want mismatched driver and URL settings rejected, so that configuration errors cannot select an unintended backend.
4. As a PostgreSQL user, I want the target database name required, so that the tool never guesses which database to access.
5. As an existing MySQL user, I want database names to remain optional, so that existing server-level connection configurations remain valid.
6. As an operator, I want credentials supplied through the existing username and password environment-variable configuration, so that passwords stay out of connection URLs.
7. As an operator, I want profiles to work for both databases, so that I can use the same configuration discovery workflow.
8. As an operator, I want validate --connect to establish and verify a read-only session, so that a successful check reflects the query prerequisite.
9. As a production operator, I want existing exact-profile confirmation retained, so that PostgreSQL follows the same production access workflow.
10. As a query author, I want to submit native PostgreSQL SQL unchanged, so that its meaning is not altered by translation or rewriting.
11. As a query author, I want SELECT and read-only WITH queries, so that I can perform ordinary analysis and compose queries.
12. As an operator, I want SHOW and non-executing EXPLAIN, so that I can inspect settings and plans within the supported read-only scope.
13. As an operator, I want writes, writable CTEs, locking queries and EXPLAIN ANALYZE rejected, so that unsupported operations cannot pass SQL validation.
14. As a query author, I want PostgreSQL quoting and comments interpreted correctly, so that text inside literals cannot bypass policy or cause incorrect rejection.
15. As an operator, I want profile-specific LIMIT rules retained, so that accepted queries follow the same row-limit policy across backends.
16. As a query author, I want existing aggregate and other supported LIMIT exemptions preserved, so that qualifying read-only queries remain usable.
17. As an operator, I want TLS required to verify both certificates and hostnames, so that its security meaning remains consistent.
18. As an operator, I want a custom CA file supported, so that I can connect to servers using a private certificate authority.
19. As an operator, I want a PostgreSQL server-side statement timeout, so that long-running statements are cancelled according to the configured query timeout.
20. As a MySQL user, I want existing timeout behavior retained, so that PostgreSQL support does not silently change my runtime contract.
21. As an installer, I want PostgreSQL support available as an optional dependency, so that MySQL-only installations need no new driver.
22. As a PostgreSQL user, I want actionable missing-driver errors, so that I can install the required extra without debugging an import traceback.
23. As an automation author, I want booleans, timezone-aware times, UUIDs, JSON and arrays encoded successfully, so that ordinary PostgreSQL results remain machine-readable.
24. As a CSV or table consumer, I want JSON and arrays displayed as compact JSON text, so that complex values have a consistent representation.
25. As an existing consumer, I want MySQL result encoding preserved, so that downstream integrations remain compatible.
26. As an operator, I want connection, authentication, TLS and timeout failures reported through the existing structured error interface, so that automation can handle them consistently.
27. As a maintainer, I want tests against PostgreSQL 16, 17 and 18 plus MySQL regressions, so that the declared support has runtime evidence.
28. As a user, I want installation, configuration, safety and timeout differences documented in both READMEs, so that I can configure the feature correctly.

## Implementation Decisions

- Extend profile configuration with driver values mysql and postgresql. Omitted driver means mysql; reject unknown values and driver/URL mismatches.
- PostgreSQL connections use a single TCP host and an optional port in a postgresql URL. The database name is mandatory and a missing name fails configuration validation before connection. Preserve MySQL's optional database behavior. Do not infer a PostgreSQL database from the username or environment or try fallback databases.
- Continue using username and password_env. Reject embedded URL credentials. Preserve production-profile confirmation and credential redaction.
- Introduce backend dispatch behind the existing CLI commands. Keep SQL policy in the safety layer, connection/session behavior in backend runners, and value representation in the encoding/formatting layer.
- Treat a read-only session as one whose read-only transaction mode has been enabled and confirmed. Both connection validation and query execution require this state; setup or verification failure must prevent user SQL execution. Keep SQL validation independent of this runtime protection.
- Apply PostgreSQL-aware SQL validation. Accept SELECT, read-only WITH, SHOW and EXPLAIN without execution. Reject multiple statements, writes, DDL, writable CTEs, locking queries, EXPLAIN ANALYZE and psql meta-commands. Handle PostgreSQL literal, identifier and comment forms so their contents cannot be mistaken for executable policy tokens.
- Allow ordinary query functions and reject known dangerous calls that modify session state or sequences or acquire advisory locks. Database permissions constrain arbitrary user-defined functions; static validation does not prove the absence of every internal or external side effect.
- Preserve profile-specific max_rows policy: ordinary table queries need an outer integer LIMIT within the configured cap. Carry over applicable existing exemptions, including qualifying aggregate queries. Do not accept FETCH FIRST as a substitute in this release. Execute accepted SQL unchanged; do not insert a LIMIT. This policy is not a universal output-row hard cap.
- Preserve required, preferred and disabled TLS settings. PostgreSQL required uses certificate and hostname verification equivalent to verify-full, with custom CA file support. Do not weaken required to encryption-only behavior. Retain connection-check reporting of TLS state.
- For PostgreSQL, query_timeout_seconds controls a server-side statement timeout. Preserve MySQL socket timeout behavior and document the distinction. Neither is advertised as an end-to-end deadline covering connection, network transfer and local output.
- Package the PostgreSQL driver in the postgresql installation extra. Missing optional support produces an actionable structured error when PostgreSQL is selected, without breaking MySQL-only operation. The concrete driver is an implementation choice, subject to the accepted contracts.
- Preserve existing output envelopes and MySQL encoding. PostgreSQL JSON results represent booleans as booleans, timezone-aware time values as strings with offsets, UUIDs as strings, and JSON/JSONB and arrays as JSON objects/arrays. SQL NULL and JSON null both become JSON null. Do not add type wrappers.
- In CSV/table, render JSON and arrays as compact JSON text. Preserve existing handling of shared scalar types and duplicate-column rejection.
- Update both READMEs and configuration examples with driver defaults, mandatory PostgreSQL database names, optional installation, TLS, timeout differences, supported SQL and type behavior.

## Testing Decisions

Confirmed test seams:

- Use the existing CLI subprocess boundary as the primary acceptance seam. Exercise public commands, exit status, stdout/stderr, structured errors and serialized results. Avoid asserting internal helper decomposition or private call sequences.
- Extend the existing simulated database-driver boundary for fast deterministic CLI tests. Cover profile defaults and errors, missing optional driver, production confirmation, rejected SQL before database access, unchanged accepted SQL, read-only setup/verification failure, error mapping and output encoding. The existing CLI tests and simulated MySQL driver are the prior art.
- Use the same CLI-facing acceptance boundary with disposable real PostgreSQL 16, 17 and 18 containers for behavior that stubs cannot establish: authentication, actual read-only transaction enforcement, TLS certificate and hostname verification, custom CA, statement timeout and real PostgreSQL result types. Reuse the disposable MySQL integration-test approach. No live production or staging database is needed.
- Verify the two independent read-only protections: unsafe statements are rejected at the CLI boundary before database access, and database-enforced read-only state blocks writes attempted inside a suitable test function that passes ordinary query syntax.
- Cover PostgreSQL dollar-quoted strings, escaped literals, quoted identifiers and nested comments; distinguish policy keywords inside text from executable SQL. Include writable CTE, locking, multi-statement and dangerous-function regressions.
- Cover ordinary LIMIT acceptance/rejection, profile-specific caps and applicable existing exemptions; verify FETCH FIRST does not replace the required LIMIT. Test successful accepted SQL without rewriting.
- Cover boolean, timezone-aware values, UUID, JSON/JSONB, arrays, nested values, nulls, common shared scalars and duplicate columns across JSON/CSV/table; preserve MySQL's existing representations.
- Run focused checks during implementation, then type checking and the complete unit suite. Run PostgreSQL version-matrix integration tests and the existing MySQL integration suite. Report skipped integration tests separately; skipped tests do not establish support for that server version.

## Out of Scope

- Writes, DDL, schema migrations and SQL dialect translation.
- psql meta-commands, EXPLAIN ANALYZE, writable CTEs and locking queries.
- FETCH FIRST as an alternative to the initial LIMIT policy.
- Unix-domain sockets, multi-host failover and credentials embedded in URLs.
- Implicit PostgreSQL database selection or automatic fallback.
- Automatic LIMIT insertion, a new universal row-count cap or a total end-to-end timeout guarantee.
- Complete static analysis of arbitrary user-defined function behavior.
- New output type wrappers that distinguish SQL NULL from JSON null.
- Changing existing MySQL configuration, SQL-policy or result contracts beyond the compatible backend selection extension.
- Deployment or queries against production/staging databases.

## Further Notes

- Final database-name decision: PostgreSQL requires a database name. This supersedes the earlier proposal to allow omission.
- The specification captures the completed design discussion. The test seams have been confirmed. Implementation has not started.
- PostgreSQL documentation confirms the distinctions used here: [read-only transactions](https://www.postgresql.org/docs/current/sql-set-transaction.html), [TLS verification](https://www.postgresql.org/docs/current/libpq-ssl.html), and [statement timeout](https://www.postgresql.org/docs/current/runtime-config-client.html).
