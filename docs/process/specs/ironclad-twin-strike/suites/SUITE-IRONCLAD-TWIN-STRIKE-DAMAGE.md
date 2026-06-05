# SUITE-IRONCLAD-TWIN-STRIKE-DAMAGE 战士双重打击真实流程验证

## Metadata
- id: SUITE-IRONCLAD-TWIN-STRIKE-DAMAGE
- level: suite
- tags: ironclad, combat, card, damage
- priority: P0

## Goal
- 验证从启动游戏、进入战士首战、添加双重打击到手牌、打出卡牌，到校验 5 点伤害 2 次的完整自动化链路。

## Mode
- execution: sequential_shared_session

## Includes
1. TC-IRONCLAD-TWIN-STRIKE-DAMAGE

## Then
- 测试规格应可被 review 和 compile
- 真实运行应给出通过、失败或运行时阻塞的明确证据
