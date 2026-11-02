# 规格目录（SDD）

本目录是 ResearchOS 的规格来源。流程要求见 `PROMPT.md` 任务 2026-10-30-2：**任何编码前先写或更新规格**，实现偏离时先改规格和决策记录，阶段结束记录验收证据。

| 文件 | 作用 |
| --- | --- |
| [REQUIREMENTS.md](./REQUIREMENTS.md) | 需求映射表：每个功能 ID 的阶段、状态、证据 |
| [DECISIONS.md](./DECISIONS.md) | 技术决策记录，含偏离开发文档的原因 |
| [000-foundation.md](./000-foundation.md) | 阶段 0：工程底座 |
| [001-p0-workspace-paper-import.md](./001-p0-workspace-paper-import.md) | P0.1 / P0.2：工作区与 PDF 导入纵向切片 |
| [002-p0-literature-search.md](./002-p0-literature-search.md) | P0.3：文献检索（arXiv、OpenAlex、Crossref）与元数据导入 |
| [003-p0-notes-annotations.md](./003-p0-notes-annotations.md) | P0.4（第一部分）：笔记与带来源的批注 |
| [004-p0-tasks.md](./004-p0-tasks.md) | P0.5（第一部分）：项目任务与看板 |
| [005-p0-cockpit.md](./005-p0-cockpit.md) | P0.6：最小驾驶舱（工作区总览） |
| [006-p0-citation-export.md](./006-p0-citation-export.md) | P0.7：引用导出（BibTeX、RIS） |
| [007-p0-cited-qa.md](./007-p0-cited-qa.md) | P0.8：带出处的文献问答（接入 Anthropic 模型） |
| [008-p0-paper-summary.md](./008-p0-paper-summary.md) | P0.9：单篇文献的核心贡献总结 |
| [spikes/](./spikes/) | 上游候选验证记录（版本、许可、实测结果、失败情况） |

## 状态词

| 状态 | 含义 |
| --- | --- |
| 未做 | 没有实现 |
| 进行中 | 有部分实现，验收未全部通过 |
| 完成 | 全部验收标准有证据；证据写在规格或映射表中 |
| 实验 | 隔离原型，不进入默认流程 |
| 阻塞 | 缺条件才能继续，写明缺什么 |

只有有验收证据才能标“完成”。部分子项完成的功能 ID 标“进行中”，并写明完成了哪一部分。

## 每份规格的必备章节

1. 问题、目标、范围/非目标、需求映射
2. 用户场景与行为（流程、页面/API、状态变化、空/加载/失败状态、角色权限）
3. 数据与接口（实体、迁移、API 契约、外部服务版本/配置）
4. 验收标准（Given/When/Then，覆盖成功、异常、权限、数据一致性）
5. 技术决策与风险
6. 实施计划与完成定义
7. 验收证据（阶段结束时填写：实际命令与结果）

## 检查

`scripts/check_requirements.py` 校验开发文档中的每个功能 ID 都出现在映射表里，且状态词合法，防止需求被悄悄删掉。
