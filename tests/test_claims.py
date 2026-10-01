from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest
from pydantic import ValidationError

from core.checks.claims import check_claims, omitted_current_roles
from core.profile.models import Dates, Fact, FactKind, Identity, Provenance, Trust
from core.resume.models import Bullet, Entry, ResumeDoc, Section

CONFIRMED = Provenance(source="onboarding_answer", trust=Trust.USER_CONFIRMED)
IMPORTED = Provenance(source="import:resume.pdf", trust=Trust.IMPORT_UNCONFIRMED)
IDENTITY = Identity(name="Dana Vex", email="dana@example.com", links=["github.com/dv"])


def fact(fid: str, text: str, **kw: object) -> Fact:
    body: dict[str, object] = {
        "id": fid,
        "kind": FactKind.EXPERIENCE,
        "text": text,
        "provenance": CONFIRMED,
    }
    body.update(kw)
    return Fact.model_validate(body)


ROLE = fact(
    "role",
    "Data Engineer at Northwind Analytics, consulting for Goldman Sachs; "
    "reported to the Chief Technology Officer",
    organization="Northwind Analytics",
    title="Data Engineer",
    location="Boston, MA",
    entities=["Northwind Analytics", "Goldman Sachs"],
    dates=Dates(start="2024-06", end="2025-08"),
)
INTERN = fact(
    "intern",
    "Data Engineering Intern at Harbor Lab",
    organization="Harbor Lab",
    title="Data Engineering Intern",
    dates=Dates(start="2023-06", end="2023-08"),
)
CURRENT = fact(
    "current",
    "Research Assistant at Harbor Lab",
    organization="Harbor Lab",
    title="Research Assistant",
    dates=Dates(start="2025-09"),
)
DEGREE = fact(
    "degree",
    "BS Statistics, Lakeshore State University",
    kind=FactKind.EDUCATION,
    organization="Lakeshore State University",
    title="BS Statistics",
    dates=Dates(start="2019-09", end="2023-05"),
)
SKILLS = fact("skills", "Kubernetes", kind=FactKind.SKILL)
DETAIL = fact(
    "detail",
    "Moved the event bus to Kafka",
    kind=FactKind.EXPERIENCE_DETAIL,
    parent="role",
    entities=["Kafka"],
)
CONTACT = fact(
    "contact",
    "Dana Vex, dana@example.com, github.com/dv",
    kind=FactKind.LINKS,
    entities=["Dana Vex", "dana@example.com", "github.com/dv"],
)
FACTS = {f.id: f for f in (ROLE, INTERN, CURRENT, DEGREE, SKILLS, DETAIL, CONTACT)}


def doc(
    *,
    entry: Mapping[str, object] | None = None,
    skills: list[str] | None = None,
    identity: Identity = IDENTITY,
    title: str = "Experience",
) -> ResumeDoc:
    body: dict[str, object] = {
        "heading": "Northwind Analytics",
        "subheading": "Data Engineer",
        "location": "Boston, MA",
        "start": "Jun 2024",
        "end": "Aug 2025",
        "sources": ["role"],
        "bullets": [Bullet(text="Moved the event bus to Kafka", sources=["detail"])],
    }
    body.update(entry or {})
    sections = [Section(title=title, entries=[Entry.model_validate(body)])]
    if skills is not None:
        sections.append(Section(title="Skills", skills=skills))
    return ResumeDoc(identity=identity, sections=sections)


def findings_for(document: ResumeDoc, facts: dict[str, Fact] | None = None) -> tuple[str, ...]:
    return check_claims(document, facts if facts is not None else FACTS).findings


def test_an_honest_document_passes() -> None:
    assert findings_for(doc(skills=["Kubernetes", "Kafka"])) == ()


# --- entry headers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        # The invented employer shares a word with the real one.
        ({"heading": "Northwind Capital"}, "employer 'Northwind Capital'"),
        # An entity of the fact, but the client, not the employer.
        ({"heading": "Goldman Sachs"}, "employer 'Goldman Sachs'"),
        # A title stated in the fact's text, but a manager's.
        ({"subheading": "Chief Technology Officer"}, "title 'Chief Technology Officer'"),
        ({"subheading": "Senior Data Engineer"}, "title 'Senior Data Engineer'"),
        ({"location": "New York, NY"}, "location 'New York, NY'"),
        ({"start": "Jun 2023"}, "start date 'Jun 2023'"),
        ({"end": "Present"}, "end date 'Present'"),
        ({"start": "Summer 2024"}, "start date 'Summer 2024'"),
        ({"sources": []}, "cites no fact"),
        ({"sources": ["nope"]}, "unknown fact"),
    ],
)
def test_a_fabricated_header_field_fails(change: dict[str, object], expected: str) -> None:
    found = findings_for(doc(entry=change))
    assert any(expected in f for f in found), found


def test_dropping_a_qualifier_from_a_title_is_inflation() -> None:
    """Mutation: compare titles by containment -- this test fails."""
    intern = {
        "heading": "Harbor Lab",
        "subheading": "Data Engineering",
        "location": None,
        "start": "Jun 2023",
        "end": "Aug 2023",
        "sources": ["intern"],
        "bullets": [],
    }
    assert any("title 'Data Engineering'" in f for f in findings_for(doc(entry=intern)))


def test_every_header_field_must_come_from_the_same_fact() -> None:
    """Citing a degree alongside a job must not lend the job the degree's start.
    Mutation: check each date against any cited fact -- this test fails."""
    spliced = {"start": "Sep 2019", "sources": ["role", "degree"]}
    assert any("start date 'Sep 2019'" in f for f in findings_for(doc(entry=spliced)))


def test_an_employer_named_only_by_a_detail_fact_is_not_an_employer() -> None:
    partner = fact(
        "partner",
        "Partnered with Acme Quant Capital",
        kind=FactKind.EXPERIENCE_DETAIL,
        organization="Acme Quant Capital",
    )
    found = findings_for(
        doc(entry={"heading": "Acme Quant Capital", "sources": ["partner"], "bullets": []}),
        {**FACTS, "partner": partner},
    )
    assert any("employer 'Acme Quant Capital'" in f for f in found), found


def test_present_is_allowed_only_for_a_role_that_has_not_ended() -> None:
    current: dict[str, object] = {
        "heading": "Harbor Lab",
        "subheading": "Research Assistant",
        "location": None,
        "start": "Sep 2025",
        "end": "Present",
        "sources": ["current"],
        "bullets": [],
    }
    assert findings_for(doc(entry=current)) == ()


def test_a_bare_year_is_compared_to_the_fact_year() -> None:
    assert findings_for(doc(entry={"start": "2024", "end": "2025"})) == ()
    assert findings_for(doc(entry={"start": "2023"})) != ()


def test_an_unconfirmed_fact_cannot_back_a_header() -> None:
    imported = ROLE.model_copy(update={"provenance": IMPORTED})
    found = findings_for(doc(), {**FACTS, "role": imported})
    assert any("not confirmed" in f for f in found), found


# --- bullets belong to their entry ------------------------------------------------


def test_a_bullet_may_not_cite_another_entrys_fact() -> None:
    """Mutation: skip the ownership check -- this test fails."""
    other = fact(
        "other",
        "Cut error rates 42%",
        kind=FactKind.EXPERIENCE_DETAIL,
        parent="intern",
    )
    borrowed = {"bullets": [Bullet(text="Cut error rates 42%", sources=["other"])]}
    found = findings_for(doc(entry=borrowed), {**FACTS, "other": other})
    assert any("belong to no fact of this entry ['other']" in f for f in found), found


# --- identity and section titles --------------------------------------------------


def test_a_credential_appended_to_the_name_fails() -> None:
    found = findings_for(doc(identity=IDENTITY.model_copy(update={"name": "Dana Vex, PhD"})))
    assert found == ("identity name 'Dana Vex, PhD' is not an entity of any confirmed links fact",)


def test_a_link_that_is_prose_is_refused_at_the_model() -> None:
    with pytest.raises(ValidationError, match="no spaces"):
        Identity(name="Dana Vex", email="dana@example.com", links=["TS/SCI clearance"])


def test_an_unconfirmed_link_fails() -> None:
    extra = IDENTITY.model_copy(update={"links": ["github.com/dv", "linkedin.com/in/someone"]})
    found = findings_for(doc(identity=extra))
    assert found == (
        "identity link 'linkedin.com/in/someone' is not an entity of any confirmed links fact",
    )


def test_an_embellished_section_title_fails() -> None:
    found = findings_for(doc(title="Experience - Forbes 30 Under 30 Honoree"))
    assert any("section title" in f for f in found), found


# --- skills -----------------------------------------------------------------------


def test_a_skill_with_no_confirmed_fact_fails() -> None:
    assert findings_for(doc(skills=["Kubernetes", "Terraform"])) == (
        "skill 'Terraform' is backed by no confirmed fact",
    )


def test_a_skill_matches_through_its_alias() -> None:
    assert findings_for(doc(skills=["k8s"])) == ()


def test_a_skill_fact_stored_as_an_alias_backs_the_canonical_name() -> None:
    torch = fact("torch", "torch", kind=FactKind.SKILL)
    assert findings_for(doc(skills=["PyTorch"]), {**FACTS, "torch": torch}) == ()


def test_a_skill_evidenced_by_a_cited_fact_passes_without_a_skill_fact() -> None:
    assert findings_for(doc(skills=["Kafka"])) == ()


# An uncited detail of the role: true, confirmed, and quoted by no bullet.
EXCEL = fact(
    "excel",
    "Used Excel for the weekly capacity report",
    kind=FactKind.EXPERIENCE_DETAIL,
    parent="role",
    entities=["Excel"],
)


def test_an_uncited_detail_of_a_role_backs_a_skill() -> None:
    """A confirmed fact is true whether or not a bullet quotes it, so the
    skills line does not have to be bought with a bullet naming the tool.
    Mutation: drop the work-lineage route in `_skill_backing` -- this fails."""
    document = doc(skills=["Excel"])
    assert "excel" not in {
        s for e in document.sections[0].entries for b in e.bullets for s in b.sources
    }
    assert findings_for(document, {**FACTS, "excel": EXCEL}) == ()


@pytest.mark.parametrize(
    "update",
    [{"provenance": IMPORTED}, {"retired_on": date(2026, 9, 30)}],
    ids=["unconfirmed", "retired"],
)
def test_an_uncited_detail_that_is_not_selectable_backs_nothing(update: dict[str, object]) -> None:
    excel = EXCEL.model_copy(update=update)
    assert not excel.selectable()
    assert findings_for(doc(skills=["Excel"]), {**FACTS, "excel": excel}) == (
        "skill 'Excel' is backed by no confirmed fact",
    )


def test_a_detail_under_a_retired_role_backs_nothing() -> None:
    """The route runs through work the candidate still stands behind."""
    retired_role = ROLE.model_copy(update={"retired_on": date(2026, 9, 30)})
    found = findings_for(doc(skills=["Excel"]), {**FACTS, "role": retired_role, "excel": EXCEL})
    assert "skill 'Excel' is backed by no confirmed fact" in found


def test_an_uncited_course_detail_does_not_back_a_skill() -> None:
    """A course's entities are what it covered, not what the candidate can do:
    a course taught in R is not R on the skills line. Cited by a bullet, it is
    visible on the page and backs the skill as before.
    Mutation: let any confirmed fact's entities back a skill -- this fails."""
    course = fact(
        "course",
        "Coursework: Data Science in R",
        kind=FactKind.EXPERIENCE_DETAIL,
        parent="degree",
        entities=["R"],
    )
    facts = {**FACTS, "course": course}
    assert course.selectable() and DEGREE.selectable()
    assert findings_for(doc(skills=["R"]), facts) == ("skill 'R' is backed by no confirmed fact",)

    cited = doc(
        skills=["R"],
        entry={"bullets": [Bullet(text="Coursework: Data Science in R", sources=["course"])]},
    )
    assert "skill 'R' is backed by no confirmed fact" not in findings_for(cited, facts)


def test_a_roles_own_entities_do_not_back_a_skill() -> None:
    """`role` names its client, Goldman Sachs, which is no skill.
    Mutation: let the work fact itself back -- this fails."""
    assert "Goldman Sachs" in ROLE.entities
    assert findings_for(doc(skills=["Goldman Sachs"])) == (
        "skill 'Goldman Sachs' is backed by no confirmed fact",
    )


@pytest.mark.parametrize(
    "extra",
    [
        # A record that the skill is missing.
        {
            "ab": fact(
                "ab",
                "No Terraform here",
                kind=FactKind.ABSENT,
                parent="role",
                entities=["Terraform"],
            )
        },
        # A links fact filed under a role.
        {
            "ln": fact(
                "ln",
                "Lead: Terraform Jones",
                kind=FactKind.LINKS,
                parent="role",
                entities=["Terraform"],
            )
        },
        # A course detail under a degree that is filed under a role.
        {
            "degree": DEGREE.model_copy(update={"parent": "role"}),
            "ab": fact(
                "ab",
                "Coursework: Terraform",
                kind=FactKind.EXPERIENCE_DETAIL,
                parent="degree",
                entities=["Terraform"],
            ),
        },
    ],
    ids=["absent", "links", "course-under-a-reparented-degree"],
)
def test_only_a_chain_of_details_reaches_a_role(extra: dict[str, Fact]) -> None:
    """Mutation: accept any kind under a role, checking only the ancestor --
    every case fails."""
    facts = {**FACTS, **extra}
    assert all(f.selectable() for f in extra.values())
    assert findings_for(doc(skills=["Terraform"]), facts) == (
        "skill 'Terraform' is backed by no confirmed fact",
    )


def test_a_skill_fact_that_is_unconfirmed_does_not_back_a_skill() -> None:
    imported = SKILLS.model_copy(update={"provenance": IMPORTED})
    assert findings_for(doc(skills=["Kubernetes"]), {**FACTS, "skills": imported}) != ()


# --- current roles ------------------------------------------------------------------


def test_a_current_role_no_entry_cites_is_named() -> None:
    """`current` (Harbor Lab, no end date) is confirmed and nothing cites it.
    Mutation: drop the `end is None` condition -- the ended `role` is named too."""
    assert omitted_current_roles(doc(), FACTS) == ["Harbor Lab"]


def test_a_cited_detail_puts_its_current_role_on_the_page() -> None:
    task = fact("task", "Ran the lab's intake", kind=FactKind.EXPERIENCE_DETAIL, parent="current")
    cited = doc(entry={"bullets": [Bullet(text="Ran the lab's intake", sources=["task"])]})
    assert omitted_current_roles(cited, {**FACTS, "task": task}) == []


def test_a_second_header_for_the_same_current_role_is_not_an_omission() -> None:
    """A retitled role is a second current header fact; citing either one shows it."""
    retitled = CURRENT.model_copy(update={"id": "current-2", "title": "Research Scientist"})
    shown = doc(entry={"sources": ["current-2"], "bullets": []})
    assert omitted_current_roles(shown, {**FACTS, "current-2": retitled}) == []


def test_an_ended_role_at_the_same_organization_does_not_hide_a_current_one() -> None:
    """`intern` is Harbor Lab too, but it ended. Mutation: count any on-page
    fact's organization as shown -- this fails."""
    assert INTERN.organization == CURRENT.organization and INTERN.dates is not None
    assert INTERN.dates.end is not None
    assert omitted_current_roles(doc(entry={"sources": ["intern"]}), FACTS) == ["Harbor Lab"]


def test_a_retired_current_role_is_not_an_omission() -> None:
    retired = CURRENT.model_copy(update={"retired_on": date(2026, 9, 30)})
    assert omitted_current_roles(doc(), {**FACTS, "current": retired}) == []


@pytest.mark.parametrize(
    "title", ["Teaching & Mentoring", "Honors & Awards", "Leadership and Activities"]
)
def test_common_section_titles_and_their_ampersand_forms_pass(title: str) -> None:
    assert findings_for(doc(title=title)) == ()


def test_an_ampersand_does_not_launder_an_unlisted_title() -> None:
    assert findings_for(doc(title="Experience & Forbes 30 Under 30")) == (
        "section title 'Experience & Forbes 30 Under 30' is not a recognised section name",
    )
