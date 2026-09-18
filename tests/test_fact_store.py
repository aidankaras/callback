from pathlib import Path

import pytest

from core.paths import ensure_home
from core.profile.models import Derivation, Fact, FactKind, Provenance, Quantity, Trust
from core.profile.store import (
    DuplicateFactError,
    SensitiveFieldError,
    add_fact,
    load_facts,
)


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "cb"))
    return ensure_home()


def _fact(**kw: object) -> Fact:
    base: dict[str, object] = {
        "id": "exp.lab.1",
        "kind": FactKind.EXPERIENCE_DETAIL,
        "text": "Ported the analysis module to Python",
        "numbers": {"records": Quantity(value=1_200_000, unit=None)},
        "entities": ["Python"],
        "provenance": Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED),
    }
    base.update(kw)
    return Fact.model_validate(base)


def test_round_trip_through_yaml() -> None:
    add_fact(_fact())
    loaded = load_facts()
    assert loaded["exp.lab.1"].numbers["records"].value == 1_200_000
    assert loaded["exp.lab.1"].provenance.trust is Trust.USER_CONFIRMED


def test_duplicate_id_is_rejected() -> None:
    add_fact(_fact())
    with pytest.raises(DuplicateFactError):
        add_fact(_fact())


def test_sensitive_content_is_rejected() -> None:
    with pytest.raises(SensitiveFieldError) as exc:
        add_fact(_fact(id="pii.1", text="Date of birth: 12 March 2006"))
    assert "date of birth" in str(exc.value).lower()


def test_sensitive_ssn_pattern_is_rejected() -> None:
    with pytest.raises(SensitiveFieldError):
        add_fact(_fact(id="pii.2", text="SSN 123-45-6789"))


def test_web_provenance_cannot_be_user_confirmed() -> None:
    with pytest.raises(ValueError):
        _fact(provenance=Provenance(source="web:https://example.com", trust=Trust.USER_CONFIRMED))


def test_import_provenance_cannot_be_user_confirmed() -> None:
    with pytest.raises(ValueError):
        _fact(provenance=Provenance(source="import:resume.pdf", trust=Trust.USER_CONFIRMED))


def test_sensitive_entity_is_rejected() -> None:
    with pytest.raises(SensitiveFieldError) as exc:
        add_fact(_fact(id="pii.3", entities=["Python", "Married"]))
    assert "entities[1]" in str(exc.value)


def test_sensitive_derivation_label_is_rejected() -> None:
    derivation = Derivation.model_validate(
        {"label": "Marital status summary", "op": "count", "from": ["exp.lab.1"]}
    )
    with pytest.raises(SensitiveFieldError) as exc:
        add_fact(_fact(id="pii.4", derived=[derivation]))
    assert "derived[0].label" in str(exc.value)


def test_ordinary_entities_are_not_rejected() -> None:
    add_fact(_fact(id="ok.1", entities=["Boston University", "Python"]))
    assert load_facts()["ok.1"].entities == ["Boston University", "Python"]


def test_absent_fact_records_a_declined_gap() -> None:
    add_fact(
        _fact(id="exp.lab.1.metric", kind=FactKind.ABSENT, text="no metric available", numbers={})
    )
    assert load_facts()["exp.lab.1.metric"].kind is FactKind.ABSENT
