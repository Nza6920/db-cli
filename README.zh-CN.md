# db-cli

[English](README.md) | 简体中文

`db-cli` 提供支持 profile 的只读 MySQL 命令 `db-query`。0.3.1 版本通过固定
版本的 Python 依赖直接连接数据库，因此正常的 pipx 安装不再需要单独的 SQL
客户端。它接受 MySQL 和 JDBC MySQL URL，并避免将密码写入持久化配置。

## 功能

- 每次查询明确选择一个 development、test、staging 或 production profile。
- 接受 `mysql://` 和 `jdbc:mysql://` URL，包括 IPv6 host 和经过
  percent-encode 的 database name。
- 密码只从 profile 指定的环境变量读取；拒绝明文 `password` 或 `pass` 字段。
- 只允许一条 `SELECT`、只读 `WITH`、`SHOW`、`DESC`/`DESCRIBE` 或
  `EXPLAIN`；拒绝写操作、多语句、客户端 meta-command、导出、锁定读、
  advisory lock 和 executable comment。
- 非豁免的明细和组合查询必须使用最外层字面量 `LIMIT`，且不超过所选
  profile 的 `max_rows`（默认 1000）。
- production 查询和 production 连接测试必须提供完全匹配的
  `--confirm-profile`。
- 每个连接启用 autocommit、禁用 local infile，并在执行 SQL 前将 session
  transaction mode 设置为只读并验证结果。
- 支持 `required`、`preferred` 和 `disabled` TLS 模式；
  `validate --connect` 通过 `tls_active` 返回实际协商状态。
- 默认返回稳定 JSON，也支持标准 CSV 和 SQL 风格 table。
- 对配置、凭据、连接、查询、socket 超时和结果编码失败返回结构化错误。

## 安装

使用 pipx 安装固定的 GitHub release。PyMySQL、RSA 支持和 tabulate 会随应用
一起安装，不需要 `usql`。

```bash
pipx install "git+https://github.com/Nza6920/db-cli.git@v0.3.1"
db-query --version
db-query --help
```

开发中的本地 checkout 可在仓库根目录执行 `pipx install --force .`。

## 兼容性与回滚

| db-query | 执行和格式化依赖 | 运行时 SQL 客户端 |
| --- | --- | --- |
| v0.1.2 | usql 0.21.4 | 需要单独安装 `usql` |
| v0.2.0 | PyMySQL 1.2.0、tabulate 0.10.0 | 无 |
| v0.2.1 | PyMySQL 1.2.0、tabulate 0.10.0 | 无 |
| v0.3.0 | PyMySQL 1.2.0、tabulate 0.10.0 | 无 |
| v0.3.1 | PyMySQL 1.2.0、tabulate 0.10.0 | 无 |

如需回滚 Windows 配置路径变更，请安装 v0.2.0：

```bash
pipx install --force "git+https://github.com/Nza6920/db-cli.git@v0.2.0"
```

如需恢复旧版 usql runtime，请安装固定的 v0.1.2 tag 和 usql 0.21.4：

```bash
pipx install --force "git+https://github.com/Nza6920/db-cli.git@v0.1.2"
```

## 配置

在 Linux 和 macOS 上，将 [`config.example.toml`](config.example.toml) 复制到
`${XDG_CONFIG_HOME:-~/.config}/db-cli/config.toml`；Windows 默认位置为
`%APPDATA%\db-cli\config.toml`。编辑 profiles 后，通过各 profile 指定的环境
变量提供密码。

Bash：

```bash
export DB_QUERY_PROD_PASSWORD='<password>'
db-query profiles
db-query validate
```

PowerShell：

```powershell
$configPath = Join-Path $env:APPDATA 'db-cli\config.toml'
New-Item -ItemType Directory -Force (Split-Path $configPath) | Out-Null
Copy-Item .\config.example.toml $configPath
$env:DB_QUERY_PROD_PASSWORD = '<password>'
db-query profiles
db-query validate
```

配置查找顺序为 `--config`、`DB_QUERY_CONFIG`、
`XDG_CONFIG_HOME/db-cli/config.toml`，最后是上述平台默认位置。Windows 缺少
`APPDATA` 时回退到 `%USERPROFILE%\AppData\Roaming\db-cli\config.toml`。不会
自动查找旧的 Windows 路径 `%USERPROFILE%\.config\db-cli\config.toml`；请移动
文件，或通过 `DB_QUERY_CONFIG`、`XDG_CONFIG_HOME`、`--config` 显式选择。
所有平台都会对符号链接给出警告；文件 owner 和 POSIX mode 权限过宽仅在
POSIX 系统检查，本工具不审计 Windows ACL。明文密码字段会被拒绝。TOML 必须
使用 UTF-8；使用 Windows PowerShell 5.1 时，请保留示例文件编码，或使用可将
文件保存为 UTF-8 的编辑器。

支持的 JDBC URL 参数为 `connectTimeout`、`socketTimeout` 和 `useSSL`。
显式 profile timeout 和 TLS 字段优先，其次是 JDBC URL 参数，最后使用 5 秒
连接超时和 30 秒查询超时的默认值。未知 JDBC 参数会被拒绝。连接超时映射到
driver connection timeout；查询超时映射到 read/write socket timeout。因此
`QUERY_TIMEOUT` 表示 driver/socket 超时，而不是严格的总 wall-clock deadline。

TLS 默认为 `required`，会验证系统信任链和 hostname。`preferred` 在服务端
提供 TLS 时使用 TLS，只在 TLS 不可用时允许明文回退。`disabled` 禁止 TLS。
production profile 可以显式选择较弱模式，但命令会输出与该 profile 相关的
警告。

## PostgreSQL 查询（开发版本）

在当前 checkout 执行 `pip install '.[postgresql]'`（或
`pipx install --force '.[postgresql]'`）安装可选驱动。仅使用 MySQL 无需安装。
选用 PostgreSQL 却未安装驱动时，返回含安装提示的结构化
`DRIVER_NOT_INSTALLED` 错误。

省略 `driver` 默认为 `mysql`。PostgreSQL 必须显式配置 `driver = "postgresql"`，
URL 格式为 `postgresql://host[:port]/database`；默认端口 5432，必须指定数据库。
拒绝 driver/URL 不匹配、URL 内嵌凭据、Unix socket、多主机及 URL 参数。
MySQL 继续允许省略数据库。两个后端均使用 `username` 和 `password_env`。

```toml
[profiles.pg]
driver = "postgresql"
url = "postgresql://db.example.cn:5432/analytics"
username = "readonly_user"
password_env = "DB_QUERY_PG_PASSWORD"
environment = "staging"
tls = "required"
# 私有 CA 可选配置：
# tls_ca_file = "/path/to/ca.pem"
```

`profiles`、离线 `validate`、`validate --profile pg --connect` 和 `query` 支持
PostgreSQL profile。16、17、18 为集成测试目标；跳过容器测试不能证明版本支持。
连接检查和查询都启用并确认只读会话；连接检查报告实际 `tls_active`，查询保留
production 精确 profile 确认。

本阶段支持原生 `SELECT`、嵌套/括号及集合查询，SQL 原样下发。
`WITH`、`SHOW`、`EXPLAIN` 暂不开放。普通表查询及集合查询需要不超过
`max_rows` 的外层整数 `LIMIT`；`LIMIT ALL`、表达式、逗号语法和 `FETCH FIRST`
不能代替。简单聚合投影、无外层 `FROM` 查询保留豁免；这不是全局输出行数硬上限。
词法识别 dollar quote、转义字符串、双引号标识符和嵌套注释；Unicode 转义标识符
和转义字符串续行保守拒绝。多语句、写入、DDL、行锁、psql 元命令及已知修改
会话/序列、获取 advisory lock、管理性修改函数在访问驱动前拒绝，也检查带引号及
schema 限定的函数调用。任意自定义函数的副作用仍需数据库权限约束。

整数、文本、Decimal、无时区日期/时间、二进制和 NULL 沿用现有 JSON/CSV/table
格式与 envelope，拒绝重复列名。PostgreSQL 特有布尔、带时区时间、UUID、JSON
和数组在后续类型支持前明确返回 `RESULT_ENCODING_FAILED`。

PostgreSQL 的 TLS `required` 使用 `verify-full` 验证证书及主机名，默认使用系统
信任根，私有 CA 使用 `tls_ca_file`。`preferred` 优先 TLS，可回退明文，不验证
证书；`disabled` 禁止 TLS。MySQL TLS 行为保持不变。
`connect_timeout_seconds` 控制连接建立超时。PostgreSQL 的 `query_timeout_seconds`
设置服务端毫秒级 `statement_timeout`，取消映射到 `QUERY_TIMEOUT`；MySQL 的 `query_timeout_seconds` 继续映射到 read/write socket timeout。
两者都不是端到端总时限。

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

PowerShell 可通过 here-string 传入相同 SQL：

```powershell
@'
SELECT id, status
FROM logistics.t_waybill
ORDER BY id DESC
LIMIT 20;
'@ | db-query query --profile uat --stdin
```

production 查询必须先审查准确的 profile 和 SQL，再提供完全匹配的确认：

```bash
db-query query --profile prod --confirm-profile prod --stdin
```

默认 JSON 包含 profile、environment、duration、顺序固定的 columns、row
count 和 rows。`--format csv` 使用标准 CSV quoting，以 `\N` 表示 null，并将
空字符串保留为空字段。`--format table` 使用 SQL 风格的 `psql` 布局，以
`NULL` 表示 null，并关闭数字解析，从而保留前导零和高精度数字字符串。

所有格式使用同一套标量语义：Decimal 为字符串；整数和 MySQL boolean alias
保持 JSON number；date 为 `YYYY-MM-DD`；datetime 为不带时区的 ISO 8601；
带符号的 MySQL time 保留符号和微秒；binary 使用可逆的小写 `0x` 前缀十六
进制。重复结果列名会返回 `RESULT_ENCODING_FAILED`，调用方需要提供唯一 SQL
alias。工具没有总输出字节上限，因此即使明细行数受限，也应避免选择大型
binary 字段。

每个 profile 的 TOML 表可配置 `max_rows = 2000`，表示 SQL LIMIT 返回行数的
允许上限，不会自动插入 LIMIT。省略时默认 1000，接受更大或更小的正整数；
布尔值、字符串、浮点数、零和负数均为配置错误。各 profile 独立生效，不提供
全局配置、环境变量或 CLI 覆盖。超限错误会显示实际生效的上限。

安全扫描器只接受一条受支持的只读语句。明细查询必须使用最外层字面量
`LIMIT`；`LIMIT count OFFSET offset` 和 `LIMIT offset,count` 都按 count 与
`max_rows` 比较。单行聚合豁免仅适用于投影全部为明确的 `COUNT`、`SUM`、
`AVG`、`MIN`、`MAX` 调用，允许别名和多个聚合。分组、窗口函数、混合投影和
聚合外包表达式都要求 LIMIT；名为 `max` 的普通标识符不会获得豁免。

UNION 等可识别的组合查询必须限制整个结果，即使分支是常量或聚合也不豁免。
分支、CTE、子查询内部的 LIMIT 不能代替整体限制。支持包围整个查询的括号；
无法确认作用范围时返回结构化错误，要求用户提供可识别的最外层 LIMIT，
不会自动改写 SQL。

`SHOW`、`DESC`/`DESCRIBE`、`EXPLAIN` 和没有顶层 `FROM` 的简单常量查询
（如 `SELECT 1`）保留豁免。这不是全局输出行数上限，也不保证扫描量或计算
成本，没有执行端行数兜底。数据库账号自身仍应只有只读权限：静态扫描和
session 只读模式属于 defense in depth，不能替代数据库授权。

默认只离线校验配置；也可以显式测试指定连接：

```bash
db-query validate --profile uat --connect
db-query validate --profile prod --connect --confirm-profile prod
```

## Skill

显式调用的 `$db-query` skill 位于
[`.agents/skills/db-query/SKILL.md`](.agents/skills/db-query/SKILL.md)：

```text
$db-query 使用 prod profile 查询项目 252143 最近 20 条运单
```

它每次选择一个 profile，并执行一条有范围限制的只读 SQL。对于 production，
它会展示准确的 profile 和 SQL，并等待用户明确批准这个未变化的组合。profile
或 SQL 的任何变化都需要重新批准。数据库返回证据与推断会分开说明，写操作
始终不在该 skill 的执行边界内。

如需在其他仓库使用，将 `.agents/skills/db-query` 安装或链接到对应 skills
目录，并确保 `db-query` 可用即可。

## 开发

使用 Python 3.11+，在虚拟环境中安装开发依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev,postgresql]'
python3 -m mypy
python3 -m unittest discover -s tests -v
DB_QUERY_RUN_MYSQL_INTEGRATION=1 python3 -m unittest discover -s tests -p test_mysql_integration.py -v
DB_QUERY_RUN_POSTGRESQL_INTEGRATION=1 python3 -m unittest discover -s tests -p test_postgresql_integration.py -v
```

Windows PowerShell 使用 `.venv\Scripts\Activate.ps1` 激活虚拟环境。

Mypy 检查 `src/db_query` 和 `tests`，包括未标注类型的函数体。
版本和配置统一保存在 `pyproject.toml`；配置中仅为 PyMySQL 豁免缺少
第三方类型信息的错误。GitHub Actions 在推送和 pull request 时使用 Python 3.11
运行同一类型检查命令。

集成测试仅创建一次性本地 MySQL 和 PostgreSQL 16/17/18 容器，不访问 UAT 或
production。PostgreSQL 测试需要 Docker 和 OpenSSL，生成临时 CA 与服务器证书，
覆盖可信 TLS、错误 CA、主机名不匹配、认证及只读会话初始化。跳过项必须单独
报告，不能视为对应版本的支持证据。
