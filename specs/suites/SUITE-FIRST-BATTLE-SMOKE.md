# SUITE-FIRST-BATTLE-SMOKE 首次战斗冒烟

## Metadata
- id: SUITE-FIRST-BATTLE-SMOKE
- level: suite
- tags: smoke, first_battle
- priority: P0

## Goal
- 验证从启动游戏到完成首次战斗的完整主链路可用

## Mode
- execution: sequential_shared_session

## Includes
1. TC-PREPARE-NEW-RUN
2. TC-RESOLVE-NEOW
3. TC-FINISH-FIRST-BATTLE

## Then
- 整条链路应可连续完成
- 任一子用例失败时应给出失败位置
