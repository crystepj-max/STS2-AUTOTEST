# TC-PREPARE-NEW-RUN 进入新局地图

## Metadata
- id: TC-PREPARE-NEW-RUN
- level: case
- tags: smoke, bootstrap
- priority: P0

## Start State
- 任意可恢复状态
- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN

## End State
- 到达 Act 1 地图
- 当前可选择首个可达节点

## Given
- 已安装并可连接 STS2-Cli-Mod
- 游戏可被启动
- 如存在旧 run，框架应负责回收并回到可重新开局状态

## When
1. 返回主菜单
2. 选择标准模式
3. 开始新 run
4. 选择 Ironclad
5. 开始冒险
6. 推进事件对话

## Then
- 不应出现 crash
- 最终应位于地图界面
- 应能识别至少一个可达节点
