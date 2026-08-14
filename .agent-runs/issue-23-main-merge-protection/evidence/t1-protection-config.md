## T1 分支保护配置证据（2026-08-14）

- 配置命令: gh api -X PUT .../branches/main/protection
- required_status_checks: strict=true, contexts=["PR Check Summary"]
- enforce_admins: true（管理员同样受约束）
- required_pull_request_reviews: 未启用（approvals=0，solo 维护者）
- restrictions: null（公开仓库）
- 回读完整 JSON 见: t1-protection-readback.json
