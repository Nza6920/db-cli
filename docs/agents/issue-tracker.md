# Issue tracker: GitHub

本项目的任务和规格发布到 Nza6920/db-cli 的 GitHub Issues。
使用 gh CLI 操作，明确指定该仓库。

## 工作约定

- 发布规格或任务：创建 GitHub issue。
- 获取任务：读取正文、标签和评论。
- 多行正文或评论：写入临时文件，通过 --body-file 提交。
- 标签名称使用 docs/agents/triage-labels.md 的映射。
- 发布前检查是否已有对应 issue，避免重复创建。

## Pull requests as a triage surface

PRs as a request surface: no.

## Wayfinding operations

- Map 使用标记为 wayfinder:map 的 issue。
- 子任务优先使用 GitHub sub-issue；不可用时，
  在 map 正文维护任务列表，子任务注明 Part of #编号。
- 阻塞关系优先使用 GitHub 原生 issue dependencies；
  不可用时，在正文记录 Blocked by: #编号。
- 可领取任务须未关闭、无负责人，且所有阻塞任务已关闭。
- 领取时分配给执行者；完成时记录结果、关闭任务，
  并在 map 中更新结论及链接。
