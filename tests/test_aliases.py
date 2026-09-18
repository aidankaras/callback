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
