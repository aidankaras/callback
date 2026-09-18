from core.checks.grounding import Verdict, check_bullet, check_document
from core.profile.models import Dates, Derivation, Fact, FactKind, Provenance, Quantity, Trust

PROV = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)


def fact(fid: str, **kw: object) -> Fact:
    body: dict[str, object] = {
        "id": fid,
        "kind": FactKind.EXPERIENCE_DETAIL,
        "text": "x",
        "provenance": PROV,
    }
    body.update(kw)
    return Fact.model_validate(body)


# --- brief cases -----------------------------------------------------------


def test_literal_number_passes() -> None:
    f = fact("a", numbers={"queue": Quantity(value=300)})
    verdict, findings = check_bullet("Cut the review queue to 300 items", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_magnitude_form_of_a_literal_passes() -> None:
    f = fact("b", numbers={"records": Quantity(value=1_200_000)})
    verdict, findings = check_bullet("Processed 1.2M records nightly", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_derived_percentage_is_verified_not_rejected() -> None:
    f = fact(
        "c",
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    verdict, findings = check_bullet("Cut review volume 40%", [f])
    assert verdict is Verdict.DERIVED_VERIFIED
    assert findings == []


def test_derived_percentage_with_the_wrong_arithmetic_fails() -> None:
    f = fact(
        "d",
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    verdict, findings = check_bullet("Cut review volume 75%", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_headcount_from_enumerated_entities_passes() -> None:
    f = fact(
        "e",
        entities=["Ana", "Ben", "Cleo"],
        derived=[Derivation.model_validate({"label": "team", "op": "count", "from": ["entities"]})],
    )
    verdict, findings = check_bullet("Led 3 engineers through the migration", [f])
    assert verdict is Verdict.DERIVED_VERIFIED
    assert findings == []


def test_alias_of_a_cited_entity_passes() -> None:
    f = fact("f", entities=["PostgreSQL"])
    verdict, findings = check_bullet("Tuned Postgres query plans", [f])
    assert verdict is Verdict.ALIAS_MATCH
    assert findings == []


def test_date_range_spanning_two_cited_facts_passes() -> None:
    one = fact("g", dates=Dates(start="2024-06", end="2024-08"))
    two = fact("h", dates=Dates(start="2024-09", end="2025-05"))
    verdict, findings = check_bullet("Jun 2024 - May 2025: built the pipeline", [one, two])
    assert verdict is Verdict.PASS
    assert findings == []


def test_invented_metric_fails() -> None:
    f = fact("i", numbers={})
    verdict, findings = check_bullet("Lifted retention 22%", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_unlisted_technology_fails() -> None:
    f = fact("j", entities=["Python"])
    verdict, findings = check_bullet("Deployed the model on Kubernetes", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unknown_entity"


def test_bullet_with_no_sources_fails() -> None:
    verdict, findings = check_bullet("Did something impressive", [])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "missing_source"


def test_unconfirmed_import_cannot_back_a_bullet() -> None:
    f = Fact.model_validate(
        {
            "id": "k",
            "kind": FactKind.EXPERIENCE_DETAIL,
            "text": "x",
            "provenance": Provenance(source="import:resume.pdf", trust=Trust.IMPORT_UNCONFIRMED),
        }
    )
    verdict, findings = check_bullet("Shipped the service", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unselectable_fact"


def test_false_positive_rate_on_the_measured_seven() -> None:
    """A prior containment-based rule failed six of these seven; only the last is a fabrication."""
    cases = [
        (
            "Led 3 engineers",
            [
                fact(
                    "l",
                    entities=["Ana", "Ben", "Cleo"],
                    derived=[
                        Derivation.model_validate(
                            {"label": "n", "op": "count", "from": ["entities"]}
                        )
                    ],
                )
            ],
        ),
        ("Jun 2024 - Aug 2025", [fact("m", dates=Dates(start="2024-06", end="2025-08"))]),
        ("Tuned Postgres", [fact("n", entities=["PostgreSQL"])]),
        (
            "Cut review volume 40%",
            [
                fact(
                    "o",
                    numbers={"b": Quantity(value=500), "a": Quantity(value=300)},
                    derived=[
                        Derivation.model_validate(
                            {"label": "c", "op": "pct_change", "from": ["b", "a"]}
                        )
                    ],
                )
            ],
        ),
        ("Processed 1.2M records", [fact("p", numbers={"r": Quantity(value=1_200_000)})]),
        (
            "Raised AUC 17%",
            [
                fact(
                    "q",
                    entities=["AUC"],
                    numbers={"b": Quantity(value=0.70), "a": Quantity(value=0.819)},
                    derived=[
                        Derivation.model_validate(
                            {"label": "g", "op": "pct_change", "from": ["b", "a"]}
                        )
                    ],
                )
            ],
        ),
    ]
    for text, sources in cases:
        verdict, findings = check_bullet(text, sources)
        assert verdict is not Verdict.FAIL, f"false positive on {text!r}: {findings}"
    verdict, _ = check_bullet("Lifted retention 22%", [fact("r")])
    assert verdict is Verdict.FAIL


# --- Ruling 1: the entity rule ----------------------------------------------


def test_entities_known_technologies_are_checked_case_insensitively() -> None:
    cited = fact("s1", entities=["Python", "PostgreSQL"])
    _, findings = check_bullet("Built ingestion jobs in Python against PostgreSQL", [cited])
    assert not any(f.kind == "unknown_entity" for f in findings)

    uncited = fact("s2", entities=[])
    _, findings = check_bullet("Built ingestion jobs in Python against PostgreSQL", [uncited])
    entity_kinds = {f.detail for f in findings if f.kind == "unknown_entity"}
    assert len(entity_kinds) == 2


def test_p99_is_not_flagged_as_an_entity_but_cpp_is() -> None:
    """The uppercase requirement in the identifier-shape rule is load-bearing.

    Omitting it would flag 'p99' as an entity and fail a truthful latency
    bullet; only the capitalised, identifier-shaped 'C++' is checkable here.
    """
    uncited = fact("s3", entities=[])
    _, findings = check_bullet("Improved p99 latency on the C++ service", [uncited])
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 1
    assert "C++" in entity_findings[0].detail

    cited = fact("s4", entities=["C++"])
    _, findings = check_bullet("Improved p99 latency on the C++ service", [cited])
    assert not any(f.kind == "unknown_entity" for f in findings)


def test_bare_percentile_tokens_check_nothing() -> None:
    f = fact("s5", entities=[])
    _, findings = check_bullet("Cut p50 and p95 tail latency", [f])
    assert not any(f.kind == "unknown_entity" for f in findings)


def test_lowercase_alias_of_a_technology_is_checked() -> None:
    f = fact("s6", entities=[])
    _, findings = check_bullet("Tuned postgres query plans", [f])
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 1
    assert "PostgreSQL" in entity_findings[0].detail


def test_shipped_s3_and_greet_are_both_checked() -> None:
    uncited = fact("s7", entities=[])
    _, findings = check_bullet("Shipped S3 exports and GREET updates", [uncited])
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 2

    cited = fact("s8", entities=["S3", "GREET"])
    _, findings = check_bullet("Shipped S3 exports and GREET updates", [cited])
    assert not any(f.kind == "unknown_entity" for f in findings)


def test_ordinary_capitalised_company_names_check_nothing() -> None:
    f = fact("s9", entities=[])
    _, findings = check_bullet("Coordinated with Northwind Analytics stakeholders", [f])
    assert not any(f.kind == "unknown_entity" for f in findings)


def test_led_three_separate_teams_checks_nothing() -> None:
    f = fact("s10", entities=[])
    _, findings = check_bullet("Led the migration across three separate teams", [f])
    assert not any(f.kind == "unknown_entity" for f in findings)


def test_wrote_python_312_tooling_checks_only_python() -> None:
    f = fact("s11", entities=["Python"], numbers={"v": Quantity(value=3.12)})
    _, findings = check_bullet("Wrote Python 3.12 tooling", [f])
    assert not any(f.kind == "unknown_entity" for f in findings)


# --- Ruling 2: percentages compared as magnitudes ---------------------------


def test_derived_percentage_reduction_direction_passes() -> None:
    f = fact(
        "s12",
        numbers={"before": Quantity(value=100), "after": Quantity(value=60)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    verdict, findings = check_bullet("Cut onboarding time 40%", [f])
    assert verdict is Verdict.DERIVED_VERIFIED
    assert findings == []


def test_derived_percentage_increase_direction_passes() -> None:
    f = fact(
        "s13",
        numbers={"before": Quantity(value=100), "after": Quantity(value=150)},
        derived=[
            Derivation.model_validate(
                {"label": "growth", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    verdict, findings = check_bullet("Grew signups 50%", [f])
    assert verdict is Verdict.DERIVED_VERIFIED
    assert findings == []


# --- Ruling 3: years compared as integers -----------------------------------


def test_bare_year_passes_against_a_month_precision_hull_end() -> None:
    f = fact("s14", dates=Dates(start="2024-09", end="2025-05"))
    verdict, findings = check_bullet("Completed the rollout in 2025", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_bare_year_outside_the_hull_fails() -> None:
    f = fact("s15", dates=Dates(start="2024-09", end="2025-05"))
    verdict, findings = check_bullet("Completed the rollout in 2026", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


# --- malformed derivations never raise --------------------------------------


def test_malformed_derivation_yields_a_finding_instead_of_raising() -> None:
    f = fact(
        "s16",
        numbers={"before": Quantity(value=500)},
        derived=[
            Derivation.model_validate(
                {"label": "bogus", "op": "difference", "from": ["before", "missing"]}
            )
        ],
    )
    verdict, findings = check_bullet("Cut something significant", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"
    assert "bogus" in findings[0].detail


# --- unresolved dates ---------------------------------------------------------


def test_date_outside_the_hull_fails() -> None:
    f = fact("s17", dates=Dates(start="2024-01", end="2024-06"))
    verdict, findings = check_bullet("Shipped it in Dec 2025", [f])
    assert verdict is Verdict.FAIL
    assert any(finding.kind == "unresolved_date" for finding in findings)


# --- check_document -----------------------------------------------------------


def test_check_document_fails_if_any_bullet_fails() -> None:
    ok = fact("s18", numbers={"queue": Quantity(value=300)})
    bad = fact("s19", numbers={})
    verdict, findings = check_document(
        [
            ("Cut the review queue to 300 items", [ok]),
            ("Lifted retention 22%", [bad]),
        ]
    )
    assert verdict is Verdict.FAIL
    assert any(f.bullet_index == 1 and f.kind == "unresolved_number" for f in findings)


def test_check_document_reports_derived_verified_when_no_bullet_fails() -> None:
    f = fact(
        "s20",
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    verdict, findings = check_document([("Cut review volume 40%", [f])])
    assert verdict is Verdict.DERIVED_VERIFIED
    assert findings == []
