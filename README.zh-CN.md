# db-query

[English](README.md) | 简体中文

`db-query` 是基于 [`usql`](https://github.com/xo/usql) 封装的、支持 profile
的只读 MySQL 查询工具。它接受 MySQL URL 或 JDBC MySQL URL，同时避免密码
出现在持久化配置文件和进程参数中。

## 已实现能力

- **多环境配置**：支持 development、test、staging 和 production 命名
  profiles，每次查询都需要显式选择 profile。
- **MySQL 与 JDBC MySQL URL**：接受 `mysql://` 和 `jdbc:mysql://` URL，
  支持 IPv6 host 和经过 percent-encode 的 database name。
- **JDBC 参数转换**：将 `connectTimeout`、`socketTimeout` 和 `useSSL`
  转换为 Go MySQL driver 所需的、区分大小写的参数。
- **环境变量凭据**：每个 profile 通过独立环境变量读取密码；配置中出现
  明文 `password` 或 `pass` 字段时会被拒绝。
- **只读 SQL guardrails**：只允许一条 `SELECT`、只读 `WITH`、`SHOW`、
  `DESC`/`DESCRIBE` 或 `EXPLAIN`；拒绝写操作、多语句、meta-command、
  导出、锁定读、advisory lock 和 MySQL executable comment。
- **限制明细查询规模**：包含顶层 `FROM` 的非聚合查询必须使用最外层
  字面量 `LIMIT`，且不得超过 1000。
- **production 二次确认**：production 查询及连接测试必须提供完全匹配的
  `--confirm-profile`。
- **TLS 策略**：支持 `required`、`preferred` 和 `disabled`；默认使用
  `required`，production 关闭或降级 TLS 时会输出与当前 profile 相关的警告。
- **超时保护**：支持配置连接和查询超时，配置硬上限为 120 秒。
- **结构化输出**：默认返回 JSON，同时支持适合终端使用的 table 和 CSV
  passthrough 输出。
- **离线与在线校验**：可在不访问网络的情况下校验 profiles，也可通过
  `validate --connect` 显式测试一个连接。
- **凭据安全的 usql 执行**：SQL 和 URL-encoded usql DSN 写入临时 `0600`
  文件；错误信息会脱敏，执行结束后自动删除临时文件。
- **稳定的自动化错误**：针对配置、凭据、连接、查询、超时以及缺少 `usql`
  等失败提供结构化 error code 和不同 exit code。

## 安装

先安装 `usql`，然后在仓库根目录安装此 CLI：

```bash
pipx install ./db-query
db-query --help
```

## 配置

将 [`config.example.toml`](config.example.toml) 复制到
`${XDG_CONFIG_HOME:-~/.config}/db-query/config.toml`，编辑其中的 profiles，
再通过各 profile 指定的环境变量提供密码：

```bash
export DB_QUERY_PROD_PASSWORD='<password>'
db-query profiles
db-query validate
```

配置查找优先级依次为 `--config`、`DB_QUERY_CONFIG`、
`XDG_CONFIG_HOME/db-query/config.toml`、`~/.config/db-query/config.toml`。
文件所有者、符号链接及权限过宽只会产生警告。配置中出现明文 `password`
或 `pass` 字段时会被拒绝。

支持的 JDBC URL 参数为 `connectTimeout`、`socketTimeout` 和 `useSSL`。
显式 profile timeout 和 TLS 字段优先，其次采用 JDBC URL 参数，最后使用
5 秒连接超时和 30 秒查询超时的默认值。未知 JDBC 参数会被拒绝，不会被
静默忽略。

包括 production 在内，TLS 默认为 `required`。production profile 可以显式
选择 `preferred` 或 `disabled`；`db-query` 会允许执行，但会警告数据库流量
可能未加密。

`db-query` 会将完整且经过 URL encode 的 DSN 写入临时的 `0600` usql 配置，
从而保留 MySQL driver 中区分大小写的 `readTimeout` 和 `writeTimeout` 参数。
命令结束后会删除临时目录。

## 查询

通过 stdin 传入 agent 生成的 SQL，避免 SQL 出现在进程参数中：

```bash
db-query query --profile uat --stdin <<'SQL'
SELECT id, status
FROM logistics.t_waybill
ORDER BY id DESC
LIMIT 20;
SQL
```

若 SQL 末尾没有分号，wrapper 会自动补充分号；否则 `usql --file` 会在未执行
query buffer 中语句的情况下以成功状态退出。

production 查询必须先审查 SQL，再提供完全匹配的显式确认参数：

```bash
db-query query --profile prod --confirm-profile prod --stdin
```

面向人工阅读时，可以使用 `--format table` 或 `--format csv` 直接输出。
默认格式为 JSON，其中包含 profile、environment、duration、columns、
row count 和 rows。

安全扫描器只接受一条 `SELECT`、只读 `WITH`、`SHOW`、
`DESC`/`DESCRIBE` 或 `EXPLAIN` 语句。明细查询必须在最外层使用字面量
`LIMIT`，且不得超过 1000。写操作、usql meta-command、多语句、导出子句、
锁定读、advisory lock 和 MySQL executable comment 均会被拒绝。没有顶层
`FROM` 的常量查询（例如 `SELECT 1`）不要求 `LIMIT`。数据库账号
自身仍应只有只读权限：客户端校验属于 defense in depth，不能替代数据库
授权边界。

warning 只针对当前命令涉及的 profiles。列出全部 profiles 时可能看到某项
warning，而指定单个 profile 查询时不会显示无关 warning。

默认只离线校验配置；也可以显式测试指定连接：

```bash
db-query validate --profile uat --connect
db-query validate --profile prod --connect --confirm-profile prod
```

## 开发

```bash
cd db-query
python3 -m unittest discover -s tests -v
```
