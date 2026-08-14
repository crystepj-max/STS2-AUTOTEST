"""T3b 失败样例探针（issue-23）：刻意构造 CI 失败以验证合并门禁。

该文件仅存在于探针分支，验证结束后随分支删除，不会进入 main。
"""


def test_t3_probe_ci_failure() -> None:
    """刻意失败的测试：用于验证 PR Check Summary 失败时合并入口被禁用。"""
    assert False, "T3b 探针：故意失败"
