import os
import subprocess
import sys
from pathlib import Path


def test_domain_imports_without_any_site_packages():
    backend = Path(__file__).resolve().parents[2]
    script = """
import importlib
import pkgutil
import app.domain
for module in pkgutil.walk_packages(app.domain.__path__, app.domain.__name__ + '.'):
    importlib.import_module(module.name)
from app.domain.rules import Condition
from app.domain.scoring import ScoreState
assert Condition().matches(ScoreState({}))
"""
    result = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=backend,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "DATABASE_URL"}
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
