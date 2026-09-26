import json
import subprocess
import sys


def test_validate_cli_reports_valid_document_without_database(tmp_path, document):
    path = tmp_path / "test.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "app.scenarios", "validate", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "synthetic-test@1" in result.stdout


def test_validate_cli_fails_on_invalid_document(tmp_path, document):
    document["nodes"][0]["choices"][0]["destination"] = "ghost"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "app.scenarios", "validate", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Unknown target node" in result.stderr
    assert "Traceback" not in result.stderr
