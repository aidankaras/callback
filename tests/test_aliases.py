from pathlib import Path

import yaml

from core.aliases import canonicalize, synonyms


def test_known_alias_maps_to_canonical() -> None:
    assert canonicalize("Postgres") == "PostgreSQL"
    assert canonicalize("postgres") == "PostgreSQL"
    assert canonicalize("sklearn") == "scikit-learn"
    assert canonicalize("k8s") == "Kubernetes"


def test_unknown_entity_is_returned_unchanged() -> None:
    assert canonicalize("GREET") == "GREET"


def test_canonical_form_is_idempotent() -> None:
    assert canonicalize(canonicalize("TF")) == canonicalize("TF")


def test_synonyms_include_the_canonical_form() -> None:
    assert {"postgresql", "postgres"} <= {s.lower() for s in synonyms("PostgreSQL")}


def test_canonical_names_are_case_insensitively_sorted() -> None:
    """Verify table.yaml canonical names are sorted case-insensitively.

    The table is maintained by hand; case-insensitive sorting preserves
    readability (avoiding ASCII-sort neighbours like 'scikit-learn', 'SQL').
    """
    table_path = Path(__file__).parent.parent / "core" / "aliases" / "table.yaml"
    raw: dict[str, list[str]] = yaml.safe_load(table_path.read_text(encoding="utf-8"))
    keys = list(raw.keys())
    assert keys == sorted(keys, key=str.lower)
