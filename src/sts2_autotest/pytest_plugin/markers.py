"""Custom pytest markers for STS2-AUTOTEST (FR52, FR56)."""

MARKERS: list[tuple[str, str]] = [
    ("sts2_state", "Test that depends on a specific game state"),
    ("sts2_adapter", "Test that requires an adapter connection"),
    ("sts2_timeout", "Test with a custom timeout value"),
]
