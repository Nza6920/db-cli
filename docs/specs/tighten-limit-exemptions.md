# 收紧只读查询的 LIMIT 豁免与作用范围检查

状态：功能范围与测试入口已确认，已发布为 [GitHub issue #8](https://github.com/Nza6920/db-cli/issues/8)。
发布标签：`ready-for-agent`。

## Problem Statement

使用 db-query 的用户和自动化 agent 希望明细查询具有明确的返回行数限制。当前静态检查只要遇到顶层 COUNT、SUM、AVG、MIN 或 MAX，就可能豁免 LIMIT。这会将分组聚合、窗口函数，甚至名为 max 的普通别名误当作有界聚合。检查也未充分确认 LIMIT 是否约束整个组合查询，而非其中一个分支。

这些误判可能让多行查询绕过预期的返回行数限制。同时，不同 profile 需要不同的行数预算，固定 1000 行无法适应所有使用场景。当前执行器一次性读取结果，因此返回过大可能增加内存与输出压力。此次改进针对返回行数约束，不承诺限制数据库扫描量或计算成本，也不承诺所有受支持命令统一受同一个行数上限约束。

## Solution

收紧聚合查询的 LIMIT 豁免：仅能明确识别为至多一行结果的简单无分组聚合允许免 LIMIT。其他受限查询必须提供约束整个结果的最外层字面量 LIMIT，行数不得超过所选 profile 的 max_rows；省略配置时默认 1000，可配置为更大或更小的正整数。无法确认语句符合规则或 LIMIT 作用范围时，拒绝并解释原因，不自动修改用户 SQL。

保留 SHOW、DESC/DESCRIBE、EXPLAIN 以及现有无顶层 FROM 常量查询的豁免。UNION 等组合查询单独按整个结果检查，不能因为某个分支是常量或聚合而豁免。

## User Stories

1. As an 自动化 agent, I want 无 LIMIT 的普通明细查询被拒绝, so that 意外的大结果不会通过静态检查。
2. As an 查询用户, I want 简单 COUNT 聚合继续无需 LIMIT, so that 常见计数查询保持方便。
3. As an 查询用户, I want SUM、AVG、MIN、MAX 简单聚合享有同样的明确豁免, so that 单行汇总行为一致。
4. As an 查询用户, I want 简单聚合允许别名, so that 返回列可以使用有意义的名称。
5. As an 查询用户, I want 多个简单聚合组成的投影可以豁免 LIMIT, so that 一次查询可以返回多个单行汇总指标。
6. As an 查询用户, I want GROUP BY 查询必须带有效 LIMIT, so that 分组数量不会绕过返回行数规则。
7. As an 查询用户, I want 窗口函数查询必须带有效 LIMIT, so that 保留明细行的计算不会被误判为单行汇总。
8. As an 查询用户, I want 名为 max 或其他聚合名称的标识符不触发豁免, so that 名称不会改变行数校验。
9. As an 查询用户, I want 聚合与普通表达式混合的投影必须带有效 LIMIT, so that 不明确的结果形态受到约束。
10. As an 查询用户, I want 聚合外嵌套函数或表达式暂时要求 LIMIT, so that 支持范围保持清晰且可验证。
11. As an 查询用户, I want UNION 等组合查询的 LIMIT 约束整体结果, so that 单个分支的限制不能被当作整体限制。
12. As an 查询用户, I want CTE 或子查询内部的 LIMIT 不能替代所需的外层 LIMIT, so that 内外层作用范围不会混淆。
13. As an 查询用户, I want 合法的 LIMIT 数值边界和两种 OFFSET 写法继续受支持, so that 现有有界查询保持可用。
14. As an 自动化 agent, I want 不确定的语句返回结构化拒绝原因, so that 我能改写查询而不是依赖静默放行。
15. As an 查询用户, I want 允许执行的 SQL 原样发送给驱动, so that 执行内容与我提交和审查的内容一致。
16. As an 查询用户, I want SHOW、DESC/DESCRIBE、EXPLAIN 保持现有豁免, so that 元数据查询与执行计划查看流程不受此次限制变更影响。
17. As an 查询用户, I want 简单无顶层 FROM 的常量查询继续免 LIMIT, so that 连通性检查保持兼容。
18. As an 维护者, I want 现有只读与危险语句防护继续生效, so that 修复行数规则不会削弱其他检查。
19. As an 维护者, I want 文档明确静态行数约束的例外及成本边界, so that 用户不会把 LIMIT 理解为计算量或全局输出保证。

20. As an 查询用户, I want 每个 profile 可单独配置 max_rows, so that 不同环境和查询用途能使用合适的行数预算。
21. As an 查询用户, I want 省略 max_rows 时默认 1000, so that 现有配置保持兼容。
22. As an 查询用户, I want 无效的 max_rows 在配置校验时报错, so that 配置错误不会静默改变限制。
23. As an 自动化 agent, I want 超限错误显示当前 profile 的有效上限, so that 我能提交符合要求的 LIMIT。

## Implementation Decisions

- 修改现有 SQL 安全校验模块中的聚合识别和 LIMIT 作用范围判断；沿用手写扫描实现，不引入 sqlparse。
- 在每个 profile 中新增可选 max_rows 配置项，默认 1000；接受正整数，允许高于或低于默认值。拒绝布尔值、字符串、浮点数、零和负数，不提供无限制哨兵值。
- 扩展配置解析与 Profile 数据模型，将选定 profile 的 max_rows 传入现有 SQL 安全校验入口的 max_rows 参数。无效配置使用现有配置错误机制，在数据库调用前失败。
- 本次采用 profile 配置，不增加全局配置、环境变量或 CLI 覆盖层；各 profile 的值独立生效。
- 上限仅校验 SQL 中显式 LIMIT 的返回行数，不自动插入 LIMIT，不改变既有豁免。超限错误保留 SQL_LIMIT_EXCEEDED 分类，消息显示实际有效上限。
- 聚合豁免要求投影项全部为 COUNT、SUM、AVG、MIN、MAX 的明确函数调用；允许别名及多个聚合投影项。
- 聚合豁免不适用于 GROUP BY、窗口函数、组合查询、混合普通投影或聚合调用外再包裹表达式的情形。必须区分函数调用、标识符、字符串和注释中的同名文本。
- 不扩大为完整 SQL 语法或语义验证器。仅对能明确识别的支持形态授予豁免；其余形态必须有可确认的有效外层 LIMIT，否则拒绝。
- UNION 等组合查询必须具有约束整体结果的 LIMIT。分支内、CTE 内或子查询内 LIMIT 不能冒充整体限制；某个分支的聚合或常量豁免不能传递到组合结果。
- 保持字面量整数 LIMIT 规则和现有 LIMIT count OFFSET offset、LIMIT offset,count 的支持，限制对象是返回行数参数。
- 沿用现有结构化 SQL 错误机制：缺失 LIMIT、无效 LIMIT、超过上限应保持对应的既有分类；无法确认作用范围时明确说明原因，不能作为驱动错误延后暴露。
- 保留现有单语句、写操作、危险子句、可执行注释等防护，以及数据库 session 只读设置。
- 保留 SHOW、DESC/DESCRIBE、EXPLAIN 和现有简单常量查询豁免。不增加结果读取兜底，不修改结果编码与输出格式。
- 不自动补 LIMIT 或重写 SQL。允许的语句必须原样传入数据库驱动。
- 同步中英文使用说明和示例配置，说明 max_rows 配置、默认值、有效值、收紧后的豁免范围、保守拒绝策略及非全局行数上限。

## Testing Decisions

- 使用一个已获用户确认的现有高层测试入口：CLI 子进程配合模拟 PyMySQL 驱动。
- 测试通过 CLI 覆盖 SQL 安全校验和驱动调用边界。好的测试验证退出码、结构化错误、是否调用驱动及传入的原始 SQL，不断言内部 token、辅助函数结构或具体遍历算法。
- 沿用已有的“拒绝写入、多语句及无界明细查询”和“受支持只读语句到达驱动”测试模式，不引入新的生产测试接口。
- 验证简单单个或多个聚合以及带别名聚合免 LIMIT；验证分组、窗口、同名标识符、混合投影、外包表达式缺少 LIMIT 时拒绝，并验证添加可识别有效 LIMIT 后可通过静态行数检查。
- 验证组合查询没有整体 LIMIT 或只有分支 LIMIT 时拒绝，整体 LIMIT 合法时允许，整体超限时拒绝；覆盖分支聚合和常量不能带来整体豁免。
- 验证 CTE、子查询和括号不会混淆 LIMIT 的作用范围；无法可靠分析的形态应在调用驱动前拒绝。
- 验证默认上限时的 LIMIT 0、1000、1001，以及自定义上限 N 时的 LIMIT N、N+1、两种 OFFSET 写法及非字面量输入；注释、字符串、引用标识符中的关键字不能制造豁免或伪 LIMIT。
- 通过同一 CLI 测试入口验证省略 max_rows 的兼容行为、低于和高于 1000 的配置、不同 profile 值的独立生效，以及超限消息包含有效上限。
- 通过 CLI 配置校验验证 max_rows 为布尔值、字符串、浮点数、零和负数时返回配置错误，且不会调用数据库。
- 回归常量查询、SHOW、DESC/DESCRIBE、EXPLAIN，以及现有危险语句、写入、多语句和可执行注释拦截行为。
- 模拟驱动应能证明被拒绝的查询没有触发数据库调用，并断言允许的 SQL 未被重写。
- 运行现有离线单元测试套件；此规格的验收不需要连接真实业务数据库或新增数据库集成测试入口。

## Out of Scope

- 控制数据库扫描行数、执行计划、CPU 成本或昂贵查询。
- 为所有命令建立统一输出上限，或增加读取上限加一行后报错等执行端兜底。
- 改变 SHOW、DESC/DESCRIBE、EXPLAIN 的既有豁免。
- 自动补写 LIMIT、SQL 格式化、预览或任意 SQL 改写。
- 引入 sqlparse 或其他解析器，构建完整 MySQL AST 或语义分析器。
- 扩大复杂表达式的免 LIMIT 支持、增加多数据库支持、改变 profile 确认或权限机制。

## Further Notes

用户已通过讨论确认上述功能范围和保守拒绝取舍。聚合判定误放行已通过现有校验入口离线复现，未连接数据库。

用户已确认使用 CLI 子进程与模拟数据库驱动作为测试入口。规格在 GitHub Issues 跟踪，使用 `ready-for-agent` 标签；实现工作尚未开始。
