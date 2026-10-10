from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.briefs import Brief, Exemplar, Source
from core.checks.grounding import checkable_entity_tokens
from core.checks.style import (
    PHRASE_ONLY,
    UNCONDITIONAL,
    copied_run,
    is_specific,
    resume_findings,
    stock_phrases,
    text_findings,
)
from core.gates import run_gates
from core.profile.models import Fact, FactKind, Identity, Provenance, Trust
from core.resume.models import Bullet, Entry, ResumeDoc, Section

CONFIRMED = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)
EXEMPLAR = "Cut nightly batch latency from 40 to 6 minutes by rewriting the Spark join"
BRIEF = Brief(
    field="quant-research",
    built_at=datetime(2026, 10, 10, 18, 0, tzinfo=UTC),
    sources=(
        Source(
            id="s1",
            kind="career-centre",
            publisher="Example University",
            title="Sample resumes",
            url="https://careers.example.edu/samples",
            fetched_at=datetime(2026, 10, 10, 17, 0, tzinfo=UTC),
        ),
    ),
    exemplars=(Exemplar(kind="bullet", text=EXEMPLAR, source="s1", why="System and result."),),
    phrases_to_avoid=("go-getter",),
)

# One planted case per list entry, in list order: every entry must be able to fail.
PLANTED = (
    ("Spearheaded the migration of 4 services", "Spearheaded"),
    ("A results-driven analyst with 3 internships", "results-driven"),
    ("Brings a proven track record across 2 teams", "proven track record"),
    ("Passionate about 3 open-source projects", "Passionate about"),
    ("Built cutting-edge models for 2 desks", "cutting-edge"),
    ("Thrived in a fast-paced environment of 12 analysts", "fast-paced environment"),
    ("A team player on a 6-person crew", "team player"),
    ("Detail-oriented auditor of 40 filings", "Detail-oriented"),
    ("I'm excited to apply for the analyst role", "I'm excited to apply"),
    ("I am writing to express my interest in the role", "I am writing to express"),
    ("Leveraged the cluster to train 3 models", "Leveraged the"),
    ("Leveraging state-of-the-art tools for 2 projects", "Leveraging state-of-the-art"),
    ("Delivered a robust solution for 5 clients", "robust solution"),
    ("Joined a dynamic team of 8", "dynamic team"),
    ("Produced data-driven insights for 3 desks", "data-driven insights"),
)


def test_every_list_entry_has_a_planted_case() -> None:
    assert len(PLANTED) == len(UNCONDITIONAL) + len(PHRASE_ONLY)


@pytest.mark.parametrize(("text", "phrase"), PLANTED)
def test_a_stock_phrase_fails(text: str, phrase: str) -> None:
    assert stock_phrases(text) == [phrase]


# Each is legitimate usage in the candidate's fields, and the word that would
# collide with a list entry is asserted present before the text is passed.
COLLISIONS = (
    ("Computed the leverage ratio across 40 funds", "leverage"),
    ("Estimated robust standard errors for 12 panel regressions", "robust"),
    ("Solved routing with dynamic programming in C++", "dynamic"),
    ("Drove a 3\u00d7 speed-up in the nightly ETL job", "drove"),
    ("Screened synergistic drug combinations across 200 cell lines", "synergistic"),
    ("Modelled retention for 2M Steam players", "team player"),
    ("Leveraging state-space models to forecast 3 series", "state"),
    ("Leveraging cutting-plane methods for 2 solvers", "cutting"),
    ("We measured the leverage the bank took on", "leverage the"),
    ("Modelled how hedge funds leverage the repo market", "leverage the"),
)


@pytest.mark.parametrize(("text", "word"), COLLISIONS)
def test_a_technical_term_is_not_a_stock_phrase(text: str, word: str) -> None:
    assert word in text.casefold()  # premise: the colliding word is there
    assert stock_phrases(text) == []


def test_a_brief_adds_its_own_phrases_whole_word() -> None:
    assert stock_phrases("A Go-Getter on 3 desks", BRIEF.phrases_to_avoid) == ["Go-Getter"]
    assert stock_phrases("go-getters", BRIEF.phrases_to_avoid) == []


# Each writes a listed phrase with a character a keyboard, a word processor or
# a copy from a web page can put there in place of the ASCII one.
UNICODE_FORMS = (
    ("Results\u2011driven analyst", "\u2011", "Results-driven"),  # non-breaking hyphen
    ("Built cutting\u2010edge models", "\u2010", "cutting-edge"),  # hyphen
    ("A team\u2013player on 6 crews", "\u2013", "team-player"),  # en dash
    ("Spear\u00adheaded 4 migrations", "\u00ad", "Spearheaded"),  # soft hyphen
    ("Spear\u200bheaded 4 migrations", "\u200b", "Spearheaded"),  # zero-width space
    ("I\u02bcm excited to apply", "\u02bc", "I'm excited to apply"),  # modifier apostrophe
    ("I\uff07m excited to apply", "\uff07", "I'm excited to apply"),  # fullwidth apostrophe
)


@pytest.mark.parametrize(("text", "char", "phrase"), UNICODE_FORMS)
def test_a_stock_phrase_written_with_unicode_punctuation_fails(
    text: str, char: str, phrase: str
) -> None:
    assert char in text and not char.isascii()  # premise: the special character is there
    assert stock_phrases(text) == [phrase]


def test_a_date_range_with_an_en_dash_is_not_a_stock_phrase() -> None:
    text = "Ran the 2019\u20132021 study"
    assert "\u2013" in text  # premise: the dash is folded like the ones above
    assert stock_phrases(text) == []


@pytest.mark.parametrize("text", ["results  driven", "results\ndriven", "results-driven"])
def test_a_listed_compound_matches_across_any_whitespace(text: str) -> None:
    assert stock_phrases(text) == [text]


def test_a_listed_compound_does_not_match_across_other_punctuation() -> None:
    assert stock_phrases("results+driven") == []


@pytest.mark.parametrize(
    ("phrase", "text"),
    [
        ("self-starter", "A self starter"),
        ("self starter", "A self-starter"),
        ("self\u2011starter", "A self-starter"),  # written with a non-breaking hyphen
    ],
)
def test_a_briefs_phrase_matches_hyphenated_or_spaced(phrase: str, text: str) -> None:
    assert phrase not in text  # premise: the text spells it the other way
    assert stock_phrases(text, [phrase]) == [text.removeprefix("A ")]


def test_a_soft_hyphen_does_not_hide_a_copied_run() -> None:
    text = "Cut nightly batch la\u00adtency from 40 to 9 minutes"
    assert "\u00ad" in text  # premise: the word is split by an invisible character
    assert copied_run(text, [EXEMPLAR]) == "cut nightly batch latency from 40"


def test_six_shared_words_are_copying_and_five_are_not() -> None:
    six = "Rewrote ETL; cut nightly batch latency from 40 to 9 minutes"
    five = "cut nightly batch latency from 12 minutes"
    assert copied_run(six, [EXEMPLAR]) == "cut nightly batch latency from 40"
    assert copied_run(five, [EXEMPLAR]) is None


def test_copying_an_exemplar_fails_only_against_the_named_brief() -> None:
    text = "Cut nightly batch latency from 40 to 6 minutes"
    assert text_findings("bullet 0", text, None) == []
    found = text_findings("bullet 0", text, BRIEF)
    assert len(found) == 1 and "copies an exemplar of the quant-research brief" in found[0]


def _fact(fid: str, text: str, entities: list[str]) -> Fact:
    return Fact(
        id=fid,
        kind=FactKind.EXPERIENCE_DETAIL,
        parent="role",
        text=text,
        entities=entities,
        provenance=CONFIRMED,
    )


def test_a_bullet_is_specific_by_a_digit_an_entity_or_an_alias() -> None:
    org = _fact("o", "Rebuilt the claims loader for Northwind Analytics", ["Northwind Analytics"])
    db = _fact("d", "Tuned queries in PostgreSQL", ["PostgreSQL"])
    literal = "Rebuilt the claims loader for Northwind Analytics"
    alias = "Tuned queries in Postgres"
    # Premises: neither carries a digit, so each is specific only by the route
    # it is named for -- the literal name is no checkable token, and the alias
    # is not the entity's spelling.
    assert not any(c.isdigit() for c in literal + alias)
    assert checkable_entity_tokens(literal) == []
    assert "postgresql" not in alias.casefold()
    assert is_specific("Shipped 4 loaders", [])
    assert is_specific(literal, [org])
    assert is_specific(alias, [db])
    assert not is_specific("Shipped the loader", [org, db])


@pytest.mark.parametrize(
    ("entity", "text", "specific"),
    [
        ("R", "Reported R-squared gains", False),
        ("R", "Worked in the R&D group", False),
        ("C", "Studied the C++ guide", False),
        ("C", "Briefed the C-suite", False),
        ("C", "Wrote the driver in C", True),
        ("R", "Built models in R.", True),
    ],
)
def test_a_one_letter_entity_counts_only_as_a_word_of_its_own(
    entity: str, text: str, specific: bool
) -> None:
    assert not any(c.isdigit() for c in text)  # premise: no digit to rescue it
    assert entity in text  # premise: the letter is present
    cited = _fact("lang", f"Used {entity}", [entity])
    assert is_specific(text, [cited]) is specific


@pytest.mark.parametrize(
    ("entity", "text", "spelling"),
    [
        ("PostgreSQL", "Designed the Postgres-backed course-review site", "postgresql"),
        ("AWS", "Deployed the scraper on Amazon Web Services", "aws"),
    ],
)
def test_a_bullet_naming_its_entity_by_an_alias_is_specific(
    entity: str, text: str, spelling: str
) -> None:
    # Premises: no digit, and not the entity as the fact spells it.
    assert not any(c.isdigit() for c in text)
    assert spelling not in text.casefold()
    assert is_specific(text, [_fact("x", f"Used {entity}", [entity])])


ROLE = Fact(
    id="role",
    kind=FactKind.EXPERIENCE,
    text="Data Engineer at Northwind Analytics",
    organization="Northwind Analytics",
    title="Data Engineer",
    entities=["Northwind Analytics"],
    provenance=CONFIRMED,
)


def _doc(*bullets: Bullet, brief: str | None = None) -> ResumeDoc:
    entry = Entry(
        heading="Northwind Analytics",
        subheading="Data Engineer",
        sources=["role"],
        bullets=list(bullets),
    )
    return ResumeDoc(
        identity=Identity(name="Dana Vex", email="d@example.com"),
        sections=[Section(title="Experience", entries=[entry])],
        brief=brief,
    )


def test_resume_findings_split_failures_from_warnings() -> None:
    loader = _fact("loader", "Shipped the Airflow loader", ["Airflow"])
    doc = _doc(
        Bullet(text="Spearheaded the Airflow loader", sources=["loader"]),
        Bullet(text="Shipped the loader", sources=["loader"]),
    )
    fails, warns = resume_findings(doc, {"role": ROLE, "loader": loader}, None)
    assert fails == ["bullet 0: stock phrase 'Spearheaded'"]
    assert len(warns) == 1 and warns[0].startswith("bullet 1: no number")


CNN_EXEMPLAR = "Trained a convolutional neural network in PyTorch to classify satellite images"
CNN_FACT = "Trained a convolutional neural network in PyTorch on 40k galaxy images"


def _cnn_findings(bullet: str) -> list[str]:
    brief = BRIEF.model_copy(
        update={"exemplars": (Exemplar(kind="bullet", text=CNN_EXEMPLAR, source="s1", why="w"),)}
    )
    cnn = _fact("cnn", CNN_FACT, ["PyTorch"])
    fails, _ = resume_findings(
        _doc(Bullet(text=bullet, sources=["cnn"])), {"role": ROLE, "cnn": cnn}, brief
    )
    return fails


def test_wording_shared_with_the_cited_fact_is_not_copying() -> None:
    bullet = "Trained a convolutional neural network in PyTorch on galaxy images"
    # Premise: the bullet shares six words with the exemplar, all of them the fact's.
    assert copied_run(bullet, [CNN_EXEMPLAR]) is not None
    assert _cnn_findings(bullet) == []


def test_wording_copied_beyond_the_cited_fact_is_copying() -> None:
    bullet = "Trained a convolutional neural network in PyTorch to classify galaxy morphologies"
    # Premise: the bullet's first shared run is the fact's own wording.
    assert copied_run(bullet, [CNN_EXEMPLAR]) == "trained a convolutional neural network in"
    assert copied_run(bullet, [CNN_FACT]) == "trained a convolutional neural network in"
    assert _cnn_findings(bullet) == [
        "bullet 0: copies an exemplar of the quant-research brief: "
        "'convolutional neural network in pytorch to'"
    ]


def test_a_document_naming_a_brief_that_was_not_loaded_fails_style(tmp_path: Path) -> None:
    loader = _fact("loader", "Shipped the Airflow loader", ["Airflow"])
    doc = _doc(Bullet(text="Shipped the Airflow loader", sources=["loader"]), brief="ai-ml")
    report = run_gates(doc, {"role": ROLE, "loader": loader}, tmp_path / "absent.pdf")
    style = report.outcomes[-1]
    assert style.gate == "style" and style.verdict == "fail"
    assert "names brief 'ai-ml', which was not loaded" in style.findings[0]
