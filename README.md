# STS2-AUTOTEST
杀戮尖塔 2 Mod 端到端自动化测试系统

## B19 CliModAdapter 真实 CLI 集成测试

CLI-only 测试只要求 `sts2` CLI 可发现：

```bash
python -m pytest tests/integration/test_cli_mod_cli_only.py -q --tb=short
```

如果 CLI 不在 `PATH` 中，设置：

```powershell
$env:STS2_CLI_PATH="C:\path\to\sts2.exe"
```

真实游戏链路测试要求 Slay the Spire 2 正在运行，且 STS2-Cli-Mod 已加载：

```bash
python -m pytest tests/integration/test_cli_mod_game_smoke.py -q --tb=short
```

B19 的关闭标准：

- CLI-only 测试在有 `sts2.exe` 的机器上通过。
- Game-required 测试在启动游戏和 Mod 后通过。
- 没有游戏环境时，Game-required 测试必须跳过，而不是失败。
- 单元测试继续覆盖 CLI 参数映射、screen mapping、subprocess mock 和错误分类。
