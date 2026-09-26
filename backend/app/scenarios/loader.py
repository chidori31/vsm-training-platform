import json
from pathlib import Path

from .schema import ScenarioDocument

MAX_DOCUMENT_BYTES = 1024 * 1024


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_document(path: Path) -> ScenarioDocument:
    with path.open("rb") as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Scenario exceeds the 1 MiB document limit")
    # JSONB would silently collapse duplicate keys; reject them before validation.
    json.loads(raw, object_pairs_hook=_unique_keys)
    return ScenarioDocument.model_validate_json(raw)
