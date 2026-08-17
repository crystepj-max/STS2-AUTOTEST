"""探针 A：必然失败的单元测试（issue-21 反向验证，勿合入）。"""

from __future__ import annotations


def test_always_fails() -> None:
    assert False, "探针 A 故意失败"
