## T3a 失败样例证据：直接推送 main 被拒（2026-08-14）

### 第一次（ruleset 修正前）
```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Changes must be made through a pull request.
remote: - Required status check "Unit Tests" is expected.  # 旧 ruleset 悬空检查（已修正）
remote: - Required status check "PR Check Summary" is expected.
! [remote rejected] tmp-t3a-probe -> main (push declined due to repository rule violations)
```

### 复测（ruleset 修正为 PR Check Summary + approvals=0 后）
```
remote: error: GH013: Repository rule violations found for refs/heads/main.
! [remote rejected] tmp-t3a-probe -> main (push declined due to repository rule violations)
```

- 结论：直接写入 main 被制度性禁止（PR 之外无任何写入通道），修正未削弱保护。
