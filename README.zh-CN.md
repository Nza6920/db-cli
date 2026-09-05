# db-cli

[English](README.md) | 简体中文

`db-cli` 提供支持 profile 的只读 MySQL 命令 `db-query`。0.2.1 版本通过固定
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
pipx install "git+https://github.com/Nza6920/db-cli.git@v0.2.1"
db-query --help
```

开发中的本地 checkout 可在仓库根目录执行 `pipx install --force .`。

## 兼容性与回滚

| db-query | 执行和格式化依赖 | 运行时 SQL 客户端 |
| --- | --- | --- |
| v0.1.2 | usql 0.21.4 | 需要单独安装 `usql` |
| v0.2.0 | PyMySQL 1.2.0、tabulate 0.10.0 | 无 |
| v0.2.1 | PyMySQL 1.2.0、tabulate 0.10.0 | 无 |

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

```bash
python3 -m unittest discover -s tests -v
DB_QUERY_RUN_MYSQL_INTEGRATION=1 python3 -m unittest tests.test_mysql_integration -v
```

集成测试只创建一次性本地 MySQL 容器，不访问 UAT 或 production。
