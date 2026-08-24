# TC-IRONCLAD-TWIN-STRIKE-DAMAGE 战士双重打击伤害验证

## Metadata
- id: TC-IRONCLAD-TWIN-STRIKE-DAMAGE
- level: case
- tags: ironclad, combat, card, damage
- priority: P0

## Start State
- 任意可恢复状态
- 允许当前处于 MAIN_MENU / CHARACTER_SELECT / EVENT / MAP / COMBAT / VICTORY / GAME_OVER / UNKNOWN

## End State
- 当前位于战斗界面
- 已尝试打出 TWIN_STRIKE
- 伤害事件应记录为 5 点伤害 2 次

## Given
- 已安装并可连接 STS2-Agent（HTTP `http://127.0.0.1:8080`），且调试动作已启用（`STS2_ADAPTER__AGENT__DEBUG_ACTIONS=true`）
- 本用例权威运行路径为 Agent + debug：`give_card` 注入依赖适配器调试能力，STS2-Cli-Mod（`sts2` CLI）无该命令通道，不作为本用例通过路径
- 游戏可被启动并加载到主菜单
- 使用原游戏角色 Ironclad（战士）
- 双重打击的原版卡牌 ID 为 TWIN_STRIKE
- 双重打击的原版基线为 damage=5、hit_count=2

## When
1. 返回主菜单
2. 开始新 run
3. 选择战士
4. 开始冒险
5. 选择开局事件的第 0 个选项
6. 选择首个普通战斗节点
7. 进入首次战斗
8. 添加 TWIN_STRIKE 到手牌
9. 使用 TWIN_STRIKE

## Then
- 不应出现 crash
- 当前应位于战斗界面
- 造成 5 点伤害 2 次
