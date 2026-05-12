# STS2-Cli-Mod CLI 参考文档

> 来源：https://github.com/longkerdandy/STS2-Cli-Mod
> 生成日期：2026-05-11

## 概述

STS2-Cli-Mod 是杀戮尖塔 2 的 CLI 控制模组，通过命令行让 AI 代理观察游戏状态并执行动作。

通信架构：

```
AI Agent → sts2 CLI (Named Pipe) → In-Game Mod → Game
```

## 通信协议

- **Named Pipe 名称**：`sts2-cli-mod`
- **默认超时**：5 秒
- **响应格式**：JSON 到 stdout

**成功响应**：
```json
{"ok": true, "data": {...}}
```

**错误响应**：
```json
{"ok": false, "error": "CODE", "message": "..."}
```

**退出码**：
| 退出码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1 | 连接错误 |
| 2 | 无效状态 |
| 3 | 无效参数 |
| 4 | 超时 |

**全局选项**：
- `--version`, `-v`：显示 CLI 版本
- `--pretty`, `-p`：格式化 JSON 输出

## 核心命令

### 连接与状态

| 命令 | 参数 | 描述 | 输出 |
|------|------|------|------|
| `ping` | 无 | 测试与模组的连接 | `{"ok": true}` |
| `state` | `[--include-pile-details]` | 获取完整游戏状态 | 完整游戏状态 JSON |
| `view_deck` | 无 | 查看主牌组 | `{count, cards[]}` |

### 主菜单

| 命令 | 参数 | 描述 |
|------|------|------|
| `new_run` | 无 | 从主菜单开始新游戏 |
| `continue_run` | 无 | 继续已保存的运行 |
| `abandon_run` | 无 | 放弃已保存的运行 |
| `choose_game_mode` | `<mode>` | 选择模式：`standard`、`daily`、`custom` |

### 角色选择

| 命令 | 参数 | 描述 |
|------|------|------|
| `select_character` | `<character_id>` | 选择角色（不区分大小写） |
| `set_ascension` | `<level>` | 设置进阶等级（0 到最大值） |
| `embark` | 无 | 从角色选择开始运行 |

### 地图与导航

| 命令 | 参数 | 描述 |
|------|------|------|
| `choose_map_node` | `<col> <row>` | 前往地图节点 |
| `proceed` | 无 | 离开当前屏幕进入地图 |

### 战斗

| 命令 | 参数 | 描述 |
|------|------|------|
| `play_card` | `<card_id> [--nth <n>] [--target <combat_id>]` | 从手牌打出卡牌 |
| `end_turn` | 无 | 结束当前回合 |
| `use_potion` | `<potion_id> [--nth <n>] [--target <combat_id>]` | 使用药水 |

### 战斗子状态

| 命令 | 参数 | 描述 |
|------|------|------|
| `hand_select_card` | `<card_id> [<card_id>...] [--nth <n>...]` | 从手牌选择卡牌 |
| `hand_confirm_selection` | 无 | 确认手牌选择 |
| `grid_select_card` | `<card_id> [<card_id>...] [--nth <n>...]` | 从网格选择卡牌 |
| `grid_select_skip` | 无 | 跳过网格选择 |

### 事件

| 命令 | 参数 | 描述 |
|------|------|------|
| `choose_event` | `<index>` | 按索引选择事件选项 |
| `advance_dialogue` | `[--auto]` | 推进远古事件对话 |

### 奖励

| 命令 | 参数 | 描述 |
|------|------|------|
| `reward_claim` | `--type <type> [--id <id>] [--nth <n>]` | 领取奖励（gold、potion、relic、special_card） |
| `reward_choose_card` | `--type card --card_id <card_id> [--nth <n>]` | 从奖励中选择卡牌 |
| `reward_skip_card` | `--type card [--nth <n>]` | 跳过卡牌奖励 |

### 卡牌选择

| 命令 | 参数 | 描述 |
|------|------|------|
| `tri_select_card` | `<card_id> [<card_id>...] [--nth <n>...]` | 三选一选择卡牌 |
| `tri_select_skip` | 无 | 跳过三选一选择 |

### 休息点

| 命令 | 参数 | 描述 |
|------|------|------|
| `choose_rest_option` | `<option_id>` | 选择休息选项（HEAL、SMITH 等） |

### 宝箱

| 命令 | 参数 | 描述 |
|------|------|------|
| `open_chest` | 无 | 打开宝箱 |
| `pick_relic` | `<index>` | 从宝箱中选择遗物 |

### 商店

| 命令 | 参数 | 描述 |
|------|------|------|
| `shop_buy_card` | `<card_id> [--nth <n>]` | 购买卡牌 |
| `shop_buy_relic` | `<relic_id> [--nth <n>]` | 购买遗物 |
| `shop_buy_potion` | `<potion_id> [--nth <n>]` | 购买药水 |
| `shop_remove_card` | 无 | 购买删卡服务 |

### 遗物与包裹

| 命令 | 参数 | 描述 |
|------|------|------|
| `relic_select` | `<index>` | 从 Boss/事件选择遗物 |
| `relic_skip` | 无 | 跳过遗物选择 |
| `bundle_select` | `<index>` | 预览包裹（卷轴盒遗物） |
| `bundle_confirm` | 无 | 确认包裹选择 |
| `bundle_cancel` | 无 | 取消包裹预览 |

### 水晶球

| 命令 | 参数 | 描述 |
|------|------|------|
| `crystal_set_tool` | `<tool>` | 切换工具（big/small） |
| `crystal_click_cell` | `<x> <y>` | 点击单元格清除迷雾 |
| `crystal_proceed` | 无 | 离开迷你游戏 |

### 游戏结束

| 命令 | 参数 | 描述 |
|------|------|------|
| `return_to_menu` | 无 | 从游戏结束返回主菜单 |

### 工具

| 命令 | 参数 | 描述 |
|------|------|------|
| `report_bug` | `--title <t> --description <d> [--last-command <cmd>] [--last-response <json>] [--severity <level>] [--labels <labels>]` | 保存结构化 Bug 报告 |

## 游戏状态结构（state 命令输出）

**顶层字段**：
- `screen` — 当前屏幕枚举（MENU、COMBAT、MAP、EVENT 等）
- `timestamp` — Unix 时间戳（毫秒）
- `error` — 状态提取失败时的错误消息

**屏幕特定区域**：
- `menu` — 主菜单状态（has_run_save）
- `singleplayer_submenu` — 游戏模式可用性
- `character_select` — 角色选项、已选角色、进阶
- `map` — 地图节点、当前位置、可前往坐标
- `combat` — 完整战斗状态（玩家、敌人、手牌、牌堆）
- `hand_select` — 手牌选择模式状态
- `grid_card_select` — 网格选择状态
- `event` — 事件选项、对话状态
- `rest_site` — 休息选项
- `treasure` — 宝箱状态、遗物
- `shop` — 商店库存、价格
- `rewards` — 奖励物品
- `tri_select` — 三选一选择
- `relic_select` — 遗物选择
- `bundle_select` — 包裹预览
- `crystal_sphere` — 迷你游戏网格状态
- `game_over` — 胜利/失败状态

## 错误码

**通用错误**：
| 错误码 | 描述 |
|--------|------|
| `CONNECTION_ERROR` | 游戏未运行或模组未加载 |
| `INVALID_REQUEST` | 请求解析失败 |
| `UNKNOWN_COMMAND` | 命令未识别 |
| `MISSING_ARGUMENT` | 缺少必需参数 |
| `INTERNAL_ERROR` | 意外内部错误 |
| `UI_NOT_FOUND` | 未找到所需 UI 元素 |
| `TIMEOUT` | 操作未在时限内完成 |

**状态特定错误**：每个命令类别有特定错误码（如 `NOT_IN_COMBAT`、`CARD_NOT_FOUND`、`NOT_ENOUGH_GOLD` 等）。

## 版本

CLI 和模组版本通过 `Directory.Build.props` 同步（如 `0.103.0`）。

## 关键实现细节

- **通信**：Named Pipe `sts2-cli-mod`，5 秒默认超时
- **线程安全**：所有游戏状态访问通过 `MainThreadExecutor` 封送到 Godot 主线程
- **JSON 格式**：snake_case 命名，null 字段省略，宽松 Unicode 转义
