import argparse
import os
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.persistence.scenarios import ScenarioRepository

from .loader import load_document
from .schema import ScenarioDocument


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and import scenario JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "import"):
        command = commands.add_parser(name)
        command.add_argument(
            "paths", nargs="+", type=Path, help="JSON files or directories"
        )
    export = commands.add_parser("export-schema")
    export.add_argument("path", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "export-schema":
            import json

            arguments.path.write_text(
                json.dumps(
                    ScenarioDocument.model_json_schema(), ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            return 0
        paths: list[Path] = []
        for path in arguments.paths:
            if path.is_dir():
                candidates = sorted(path.glob("*.json"))
                if not candidates:
                    raise ValueError(f"No scenario JSON files in {path}")
                paths.extend(candidates)
            else:
                paths.append(path)
        # Validate the complete batch before opening the database transaction.
        documents = []
        for path in paths:
            try:
                documents.append(load_document(path))
            except ValidationError as error:
                details = "; ".join(
                    f"{'.'.join(map(str, item['loc'])) or 'document'}: {item['msg']}"
                    for item in error.errors(include_url=False, include_input=False)
                )
                raise ValueError(f"{path}: {details}") from None
            except ValueError as error:
                raise ValueError(f"{path}: {error}") from None
        if arguments.command == "validate":
            for document in documents:
                print(f"Valid: {document.id}@{document.version}")
            return 0
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise ValueError("Set DATABASE_URL before importing scenarios")
        engine = create_engine(url)
        try:
            with Session(engine) as session, session.begin():
                repository = ScenarioRepository(session)
                inserted = sum(repository.add(document) for document in documents)
        finally:
            engine.dispose()
        print(f"Imported: {inserted}; unchanged: {len(documents) - inserted}")
        return 0
    except SQLAlchemyError:
        print(
            "Database operation failed; check connectivity and apply migrations.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
