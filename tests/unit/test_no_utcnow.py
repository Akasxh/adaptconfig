"""Verify no deprecated datetime.utcnow() calls remain in Python source files."""

from pathlib import Path


class TestNoDeprecatedDatetime:
    def test_no_utcnow_in_src(self) -> None:
        src_dir = Path(__file__).parents[2] / "src"
        violations = []
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text()
            if "datetime.utcnow()" in content:
                violations.append(str(py_file))
        assert violations == [], (
            f"datetime.utcnow() found in: {', '.join(violations)}. "
            "Use datetime.now(timezone.utc) instead."
        )
