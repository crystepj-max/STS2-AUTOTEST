# TC-RESOLVE-NEOW 处理开局祝福事件

## Metadata
- id: TC-RESOLVE-NEOW
- level: case
- tags: smoke, event
- priority: P0

## Start State
- 已进入新 run
- 当前位于开局事件界面，且事件可交互

## End State
- 事件处理完成
- 当前位于地图界面，且首个节点可选

## Given
- 已安装并可连接 STS2-Cli-Mod
- 当前事件为开局祝福事件

## When
1. 选择开局事件的第 0 个选项
2. 推进事件对话

## Then
- 不应出现 crash
- 最终应位于地图界面
- 应能识别至少一个可达节点
