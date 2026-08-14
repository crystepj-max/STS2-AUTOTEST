# F1 状态工具漂移取证（T1 基线）

取证时间：2026-08-14（只读，无任何改动）。

## 脚本描述的安装 vs 本机真实安装

| 项 | `scripts/setup-mac-runner.sh` 描述 | 本机真实状态（实测） |
|---|---|---|
| 安装目录 | `~/actions-runner-autotest` | `~/actions-runner`（v2.336.0） |
| 服务形态 | 自定义 `com.sts2.autotest-runner.plist` + `run.sh` | svc.sh 安装的 launchd 服务 |
| launchd label | `com.sts2.autotest-runner` | `actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST` |
| .env 内容 | `STS2_WORKSPACE`/`STS2_GAME_DIR`/`STS2_MODS_DIR`/`GODOT_PATH` | `PATH`/`STS2_CLI_PATH`/`AGENT_TOOLSDIRECTORY`/`RUNNER_TOOL_CACHE`/代理变量 |
| runner 名称 | `mac-autotest-$(hostname -s)` | `Chris-Mac-mini-STS2-AUTOTEST`（agentId 21） |
| 版本 | 2.322.0 | 2.336.0 |

## 实测命令输出

```bash
$ ls -d ~/actions-runner-autotest
ls: /Users/chris/actions-runner-autotest: No such file or directory

$ launchctl list | grep com.sts2
（无输出 —— com.sts2.autotest-runner 不存在）

$ cd /tmp && ~/actions-runner/svc.sh status
Failed: Must run from runner root or install is corrupt
# ← svc.sh 强依赖 cwd；任何封装脚本必须先 cd 到 runner 根目录

$ cd ~/actions-runner && ./svc.sh status
status actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST:
/Users/chris/Library/LaunchAgents/actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST.plist
Started:
40231 0 actions.runner.crystepj-max-STS2-AUTOTEST.Chris-Mac-mini-STS2-AUTOTEST
```

## 影响链

1. 运维人员按 `setup-mac-runner.sh` / 旧文档操作 → 目录、label 全部打不到真实服务；
2. 状态命令（按旧 label 查询）报「Stopped / 不存在」→ Issue 症状 3「状态报告与
   真实进程不一致、停止/启动未真正替换后台进程」；
3. 即使按真实路径调用 `svc.sh`，cwd 不对也会误报 "install is corrupt"。

## T2 修复方向（以此为准）

- 统一入口脚本：先探测真实安装目录（默认 `~/actions-runner`），`cd` 到该目录后
  调用 `svc.sh`，提供 `status` / `stop` / `start` 子命令；
- 重写 `setup-mac-runner.sh` 为「基于真实安装的部署/修复脚本」（install → config → svc.sh），
  删除对不存在路径的引用；保留对已存在安装的幂等性；
- 文档（T5 手册）以真实安装为准。
