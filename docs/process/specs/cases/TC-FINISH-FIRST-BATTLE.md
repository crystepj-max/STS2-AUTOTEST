# TC-FINISH-FIRST-BATTLE 完成首次战斗

## Metadata
- id: TC-FINISH-FIRST-BATTLE
- level: case
- tags: smoke, combat
- priority: P0

## Start State
- 当前位于地图界面
- 存在至少一个可到达的普通战斗节点

## End State
- 首次战斗结束
- 当前位于奖励界面或地图界面

## Given
- 已安装并可连接 STS2-Cli-Mod
- 首次战斗节点可被选择

## When
1. 选择地图节点 (2, 1)
2. 进入首次战斗
3. 按基础策略完成战斗
4. 跳过卡牌奖励

## Then
- 不应出现 crash
- 战斗结束后应回到奖励界面或地图界面
