import subprocess
from pathlib import Path

from app.api.catalog import DemoLoginRequest
from app.application.demo_personas import PERSONAS


def test_demo_identity_catalog_needs_no_personal_contact_data():
    assert len(PERSONAS) == 5
    assert len({p["id"] for p in PERSONAS}) == 5
    for persona in PERSONAS:
        assert persona["id"].startswith("demo-")
        assert persona["display_name"].startswith("Учебный проводник ")
        assert persona["company_id"].startswith("demo-")
        assert set(persona) == {
            "id",
            "display_name",
            "company_id",
            "depot_id",
            "brigade_id",
        }
    assert set(DemoLoginRequest.model_fields) == {"persona_id"}


def test_private_env_is_ignored_and_only_example_is_tracked():
    root = Path(__file__).parents[2]
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=root, text=True
    ).splitlines()
    assert [p for p in tracked if Path(p).name.startswith(".env")] == [".env.example"]
    for path in (".env", ".env.production", "backend/.env"):
        assert (
            subprocess.run(
                ["git", "check-ignore", "--no-index", "-q", path], cwd=root
            ).returncode
            == 0
        )
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=\n" in example
    assert "CHANGE_ME" in example
