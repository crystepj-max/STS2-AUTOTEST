"""单元测试：移动感知基线比较（issue #17）。

场景 S1–S7 与判定规则见 tests/fixtures/issue17_move_scenarios/README.md。
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".github/scripts"
_SPEC = importlib.util.spec_from_file_location(
    "baseline_common_script", _SCRIPTS_DIR / "baseline_common.py"
)
assert _SPEC is not None and _SPEC.loader is not None
baseline_common = importlib.util.module_from_spec(_SPEC)
# sys.modules 常驻条目是刻意的（脚本与 src/ 命名空间隔离，仓库内无同名模块）
sys.modules[_SPEC.name] = baseline_common
sys.path.insert(0, str(_SCRIPTS_DIR))
_SPEC.loader.exec_module(baseline_common)

move_aware_difference = baseline_common.move_aware_difference
IssueKey = baseline_common.IssueKey


def _key(path: str, code: str, message: str, source: str) -> IssueKey:
    return (path, code, message, source)


def test_rename_only_is_move_not_new() -> None:
    """S1：只重命名——同指纹 1:1，判为移动，0 新增 0 已解决。"""

    baseline = Counter({_key("src/legacy_a.py", "F401", "unused import", "import os"): 1})
    current = Counter({_key("src/modern_a.py", "F401", "unused import", "import os"): 1})

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 1
    assert new == Counter()
    assert resolved == Counter()


def test_rename_and_modify_is_new() -> None:
    """S2：重命名并修改问题行——源码行变化，不算同一问题，报新增。"""

    baseline = Counter({_key("src/legacy_a.py", "F401", "unused import", "import os"): 1})
    current = Counter({_key("src/modern_a.py", "F401", "unused import", "import os as i"): 1})

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 0
    assert new == current
    assert resolved == baseline


def test_split_distinct_fingerprints_all_moves() -> None:
    """S3：拆分（纯分散）——每个问题 1:1 落到新文件，全部判为移动。"""

    baseline = Counter(
        {
            _key("src/a.py", "F401", "unused import", "import os"): 1,
            _key("src/a.py", "E501", "line too long", "x = 123"): 1,
        }
    )
    current = Counter(
        {
            _key("src/a1.py", "F401", "unused import", "import os"): 1,
            _key("src/a2.py", "E501", "line too long", "x = 123"): 1,
        }
    )

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 2
    assert new == Counter()
    assert resolved == Counter()


def test_split_copy_ambiguity_fails_safe() -> None:
    """S4：拆分复制（1:2 歧义）——同指纹出现在两个新文件，不映射，全报新增。"""

    baseline = Counter({_key("src/a.py", "F401", "unused import", "import os"): 1})
    current = Counter(
        {
            _key("src/a1.py", "F401", "unused import", "import os"): 1,
            _key("src/a2.py", "F401", "unused import", "import os"): 1,
        }
    )

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 0
    assert new == current
    assert resolved == Counter()


def test_move_plus_real_new_keeps_new() -> None:
    """S5：移动 + 实质新增——只报真实新增，移动项不计。"""

    baseline = Counter({_key("src/legacy_a.py", "F401", "unused import", "import os"): 1})
    current = Counter(
        {
            _key("src/modern_a.py", "F401", "unused import", "import os"): 1,
            _key("src/new_file.py", "F821", "undefined name", "print(missing)"): 1,
        }
    )

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 1
    assert new == Counter({_key("src/new_file.py", "F821", "undefined name", "print(missing)"): 1})
    assert resolved == Counter()


def test_same_path_count_delta_unchanged() -> None:
    """S6：同路径数量增减——走朴素差值，移动逻辑不介入。"""

    baseline = Counter({_key("src/a.py", "F401", "unused import", "import os"): 2})
    current = Counter({_key("src/a.py", "F401", "unused import", "import os"): 3})

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 0
    assert new == Counter({_key("src/a.py", "F401", "unused import", "import os"): 1})
    assert resolved == Counter()


def test_multi_path_ambiguity_fails_safe() -> None:
    """S7：同指纹多文件歧义（2:2 乱序）——不映射，全报新增。"""

    baseline = Counter(
        {
            _key("src/p1.py", "F401", "unused import", "import os"): 1,
            _key("src/p2.py", "F401", "unused import", "import os"): 1,
        }
    )
    current = Counter(
        {
            _key("src/p3.py", "F401", "unused import", "import os"): 1,
            _key("src/p4.py", "F401", "unused import", "import os"): 1,
        }
    )

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 0
    assert new == current
    assert resolved == Counter()


def test_pure_new_and_pure_resolved_unchanged() -> None:
    """纯新增（0:1）与纯已解决（1:0）——无配对，行为与朴素差值一致。"""

    new_only = Counter({_key("src/new.py", "F401", "unused import", "import os"): 1})
    resolved_only = Counter({_key("src/old.py", "E501", "line too long", "x = 1"): 1})

    new, resolved, moved = move_aware_difference(Counter(), new_only)
    assert moved == 0
    assert new == new_only
    assert resolved == Counter()

    new, resolved, moved = move_aware_difference(resolved_only, Counter())
    assert moved == 0
    assert new == Counter()
    assert resolved == resolved_only


def test_identical_sets_no_change() -> None:
    """基线 = 当前——0 新增 0 已解决。"""

    counts = Counter({_key("src/a.py", "F401", "unused import", "import os"): 2})
    new, resolved, moved = move_aware_difference(counts, counts)

    assert moved == 0
    assert new == Counter()
    assert resolved == Counter()


def test_result_has_no_zero_count_keys() -> None:
    """返回值不含 0 值键——移动映射剔除后剩余计数为正，可安全迭代打印。"""

    baseline = Counter({_key("src/a.py", "F401", "unused import", "import os"): 1})
    current = Counter({_key("src/b.py", "F401", "unused import", "import os"): 1})

    new, resolved, moved = move_aware_difference(baseline, current)

    assert moved == 1
    assert all(count > 0 for count in new.values())
    assert all(count > 0 for count in resolved.values())
