import pytest

from core.checks.grounding import FindingKind, Verdict, check_bullet, check_document
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
    # `Finding.kind` is a `FindingKind` member, not a bare string; StrEnum
    # equality with its string form is what keeps the assertion above (and
    # every other `== "some_string"` comparison in this file) working
    # unchanged now that the field is enum-typed.
    assert findings[0].kind is FindingKind.MISSING_SOURCE


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


def test_web_sourced_fact_cannot_back_a_bullet() -> None:
    """No test previously drove `check_bullet` with a `trust=web` fact, so a
    regression letting web-sourced content back generated text would have
    passed every other test in this file."""
    f = Fact.model_validate(
        {
            "id": "web1",
            "kind": FactKind.EXPERIENCE_DETAIL,
            "text": "x",
            "provenance": Provenance(source="web:https://example.com", trust=Trust.WEB),
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
    verdict, findings = check_bullet("Built ingestion jobs in Python against PostgreSQL", [cited])
    assert verdict is Verdict.PASS
    assert findings == []

    uncited = fact("s2", entities=[])
    verdict, findings = check_bullet("Built ingestion jobs in Python against PostgreSQL", [uncited])
    assert verdict is Verdict.FAIL
    entity_kinds = {f.detail for f in findings if f.kind == "unknown_entity"}
    assert len(entity_kinds) == 2


def test_p99_is_not_flagged_as_an_entity_but_cpp_is() -> None:
    """The uppercase requirement in the identifier-shape rule is load-bearing.

    Omitting it would flag 'p99' as an entity and fail a truthful latency
    bullet; only the capitalised, identifier-shaped 'C++' is checkable here.
    Both assertions check the overall verdict, not just a finding slice: an
    earlier version of this test only checked for the absence/presence of
    `unknown_entity` findings and passed even while the bullet failed
    end-to-end on an unrelated `unresolved_number` finding for '99' — a
    defect in `parse_numbers`, since fixed, not in this gate.
    """
    uncited = fact("s3", entities=[])
    verdict, findings = check_bullet("Improved p99 latency on the C++ service", [uncited])
    assert verdict is Verdict.FAIL
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 1
    assert "C++" in entity_findings[0].detail

    cited = fact("s4", entities=["C++"])
    verdict, findings = check_bullet("Improved p99 latency on the C++ service", [cited])
    assert verdict is Verdict.PASS
    assert findings == []


def test_bare_percentile_tokens_check_nothing() -> None:
    f = fact("s5", entities=[])
    verdict, findings = check_bullet("Cut p50 and p95 tail latency", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_lowercase_alias_of_a_technology_is_checked() -> None:
    f = fact("s6", entities=[])
    verdict, findings = check_bullet("Tuned postgres query plans", [f])
    assert verdict is Verdict.FAIL
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 1
    assert "PostgreSQL" in entity_findings[0].detail


def test_shipped_s3_and_greet_are_both_checked() -> None:
    uncited = fact("s7", entities=[])
    verdict, findings = check_bullet("Shipped S3 exports and GREET updates", [uncited])
    assert verdict is Verdict.FAIL
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 2

    cited = fact("s8", entities=["S3", "GREET"])
    verdict, findings = check_bullet("Shipped S3 exports and GREET updates", [cited])
    assert verdict is Verdict.PASS
    assert findings == []


def test_hyphenated_compound_with_a_cited_component_passes() -> None:
    """'S3-backed' tokenises as one word — the alias table needs hyphens kept
    intact for 'scikit-learn' — so a whole-token match against the cited
    entities fails even though 'S3' itself is cited. 'backed' is not itself
    checkable (no digit, not all-caps, not in the alias table), so 'S3' is
    the only component that has to match, and it does.
    """
    f = fact("s8b", entities=["S3"])
    verdict, findings = check_bullet("Built an S3-backed store", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_hyphen_does_not_hide_an_uncited_checkable_component() -> None:
    """A hyphen must not let one checkable component vouch for another.

    Both 'S3' and 'H100' are identifier-shaped on their own, so writing them
    joined by a hyphen must not be more permissive than writing them apart:
    'Deployed on S3 H100 nodes' would fail on the uncited 'H100', and so
    must the hyphenated form. Accepting on any single component match
    (an earlier version of this rule) let a hyphen hide an uncited
    technology sitting next to a cited one — this is the case that defect
    would have let through silently.
    """
    f = fact("s8c", entities=["S3"])
    verdict, findings = check_bullet("Deployed on S3-H100 nodes", [f])
    assert verdict is Verdict.FAIL
    entity_findings = [f for f in findings if f.kind == "unknown_entity"]
    assert len(entity_findings) == 1
    assert "H100" in entity_findings[0].detail


def test_hyphen_passes_when_every_checkable_component_is_cited() -> None:
    f = fact("s8d", entities=["S3", "H100"])
    verdict, findings = check_bullet("Deployed on S3-H100 nodes", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_hyphenated_alias_table_entry_matches_as_a_whole_token() -> None:
    f = fact("s8e", entities=["scikit-learn"])
    verdict, findings = check_bullet("Used scikit-learn pipelines", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_hyphenated_alias_table_entry_fails_when_not_cited() -> None:
    f = fact("s8f", entities=["Python"])
    verdict, findings = check_bullet("Used scikit-learn pipelines", [f])
    assert verdict is Verdict.FAIL
    assert any(f.kind == "unknown_entity" for f in findings)


def test_a_capitalised_hyphen_component_falls_to_the_name_rule() -> None:
    """'Redshift' is neither a known technology nor identifier-shaped, so the
    entity rule ignores it -- but it is a capitalised name mid-sentence, so
    it must be stated by a cited fact, hyphen or no hyphen."""
    f = fact("s8g", entities=["S3"])
    verdict, findings = check_bullet("Built an S3-Redshift sync", [f])
    assert verdict is Verdict.FAIL
    assert [(x.kind, x.detail) for x in findings] == [
        (FindingKind.UNSUPPORTED_NAME, "'Redshift' appears in no cited fact")
    ]
    stated = fact("s8h", text="Synced S3 into Redshift", entities=["S3"])
    assert check_bullet("Built an S3-Redshift sync", [stated]) == (Verdict.PASS, [])


def test_a_company_name_mid_sentence_must_be_stated_by_a_cited_fact() -> None:
    f = fact("s9", entities=[])
    verdict, findings = check_bullet("Coordinated with Northwind Analytics stakeholders", [f])
    assert verdict is Verdict.FAIL
    assert [x.detail for x in findings] == [
        "'Northwind' appears in no cited fact",
        "'Analytics' appears in no cited fact",
    ]
    stated = fact("s9b", text="Worked with Northwind Analytics on reporting")
    assert check_bullet("Coordinated with Northwind Analytics stakeholders", [stated]) == (
        Verdict.PASS,
        [],
    )


def test_a_spelled_out_number_must_be_sourced() -> None:
    f = fact("s10", entities=[])
    verdict, findings = check_bullet("Led the migration across three separate teams", [f])
    assert verdict is Verdict.FAIL
    assert [x.kind for x in findings] == [FindingKind.UNRESOLVED_NUMBER]
    counted = fact("s10b", numbers={"teams": Quantity(value=3)})
    assert check_bullet("Led the migration across three separate teams", [counted]) == (
        Verdict.PASS,
        [],
    )


def test_wrote_python_312_tooling_checks_only_python() -> None:
    f = fact("s11", entities=["Python"], numbers={"v": Quantity(value=3.12)})
    verdict, findings = check_bullet("Wrote Python 3.12 tooling", [f])
    assert verdict is Verdict.PASS
    assert findings == []


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


# --- Ruling 4: a bare year needs surrounding date context, not just hull
# membership -------------------------------------------------------------
#
# Before this ruling, any 1900-2100 integer inside the hull was accepted as a
# year regardless of what surrounded it, which let a plain dollar amount,
# unit or count that happened to fall in a plausible year range slip through
# ungrounded ("Cut the $2020 licensing cost" against a fact dated
# 2018-2024). Accepting/rejecting is decided from the text immediately
# around the number, not from the number's value alone.


def test_from_to_range_accepts_both_endpoints_as_years() -> None:
    f = fact("y1", dates=Dates(start="2018", end="2024"))
    verdict, findings = check_bullet("Worked there from 2018 to 2024", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_hyphen_range_accepts_both_endpoints_as_years() -> None:
    f = fact("y2", dates=Dates(start="2018", end="2024"))
    verdict, findings = check_bullet("Employed 2018-2024 as an analyst", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_en_dash_range_accepts_both_endpoints_as_years() -> None:
    f = fact("y3", dates=Dates(start="2018", end="2024"))
    verdict, findings = check_bullet("Employed 2018 – 2024 as an analyst", [f])  # noqa: RUF001
    assert verdict is Verdict.PASS
    assert findings == []


def test_month_prefixed_range_accepts_both_endpoints_as_years() -> None:
    f = fact("y4", dates=Dates(start="2024-06", end="2025-05"))
    verdict, findings = check_bullet("Jun 2024 - May 2025: built the pipeline", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_in_preposition_accepts_a_year() -> None:
    f = fact("y5", dates=Dates(start="2022", end="2024"))
    verdict, findings = check_bullet("In 2023 shipped the loader", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_until_preposition_accepts_a_year() -> None:
    f = fact("y6", dates=Dates(start="2020", end="2022"))
    verdict, findings = check_bullet("Ran the programme until 2022", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_since_preposition_accepts_a_year() -> None:
    f = fact("y7", dates=Dates(start="2019", end="2024"))
    verdict, findings = check_bullet("Since 2019 owned the pipeline", [f])
    assert verdict is Verdict.PASS
    assert findings == []


def test_leading_range_with_no_preceding_text_accepts_both_endpoints() -> None:
    f = fact("y8", dates=Dates(start="2018", end="2024"), text="three promotions")
    verdict, findings = check_bullet("2018 to 2024, three promotions", [f])
    assert verdict is Verdict.PASS
    assert findings == []


# Each of these is an ordinary quantity that happens to fall in 1900-2100 and
# inside the hull below; none of it is preceded or followed by year-marking
# context, so it must be sourced like any other number and fail when it
# isn't.
_UNSOURCED_HULL = Dates(start="2018-01", end="2024-12")


def test_dollar_amount_that_looks_like_a_year_is_not_accepted() -> None:
    f = fact("y9", dates=_UNSOURCED_HULL)
    verdict, findings = check_bullet("Cut the $2020 licensing cost", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_unit_quantity_that_looks_like_a_year_is_not_accepted() -> None:
    f = fact("y10", dates=_UNSOURCED_HULL)
    verdict, findings = check_bullet("Reduced spend by 2020 dollars", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_bare_count_that_looks_like_a_year_is_not_accepted() -> None:
    f = fact("y11", dates=_UNSOURCED_HULL)
    verdict, findings = check_bullet("Handled 2020 tickets", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_count_last_quarter_that_looks_like_a_year_is_not_accepted() -> None:
    f = fact("y12", dates=_UNSOURCED_HULL)
    verdict, findings = check_bullet("Processed 2024 invoices last quarter", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_to_preposition_before_a_rate_is_not_accepted_as_a_year() -> None:
    """A bare 'to' must not be treated as a year-range preposition: it is
    also the ordinary preposition in "improved X to Y units", which would
    otherwise wrongly exempt Y from being sourced. The hull deliberately
    contains 1999 so this fails on missing context, not on being outside
    the hull.
    """
    f = fact("y13", dates=Dates(start="1995", end="2005"))
    verdict, findings = check_bullet("Improved throughput to 1999 requests per second", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_to_preposition_before_a_latency_value_is_not_accepted_as_a_year() -> None:
    """The hull deliberately contains 2000 so this fails on missing context,
    not on being outside the hull."""
    f = fact("y14", dates=Dates(start="1995", end="2005"))
    verdict, findings = check_bullet("Cut latency to 2000 ms", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


def test_plain_count_that_looks_like_a_year_is_not_accepted() -> None:
    """The hull deliberately contains 1950 so this fails on missing context,
    not on being outside the hull."""
    f = fact("y15", dates=Dates(start="1945", end="1955"))
    verdict, findings = check_bullet("Shipped 1950 units", [f])
    assert verdict is Verdict.FAIL
    assert findings[0].kind == "unresolved_number"


# --- values_match's zero branch, exercised through the gate, not just the
# unit test in test_numbers.py -----------------------------------------------


def test_a_stored_zero_grounds_a_claimed_reduction_to_zero() -> None:
    """`values_match` falls back to an absolute check when `source == 0`,
    since the relative-tolerance formula divides by it. No grounding-level
    fixture previously drove a fact with a zero-valued number through
    `check_bullet`, so a regression in that branch would only have been
    caught by the unit test, not by the gate that actually depends on it."""
    f = fact("z1", numbers={"errors": Quantity(value=0)})
    verdict, findings = check_bullet("Cut the error count to 0", [f])
    assert verdict is Verdict.PASS
    assert findings == []


# --- documented verdict priority: derived over alias over plain pass --------


def test_derived_and_alias_together_prefers_derived_verified() -> None:
    """A bullet that both uses an alias of a cited entity and relies on a
    recomputed derivation must report DERIVED_VERIFIED, per the priority
    `check_bullet` documents -- not ALIAS_MATCH, and not a tie."""
    f = fact(
        "z2",
        entities=["PostgreSQL"],
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    verdict, findings = check_bullet("Tuned Postgres and cut load time 40%", [f])
    assert verdict is Verdict.DERIVED_VERIFIED
    assert findings == []


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
    # A malformed derivation names a broken artifact in the profile itself,
    # not an unresolved number in the text, so it gets its own finding kind.
    assert findings[0].kind == "invalid_derivation"
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


# --- nothing to ground is not the same as grounded ----------------------------


def test_empty_document_fails_rather_than_passing_vacuously() -> None:
    verdict, findings = check_document([])
    assert verdict is Verdict.FAIL
    assert [(f.bullet_index, f.kind) for f in findings] == [(-1, FindingKind.EMPTY_DOCUMENT)]


@pytest.mark.parametrize(
    "text", ["", "   ", "\n\t", "\u200b", "\xad", "\u2014", "...", "\u2022", "-"]
)
def test_blank_bullet_fails_even_with_a_valid_source(text: str) -> None:
    verdict, findings = check_bullet(text, [fact("a")], index=4)
    assert verdict is Verdict.FAIL
    assert [(f.bullet_index, f.kind) for f in findings] == [(4, FindingKind.EMPTY_BULLET)]


def test_one_grounded_bullet_still_passes_after_the_empty_guards() -> None:
    f = fact("a", numbers={"queue": Quantity(value=300)})
    assert check_document([("Cut the review queue to 300 items", [f])]) == (Verdict.PASS, [])


# --- fabrications that carry no digit and no acronym -------------------------------

NORTHWIND = fact(
    "nw",
    text="Built reporting pipelines at Northwind Analytics in Python; cut latency 42%",
    entities=["Northwind Analytics", "Python"],
    numbers={"latency": Quantity(value=42, unit="%")},
)


@pytest.mark.parametrize(
    ("bullet", "kind"),
    [
        ("Spearheaded the Google partnership", FindingKind.UNSUPPORTED_NAME),
        ("Won the Turing Award for Python pipelines", FindingKind.UNSUPPORTED_NAME),
        ("Personally closed the JPMorgan Chase account", FindingKind.UNSUPPORTED_NAME),
        ("Promoted to Principal Engineer", FindingKind.UNSUPPORTED_CLAIM_WORD),
        ("Doubled revenue across hundreds of clients", FindingKind.UNSUPPORTED_CLAIM_WORD),
        ("Led a team of twelve engineers", FindingKind.UNRESOLVED_NUMBER),
        ("Graduated summa cum laude", FindingKind.UNSUPPORTED_CLAIM_WORD),
        ("Managed 42 direct reports at Northwind Analytics", FindingKind.UNRESOLVED_NUMBER),
    ],
)
def test_an_unstated_name_claim_or_quantity_fails(bullet: str, kind: FindingKind) -> None:
    verdict, findings = check_bullet(bullet, [NORTHWIND])
    assert verdict is Verdict.FAIL
    assert kind in [f.kind for f in findings], findings


@pytest.mark.parametrize(
    "bullet",
    [
        "Built reporting pipelines at Northwind Analytics",
        "Cut latency 42% on the reporting pipelines",
        "Rewrote the loader in Python. Built reporting pipelines",
        "Processed 1.2M records in March",
    ],
)
def test_what_the_facts_state_still_passes(bullet: str) -> None:
    records = fact(
        "r", numbers={"records": Quantity(value=1_200_000)}, dates=Dates(start="2024-01")
    )
    assert check_bullet(bullet, [NORTHWIND, records]) == (Verdict.PASS, [])


def test_a_percent_backs_a_percent_but_not_a_count() -> None:
    """Mutation: match counts against every quantity -- the count case passes."""
    assert check_bullet("Cut latency 42%", [NORTHWIND])[0] is Verdict.PASS
    assert check_bullet("Onboarded 42 accounts", [NORTHWIND])[0] is Verdict.FAIL


def test_a_claim_word_stated_in_the_fact_passes() -> None:
    promoted = fact("p", text="Promoted to Senior Analyst in 2024")
    assert check_bullet("Earned a promotion; promoted to Senior Analyst", [promoted])[0] is (
        Verdict.FAIL
    )
    assert check_bullet("Promoted to Senior Analyst", [promoted]) == (Verdict.PASS, [])


def test_a_technology_ending_a_sentence_is_not_misread_as_an_identifier() -> None:
    """Mutation: keep the trailing full stop on tokens -- this test fails."""
    f = fact("py", entities=["Python", "Node.js"])
    assert check_bullet("Rewrote it in Python. Then ported it to Node.js.", [f]) == (
        Verdict.PASS,
        [],
    )


# --- an ongoing role has no end date ----------------------------------------------------


def _dated(fid: str, dates: Dates | None) -> Fact:
    return Fact(
        id=fid,
        kind=FactKind.EXPERIENCE_DETAIL,
        text="Built the reporting dashboard",
        dates=dates,
        provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
    )


ONGOING = _dated("now", Dates(start="2023-01"))
CLOSED = _dated("then", Dates(start="2020-01", end="2021-06"))


@pytest.mark.parametrize(
    ("text", "cited", "grounded"),
    [
        ("Built the reporting dashboard in 2024", [ONGOING], True),
        ("Built the reporting dashboard in March 2024", [ONGOING], True),
        # Cited beside a finished role, the current one is still unbounded.
        ("Built the reporting dashboard in 2024", [ONGOING, CLOSED], True),
        ("Built the reporting dashboard in March 2024", [ONGOING, CLOSED], True),
        # The open end does not open the start.
        ("Built the reporting dashboard in 2019", [ONGOING], False),
        ("Built the reporting dashboard in 2024", [CLOSED], False),
        # No dates at all ground no year.
        ("Built the reporting dashboard in 2024", [_dated("x", None)], False),
    ],
)
def test_a_year_is_grounded_by_an_ongoing_role(
    text: str, cited: list[Fact], grounded: bool
) -> None:
    """Mutations: treat a missing end as disqualifying -- the ongoing cases
    fail; take the closed fact's end for the hull -- the mixed cases fail."""
    _, found = check_bullet(text, cited)
    assert (found == []) is grounded, found


def _counted(value: float, unit: str | None) -> Fact:
    return Fact(
        id="q",
        kind=FactKind.EXPERIENCE_DETAIL,
        text="Changed the churn rate",
        numbers={"n": Quantity(value=value, unit=unit)},
        provenance=Provenance(source="x", trust=Trust.USER_CONFIRMED),
    )


@pytest.mark.parametrize(
    ("text", "fact", "grounded"),
    [
        ("Changed the churn rate by -15%", _counted(15, "%"), True),
        ("Changed the churn rate by 15%", _counted(-15, "%"), True),
        ("Changed the churn rate by -3 points", _counted(-3, None), True),
        ("Changed the churn rate by -3 points", _counted(3, None), False),
    ],
)
def test_a_percent_matches_by_magnitude_and_a_count_by_sign(
    text: str, fact: Fact, grounded: bool
) -> None:
    """Mutations: compare percents signed -- the first two fail; compare counts
    by magnitude -- the last passes."""
    _, found = check_bullet(text, [fact])
    assert (found == []) is grounded, found


def test_a_written_minus_on_a_derived_percentage_is_still_verified() -> None:
    """pct_change's sign says which input came first, not whether the change
    was a cut, so a percentage is compared by magnitude on both sides.
    Mutation: compare the claimed side signed -- "-40%" fails."""
    f = fact(
        "c",
        numbers={"before": Quantity(value=500), "after": Quantity(value=300)},
        derived=[
            Derivation.model_validate(
                {"label": "cut", "op": "pct_change", "from": ["before", "after"]}
            )
        ],
    )
    # Premise: the derivation's value is +40 for this reduction, so a signed
    # comparison against a claimed -40 is exactly the mismatch under test.
    from core.checks.derive import compute

    assert compute(f.derived[0], [f]) == pytest.approx(40.0)
    verdict, findings = check_bullet("Changed review volume by -40%", [f])
    assert findings == []
    assert verdict is Verdict.DERIVED_VERIFIED
