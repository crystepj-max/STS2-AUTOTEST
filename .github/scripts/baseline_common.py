"""CI baseline 脚本共享的移动感知问题比较（issue #17）。

问题指纹 ``(path, code, message, source)`` 包含文件路径：文件被移动/重命名时，
旧位置指纹消失、新位置指纹出现，朴素 Counter 差值会把「移动」误判为
「已解决 + 新增」并错误阻断 CI。本模块在差值之外补充 1:1 移动映射。

判定规则（权威定义见 tests/fixtures/issue17_move_scenarios/README.md）：

1. 路径相同的问题按朴素 Counter 差值处理——内容变化、同路径数量增减照常识别。
2. 剩余未匹配问题按指纹 (code, message, source) 分组：组内基线侧与当前侧
   未匹配实例各恰有一个时，判定为文件移动——当前侧该实例从「新增」中剔除，
   基线侧对应实例从「已解决」中剔除。
3. 其余情况（纯新增、纯已解决、1:N / N:M 多候选歧义）不做映射：新增照报；
   歧义组的基线侧未匹配项也不计入「已解决」——旧位置问题去向不明，
   不得谎报为已解决。安全失败：宁可误报也绝不吞掉真实新增。
4. 数量守恒：映射是实例级 1:1 交换，比较前后总数差不变，净增仍被识别。
"""

from __future__ import annotations

from collections import Counter

# 与两个基线脚本保持一致的指纹形状：(相对路径, code, message, 源码行)
IssueKey = tuple[str, str, str, str]
# 移动判定的同指纹组键：路径之外的三元组
_Fingerprint = tuple[str, str, str]


def _fingerprint(key: IssueKey) -> _Fingerprint:
    return (key[1], key[2], key[3])


def move_aware_difference(
    baseline_counts: Counter[IssueKey],
    current_counts: Counter[IssueKey],
) -> tuple[Counter[IssueKey], Counter[IssueKey], int]:
    """移动感知的新增/已解决比较。

    返回 (new_findings, resolved_findings, moved_count)：

    - ``new_findings``：剔除移动映射后的新增问题；非空时调用方应阻断 CI。
    - ``resolved_findings``：剔除移动映射后的已解决问题（仅统计信息）。
    - ``moved_count``：被识别为「随文件移动」的问题实例数。
    """
    new_findings = current_counts - baseline_counts
    resolved_findings = baseline_counts - current_counts

    moved = 0
    # 只检查两侧都存在未匹配实例的指纹组（1:0 与 0:1 无配对可能）
    shared_fingerprints = {_fingerprint(k) for k in new_findings} & {
        _fingerprint(k) for k in resolved_findings
    }
    for fp in shared_fingerprints:
        new_keys = [k for k in new_findings if _fingerprint(k) == fp]
        resolved_keys = [k for k in resolved_findings if _fingerprint(k) == fp]
        new_total = sum(new_findings[k] for k in new_keys)
        resolved_total = sum(resolved_findings[k] for k in resolved_keys)
        if new_total == 1 and resolved_total == 1:
            # 两侧各恰一个未匹配实例 → 文件移动；1:1 交换，数量守恒
            new_findings[new_keys[0]] -= 1
            resolved_findings[resolved_keys[0]] -= 1
            moved += 1
        else:
            # 歧义/数量不匹配：安全失败——新增照报；基线侧去向不明，
            # 从「已解决」中剔除，避免与新增并存造成误导
            for key in resolved_keys:
                del resolved_findings[key]

    # `+` 一元运算丢弃非正计数（Python 3.10+ 语义），保证返回的 Counter
    # 不含 0 值键，与朴素差值的可迭代行为一致
    return +new_findings, +resolved_findings, moved
