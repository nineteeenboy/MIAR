"""Запуск только unit-тестов; Docker и запущенные API не требуются."""
from pathlib import Path
import sys

import pytest

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    unit = root / "tests" / "unit"
    results = root / "test-results"
    results.mkdir(exist_ok=True)
    options = [
        "-c", str(unit / "pytest.ini"),
        "--confcutdir", str(unit),
        str(unit), "-v", "--tb=short", "--color=no",
        "--junitxml", str(results / "unit-tests.xml"),
    ]
    raise SystemExit(pytest.main(options + sys.argv[1:]))
