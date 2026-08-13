"""Case registry — maps case_id to CaseDefinition for modular discovery.

Core layer (协议层 B20). Supports:
- Register/de-register/resolve cases by ID
- Tag-based suite selection
- mod_id-based filtering
- Simple ActionDescriptor sequences and complex async runner callbacks

Framework manages orchestration; cases manage their own logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable

from sts2_autotest.core.action_model import ActionDescriptor, TestResult
from sts2_autotest.core.orchestrator import TestOrchestrator

# Type alias for programmatic case runners
CaseRunner = Callable[[TestOrchestrator], Awaitable[TestResult]]


@dataclass
class CaseDefinition:
    """Definition of a single test case in the registry.

    Two mutually exclusive execution modes:
    - actions: static ActionDescriptor sequence (simple linear flow)
    - runner: async callback with full Orchestrator access (complex stateful flow)
    """
    case_id: str
    mod_id: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    source_path: str = ""
    actions: list[ActionDescriptor] = field(default_factory=list)
    runner: CaseRunner | None = None
    expected: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.actions and self.runner is None:
            raise ValueError(
                f"CaseDefinition '{self.case_id}' must provide "
                "actions or runner"
            )


class CaseRegistry:
    """Global registry mapping case_id → CaseDefinition.

    Singleton-like: all register/resolve operations go to the class-level
    _cases dict. This lets MOD projects register cases at import time
    without needing a framework lifecycle hook.
    """

    _cases: dict[str, CaseDefinition] = {}

    @classmethod
    def register(cls, case: CaseDefinition) -> None:
        """Register a case. Raises ValueError on duplicate case_id."""
        if case.case_id in cls._cases:
            raise ValueError(f"Duplicate case_id: {case.case_id}")
        cls._cases[case.case_id] = case

    @classmethod
    def deregister(cls, case_id: str) -> None:
        """Remove a registered case. No-op if not found."""
        cls._cases.pop(case_id, None)

    @classmethod
    def resolve(cls, case_id: str) -> CaseDefinition:
        """Find a case by ID. Raises KeyError if not found."""
        if case_id not in cls._cases:
            raise KeyError(f"Case not found: {case_id}")
        return cls._cases[case_id]

    @classmethod
    def list_all(cls) -> list[str]:
        """Return all registered case IDs in insertion order."""
        return list(cls._cases.keys())

    @classmethod
    def list_by_mod(cls, mod_id: str) -> list[str]:
        """Return case IDs belonging to a specific MOD."""
        return [
            cid for cid, c in cls._cases.items()
            if c.mod_id == mod_id
        ]

    @classmethod
    def list_by_tags(cls, tags: set[str]) -> list[str]:
        """Return case IDs matching ALL given tags (intersection)."""
        return [
            cid for cid, c in cls._cases.items()
            if tags.issubset(set(c.tags))
        ]

    @classmethod
    def count(cls) -> int:
        """Return total registered cases."""
        return len(cls._cases)

    @classmethod
    def clear(cls) -> None:
        """Clear all registered cases. Used in tests and reset."""
        cls._cases.clear()
