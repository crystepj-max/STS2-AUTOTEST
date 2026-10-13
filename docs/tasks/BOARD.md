# 本地任务看板

> 自动生成，请勿手工编辑。来源：`docs/tasks/registry.json`

## 本地已定义

- [ ] LOC-001 api_timeout 显式化（sized-s）
- [ ] LOC-002 settle 稳定口径收敛（指纹单源）（sized-s）
- [ ] LOC-003 run-result 路径常量单源（sized-s）
- [ ] LOC-006 fluent魔法子串清除（sized-m）

## 定义中（含未决产品事项，待拍板）

- [ ] LOC-004 推进到MAP序列收敛（sized-m）
- [ ] LOC-005 回主菜单恢复统一（sized-m）
- [ ] LOC-007 runner三件套迁移（sized-m）
- [ ] LOC-009 规格门禁 fail-closed：未映射步骤不得静默通过（体量未评估）
- [ ] LOC-008 基线捕获与相对（增量）断言语义（体量未评估，依赖 LOC-009）
- [ ] LOC-010 套件级 Then 无条件阻断·仅登记待拍板（体量未评估）

<!--
2026-10-12 手工追加 LOC-008/009/010 三条（已同步登记 registry.json）。
说明两点，供下一个人核对：
1. 本文件表头写「自动生成」，但 scripts/ 与 src/ 乃至兄弟仓 sts2-dev-infra 全量搜索均无生成器，
   实际一直是手工维护——上面 LOC-001/004/005/007 的条目状态已与 registry.json（已合并/已否决）不符，
   属既存陈旧，未在本次一并修（超范围）。
2. LOC-008/009/010 是 Gawain 侧上报缺口 ⑤/②/① 的权威定义轨道，走本地轨道、不在 GitHub 建 issue；
   GitHub 本批只开了 #103–#106，覆盖第 3/4/9 条与一条新缺陷，两边不重叠。
-->
