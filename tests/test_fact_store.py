from pathlib import Path

import pytest

from core.paths import ensure_home, resolve_inside
from core.profile.models import Dates, Derivation, Fact, FactKind, Provenance, Quantity, Trust
from core.profile.store import (
    FACTS_FILE,
    SENSITIVE_PATTERNS,
    DuplicateFactError,
    SensitiveFieldError,
    StoreError,
    add_fact,
    load_facts,
    screen_sensitive,
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


def test_round_trip_preserves_dates_units_and_derivations() -> None:
    """The single-field assertions above don't exercise `Dates`, a non-`None`
    `Quantity.unit`, or the `from`/`from_` alias on `Derivation` — each of those
    only round-trips correctly if `save_facts`/`load_facts` serialize by alias and
    `model_dump` includes every field. Compare the whole reloaded fact to the
    original rather than picking fields, so a regression in any one of them fails
    this test instead of passing silently."""
    fact = _fact(
        dates=Dates(start="2024-01", end="2024-06", precision="month"),
        numbers={"records": Quantity(value=1_200_000, unit="rows")},
        derived=[
            Derivation.model_validate(
                {"label": "records per month", "op": "divide", "from": ["exp.lab.1"]}
            )
        ],
    )
    add_fact(fact)
    assert load_facts()["exp.lab.1"] == fact


def test_duplicate_id_is_rejected() -> None:
    add_fact(_fact())
    with pytest.raises(DuplicateFactError):
        add_fact(_fact())


# --- StoreError: every failure that reaches disk or the filesystem boundary
# must surface as this one declared type, never the underlying exception. ---


def test_duplicate_fact_error_is_a_store_error() -> None:
    """`DuplicateFactError` must itself satisfy the "one type" promise."""
    add_fact(_fact())
    with pytest.raises(StoreError):
        add_fact(_fact())


def test_sensitive_field_error_is_a_store_error() -> None:
    with pytest.raises(StoreError):
        add_fact(_fact(id="pii.store-error", text="SSN 123-45-6789"))


def test_add_fact_wraps_a_missing_data_home_as_store_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`add_fact` must not leak the bare `OSError`/`FileNotFoundError` that
    `write_private` raises when the data home was never created with
    `ensure_home()`. The `home` fixture already created one data home; this
    test points `CALLBACK_HOME` at a second, never-created directory."""
    monkeypatch.setenv("CALLBACK_HOME", str(tmp_path / "never-created"))
    with pytest.raises(StoreError):
        add_fact(_fact())


def test_load_facts_wraps_malformed_yaml_as_store_error() -> None:
    path = resolve_inside(FACTS_FILE)
    path.write_text("exp.lab.1: [unterminated flow sequence\n", encoding="utf-8")
    with pytest.raises(StoreError):
        load_facts()


def test_load_facts_rejects_a_non_mapping_top_level() -> None:
    """A facts file whose top level is a sequence, not a mapping, is a
    corrupt store -- it must be reported, not silently misread."""
    path = resolve_inside(FACTS_FILE)
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(StoreError):
        load_facts()


def test_load_facts_rejects_a_falsy_non_mapping_top_level() -> None:
    """`yaml.safe_load(...) or {}` previously read a falsy-but-wrong-type top
    level (e.g. an empty list) as "no facts yet", masking store corruption as
    an empty store. An empty *mapping* must still mean no facts."""
    path = resolve_inside(FACTS_FILE)
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(StoreError):
        load_facts()
    path.write_text("{}\n", encoding="utf-8")
    assert load_facts() == {}


def test_load_facts_wraps_a_non_mapping_fact_body_as_store_error() -> None:
    """The top level can be a mapping while a single fact's own body is not
    (e.g. a bare string under its id). Merging `{"id": fid}` into a non-dict
    body raises a bare `TypeError`, not `ValidationError` -- a second,
    distinct way a stored fact can fail to validate that must not leak
    past this module either."""
    path = resolve_inside(FACTS_FILE)
    path.write_text("exp.lab.1: just a string, not a fact body\n", encoding="utf-8")
    with pytest.raises(StoreError):
        load_facts()


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


def test_provenance_trust_cannot_be_mutated_after_construction() -> None:
    """`selectable()` only means anything if trust can't be bumped after the fact.

    Before `Provenance` was frozen, `fact.provenance.trust = Trust.USER_CONFIRMED`
    silently flipped an unconfirmed import into one that backs generated text,
    with no validator in the way -- a single attribute assignment defeated the
    trust gate entirely.
    """
    provenance = Provenance(source="import:resume.pdf", trust=Trust.IMPORT_UNCONFIRMED)
    with pytest.raises(ValueError):
        provenance.trust = Trust.USER_CONFIRMED


def test_fact_provenance_cannot_be_replaced_wholesale() -> None:
    """Freezing `Provenance` alone does not close this: replacing the whole
    `provenance` field with a differently-trusted one flips `selectable()`
    just as completely as mutating `trust` in place did. `Fact` itself must
    be frozen too.
    """
    fact = _fact(provenance=Provenance(source="import:resume.pdf", trust=Trust.IMPORT_UNCONFIRMED))
    assert not fact.selectable()
    with pytest.raises(ValueError):
        fact.provenance = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)


def test_sensitive_entity_is_rejected() -> None:
    with pytest.raises(SensitiveFieldError) as exc:
        add_fact(_fact(id="pii.3", entities=["Python", "SSN 123-45-6789"]))
    assert "entities[1]" in str(exc.value)


def test_sensitive_derivation_label_is_rejected() -> None:
    derivation = Derivation.model_validate(
        {"label": "Marital status summary", "op": "count", "from": ["exp.lab.1"]}
    )
    with pytest.raises(SensitiveFieldError) as exc:
        add_fact(_fact(id="pii.4", derived=[derivation]))
    assert "derived[0].label" in str(exc.value)


def test_ordinary_entities_are_not_rejected() -> None:
    add_fact(_fact(id="ok.1", entities=["Lakeshore State University", "Python"]))
    assert load_facts()["ok.1"].entities == ["Lakeshore State University", "Python"]


def test_absent_fact_records_a_declined_gap() -> None:
    add_fact(
        _fact(id="exp.lab.1.metric", kind=FactKind.ABSENT, text="no metric available", numbers={})
    )
    assert load_facts()["exp.lab.1.metric"].kind is FactKind.ABSENT


# One (self-attributing, ordinary-vocabulary) pair per label in SENSITIVE_PATTERNS. The
# positive case must be refused; the negative case is realistic technical or academic
# resume text that must be accepted despite superficially resembling the topic. This is
# what actually protects against an over-eager pattern: a bare topic word would fail the
# negative half even though it happily catches the positive half.
SENSITIVE_CASES: dict[str, tuple[str, str]] = {
    "date of birth": (
        "Date of birth: 12 March 2006",
        "Completed a certificate program before graduation",
    ),
    "age": ("Age: 34", "Led a cross-functional team of 12 engineers"),
    "ssn": ("SSN 123-45-6789", "Streamlined onboarding paperwork for new hires"),
    "salary history": (
        "Please see my salary history attached below",
        "Negotiated vendor contracts worth $85k annually",
    ),
    "marital status": (
        "I am married and have two children",
        "Married two datasets on a composite key",
    ),
    "religion": ("My religion is Buddhist", "BA in Religious Studies"),
    "disability": (
        "Disability status: none",
        "Disabled legacy authentication flow before rollout",
    ),
    "race or ethnicity": ("Race: Asian", "Fixed a race condition in the concurrency module"),
    "gender": ("Gender: female", "Managed gender-balanced hiring initiatives across the org"),
    "street address": ("123 Main Street, Apt 4", "Delivered the project two weeks ahead"),
}


def test_sensitive_cases_cover_every_pattern_label() -> None:
    """Every label in SENSITIVE_PATTERNS must have a case pair, so the table and its
    tests cannot silently drift apart as patterns are added or renamed."""
    assert set(SENSITIVE_CASES) == {label for label, _pattern in SENSITIVE_PATTERNS}


@pytest.mark.parametrize("label", [label for label, _pattern in SENSITIVE_PATTERNS])
def test_sensitive_pattern_refuses_self_attribution_and_accepts_vocabulary(label: str) -> None:
    positive, negative = SENSITIVE_CASES[label]
    with pytest.raises(SensitiveFieldError):
        screen_sensitive(positive, "text")
    screen_sensitive(negative, "text")  # must not raise


def test_generic_technical_bullets_are_not_rejected() -> None:
    for text in (
        "Improved accessibility for users with disabilities",
        "Reduced p99 latency by 40%",
    ):
        screen_sensitive(text, "text")


def test_entities_are_canonicalised_on_write() -> None:
    add_fact(_fact(id="exp.lab.2", entities=["postgres", "sklearn"]))
    assert load_facts()["exp.lab.2"].entities == ["PostgreSQL", "scikit-learn"]
