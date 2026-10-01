"""Command-line entry point for callback."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import TextIO, cast

from core import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="callback")
    parser.add_argument("--version", action="version", version=f"callback {__version__}")
    parser.set_defaults(handler=None)

    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="create the private data directory")
    init.set_defaults(handler=_cmd_init)

    delete_all = sub.add_parser(
        "delete-all", help="permanently remove everything under the private data directory"
    )
    delete_all.set_defaults(handler=_cmd_delete_all)

    build = sub.add_parser(
        "build", help="render a resume document and run every gate on it (writes a draft)"
    )
    build.add_argument("doc", type=Path, help="resume document (YAML)")
    build.add_argument("--job", required=True, help="job id: lowercase letters, digits, hyphens")
    build.set_defaults(handler=_cmd_build)

    targets = sub.add_parser("targets", help="the companies whose job boards to watch")
    targets_sub = targets.add_subparsers(dest="targets_command", required=True)
    t_add = targets_sub.add_parser("add", help="watch a company's Greenhouse, Lever or Ashby board")
    t_add.add_argument("company")
    t_add.add_argument("--ats", required=True, choices=["greenhouse", "lever", "ashby", "hn"])
    t_add.add_argument("--board", required=True, help="the board name in the company's job URLs")
    t_add.set_defaults(handler=_cmd_targets_add)
    targets_sub.add_parser("list", help="list watched companies").set_defaults(
        handler=_cmd_targets_list
    )

    criteria = sub.add_parser("criteria", help="which posting titles a watch keeps")
    criteria.add_argument("--include", required=True, help="comma-separated title terms")
    criteria.add_argument(
        "--require", default="", help="comma-separated terms a title must also contain one of"
    )
    criteria.add_argument("--exclude", default="", help="comma-separated title terms to skip")
    criteria.set_defaults(handler=_cmd_criteria)

    sub.add_parser("watch", help="poll watched boards for new matching postings").set_defaults(
        handler=_cmd_watch
    )

    postings = sub.add_parser("postings", help="discovered postings")
    postings_sub = postings.add_subparsers(dest="postings_command", required=True)
    p_list = postings_sub.add_parser("list", help="list stored postings, newest first")
    p_list.add_argument(
        "--days", type=int, default=None, help="only those first seen this recently"
    )
    p_list.set_defaults(handler=_cmd_postings_list)
    p_resolve = postings_sub.add_parser(
        "resolve",
        help="fetch the posting behind a Greenhouse, Lever or Ashby URL, or an employer's "
        "careers page that publishes schema.org JobPosting data",
    )
    p_resolve.add_argument("url")
    p_resolve.add_argument("--company", required=True)
    p_resolve.set_defaults(handler=_cmd_postings_resolve)
    p_take = postings_sub.add_parser("take", help="save a posting as the posting for a job")
    p_take.add_argument("posting_id")
    p_take.add_argument("--job", required=True)
    p_take.set_defaults(handler=_cmd_postings_take)

    profile = sub.add_parser(
        "profile", help="what eligibility is judged against, and the resume's page limit"
    )
    profile.add_argument(
        "--work-authorization", help='e.g. "US citizen", "F-1, will need sponsorship"'
    )
    profile.add_argument("--graduation", help='expected graduation, e.g. "May 2028"')
    profile.add_argument(
        "--degree", choices=["high school", "associate", "bachelor", "master", "phd"]
    )
    profile.add_argument("--pages", type=int, help="how many pages a resume may run to (default 1)")
    profile.set_defaults(handler=_cmd_profile)

    triage = sub.add_parser("triage", help="rank stored postings: eligibility, evidence, freshness")
    triage.add_argument(
        "--days", type=int, default=None, help="only postings first seen this recently"
    )
    triage.add_argument("--limit", type=int, default=20)
    triage.set_defaults(handler=_cmd_triage)

    elig = sub.add_parser("eligibility", help="check a saved posting for knockout requirements")
    elig.add_argument("job", help="job id of a posting saved in inbox/")
    elig.set_defaults(handler=_cmd_eligibility)

    fit_cmd = sub.add_parser(
        "fit",
        help="a saved posting's knockouts, and which technologies it names that your "
        "confirmed facts back",
    )
    fit_cmd.add_argument("job", help="job id of a posting saved in inbox/")
    fit_cmd.set_defaults(handler=_cmd_fit)

    sub.add_parser(
        "agenda",
        help="what to do next: interviews to prepare, postings to take, quiet applications",
    ).set_defaults(handler=_cmd_agenda)

    templates = sub.add_parser(
        "template", help="reuse a document that passed as the starting point for the next one"
    )
    templates_sub = templates.add_subparsers(dest="template_command", required=True)
    t_save = templates_sub.add_parser("save", help="save a job's gated draft under a name")
    t_save.add_argument("job")
    t_save.add_argument("--name", required=True)
    t_save.set_defaults(handler=_cmd_template_save)
    templates_sub.add_parser("list", help="list saved templates").set_defaults(
        handler=_cmd_template_list
    )
    t_use = templates_sub.add_parser("use", help="copy a template into a job's draft")
    t_use.add_argument("name")
    t_use.add_argument("--job", required=True)
    t_use.add_argument("--overwrite", action="store_true", help="replace an existing draft")
    t_use.set_defaults(handler=_cmd_template_use)

    answers = sub.add_parser(
        "answers", help="check free-text application-form answers and write them paste-ready"
    )
    answers.add_argument("doc", type=Path, help="answers (YAML)")
    answers.set_defaults(handler=_cmd_answers)

    outreach = sub.add_parser(
        "outreach", help="check a short note to someone at the company, and print it"
    )
    outreach.add_argument("doc", type=Path, help="outreach note (YAML)")
    outreach.set_defaults(handler=_cmd_outreach)

    fin = sub.add_parser(
        "finalize", help="seal a gated draft into final/ and record it in the ledger"
    )
    fin.add_argument("job")
    fin.add_argument("--accept-warnings", action="store_true")
    fin.set_defaults(handler=_cmd_finalize)
    ver = sub.add_parser("verify", help="check a sealed document, and the ledger, for changes")
    ver.add_argument("job", nargs="?", help="job id; omit to check only the ledger")
    ver.set_defaults(handler=_cmd_verify)

    interview = sub.add_parser(
        "interview", help="build an interview prep pack from exactly what you sent"
    )
    interview.add_argument("job")
    interview.set_defaults(handler=_cmd_interview)

    gaps_cmd = sub.add_parser(
        "gaps", help="technologies eligible postings ask for that your facts do not back"
    )
    gaps_cmd.add_argument("--days", type=int, default=None)
    gaps_cmd.set_defaults(handler=_cmd_gaps)

    app = sub.add_parser(
        "apply",
        help="build the application packet for a gated draft and record it (never submits)",
    )
    app.add_argument("job", help="job id of a built draft")
    app.add_argument("--company", required=True)
    app.add_argument("--title", required=True)
    app.add_argument("--location", default="")
    app.add_argument("--url", default="")
    app.add_argument("--source", required=True, help="greenhouse, lever, linkedin, handshake, ...")
    app.add_argument(
        "--accept-warnings", action="store_true", help="apply although the gates warned"
    )
    app.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild the packet for a job already recorded, after improving its draft",
    )
    app.add_argument(
        "--role-family", default=None, help="one of `callback notes families`, for `insights`"
    )
    app.set_defaults(handler=_cmd_apply)

    outcome = sub.add_parser("outcome", help="record what happened to an application")
    outcome.add_argument("job")
    outcome.add_argument(
        "kind",
        choices=["viewed", "callback", "interview", "offer", "rejected", "ghosted", "withdrew"],
    )
    outcome.set_defaults(handler=_cmd_outcome)

    void = sub.add_parser("void", help="remove the record of an application never submitted")
    void.add_argument("job")
    void.set_defaults(handler=_cmd_void)

    applications = sub.add_parser("applications", help="list recorded applications")
    applications.set_defaults(handler=_cmd_applications)

    letter = sub.add_parser(
        "letter", help="render a cover letter and check it against your facts and the posting"
    )
    letter.add_argument("doc", type=Path, help="cover letter (YAML)")
    letter.set_defaults(handler=_cmd_letter)

    export = sub.add_parser(
        "export", help="print a resume document as plain text, for forms that take pasted text"
    )
    export.add_argument("doc", type=Path, help="resume document (YAML)")
    export.set_defaults(handler=_cmd_export)

    imp = sub.add_parser("import", help="read an existing resume PDF, screening it for hidden text")
    imp.add_argument("file", type=Path)
    imp.set_defaults(handler=_cmd_import)

    fun = sub.add_parser(
        "funnel",
        help="reply, callback and offer rates by where you applied, and stale applications",
    )
    fun.set_defaults(handler=_cmd_funnel)
    sub.add_parser(
        "insights",
        help="what happened to what was sent, by template, role family and cited fact (counts)",
    ).set_defaults(handler=_cmd_insights)

    facts = sub.add_parser("facts", help="add, list and confirm the facts a resume may use")
    facts_sub = facts.add_subparsers(dest="facts_command", required=True)
    add = facts_sub.add_parser("add", help="add facts from a YAML list (they start unconfirmed)")
    add.add_argument("file", type=Path)
    add.add_argument(
        "--replace",
        action="store_true",
        help="replace a stored draft of the same id (never a confirmed or retired fact)",
    )
    add.set_defaults(handler=_cmd_facts_add)
    check = facts_sub.add_parser(
        "check", help="what a bullet quoting each fact as written would fail on"
    )
    check.add_argument("ids", nargs="*", help="fact ids (default: every fact in use or waiting)")
    check.set_defaults(handler=_cmd_facts_check)
    listing = facts_sub.add_parser("list", help="list stored facts")
    listing.add_argument(
        "--unconfirmed", action="store_true", help="only facts still waiting for an answer"
    )
    listing.set_defaults(handler=_cmd_facts_list)
    conf = facts_sub.add_parser(
        "confirm", help="confirm facts interactively; must be run by the candidate in a terminal"
    )
    conf.add_argument(
        "ids", nargs="*", help="fact ids (default: every unconfirmed fact not yet declined)"
    )
    conf.add_argument(
        "--include-declined", action="store_true", help="also offer the facts answered no before"
    )
    conf.set_defaults(handler=_cmd_facts_confirm)
    ret = facts_sub.add_parser(
        "retire",
        help="take facts out of use for good; must be run by the candidate in a terminal",
    )
    ret.add_argument("ids", nargs="+", help="the facts to retire")
    ret.set_defaults(handler=_cmd_facts_retire)
    gh = facts_sub.add_parser(
        "github",
        help="draft unconfirmed project facts from your public GitHub repositories, to a file",
    )
    gh.add_argument("user", help="your GitHub username")
    gh.set_defaults(handler=_cmd_facts_github)

    notes = sub.add_parser(
        "notes", help="standing preferences, goals and constraints every session should know"
    )
    notes_sub = notes.add_subparsers(dest="notes_command", required=True)
    n_list = notes_sub.add_parser("list", help="list active notes")
    n_list.add_argument("--all", action="store_true", help="include proposed and retired notes")
    n_list.set_defaults(handler=_cmd_notes_list)
    n_prop = notes_sub.add_parser(
        "propose", help="record a note for the candidate to activate; it does nothing until then"
    )
    n_prop.add_argument("kind", choices=["preference", "goal", "constraint", "context"])
    n_prop.add_argument("text", help="the candidate's own words, at most 200 characters")
    n_prop.add_argument(
        "--context",
        default=None,
        help="what the note arose from, as a label such as job:acme-ml-intern",
    )
    n_prop.set_defaults(handler=_cmd_notes_propose)
    n_act = notes_sub.add_parser(
        "activate", help="activate a note; must be run by the candidate in a terminal"
    )
    n_act.add_argument("id")
    n_act.set_defaults(handler=_cmd_notes_activate)
    n_ret = notes_sub.add_parser("retire", help="set a note aside; it can be activated again")
    n_ret.add_argument("id")
    n_ret.set_defaults(handler=_cmd_notes_retire)
    n_fam = notes_sub.add_parser(
        "families", help="show, or with --set replace, the role families applications sort into"
    )
    n_fam.add_argument("--set", dest="families", default=None, help="comma-separated names")
    n_fam.set_defaults(handler=_cmd_notes_families)

    return parser


_SAFE_STEM = re.compile(r"[^a-z0-9-]+")


def _fetcher() -> object:
    """The network fetcher; a single place tests replace."""
    from core.discovery.fetch import HttpFetcher

    return HttpFetcher()


def _page_fetcher() -> object:
    """The careers-page fetcher; a single place tests replace."""
    from core.discovery.page import PageFetcher

    return PageFetcher()


def _discovery_errors() -> tuple[type[Exception], ...]:
    from pydantic import ValidationError

    from core.discovery.fetch import DiscoveryError
    from core.paths import OutsideHomeError

    return (DiscoveryError, ValidationError, OSError, OutsideHomeError)


def _cmd_targets_add(args: argparse.Namespace) -> int:
    from core.discovery.store import Target, add_target
    from core.paths import ensure_home

    ensure_home()
    try:
        added = add_target(Target(company=args.company, ats=args.ats, board=args.board))
    except _discovery_errors() as exc:
        print(f"callback targets add: {exc}", file=sys.stderr)
        return 3
    print(f"{'watching' if added else 'already watching'} {args.company} ({args.ats}:{args.board})")
    return 0


def _cmd_targets_list(_args: argparse.Namespace) -> int:
    from core.discovery.store import load_targets

    try:
        for t in load_targets():
            print(f"{t.company:<30} {t.ats}:{t.board}")
    except _discovery_errors() as exc:
        print(f"callback targets list: {exc}", file=sys.stderr)
        return 3
    return 0


def _cmd_criteria(args: argparse.Namespace) -> int:
    from core.discovery.store import Criteria, save_criteria
    from core.paths import ensure_home

    def terms(raw: str) -> list[str]:
        return [t.strip() for t in raw.split(",") if t.strip()]

    ensure_home()
    try:
        save_criteria(
            Criteria(
                include=terms(args.include),
                require=terms(args.require),
                exclude=terms(args.exclude),
            )
        )
    except _discovery_errors() as exc:
        print(f"callback criteria: {exc}", file=sys.stderr)
        return 3
    return 0


def _cmd_watch(_args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.discovery.fetch import Fetcher
    from core.discovery.store import watch

    try:
        result = watch(cast("Fetcher", _fetcher()), now=datetime.now(tz=UTC))
    except _discovery_errors() as exc:
        print(f"callback watch: {exc}", file=sys.stderr)
        return 3
    for p in result.new:
        print(f"NEW  {p.id:<40} {p.company} / {p.title}  [{p.location}]")
    for target, message in result.errors:
        print(f"callback watch: could not read {target.company}: {message}", file=sys.stderr)
    print(f"{len(result.new)} new posting(s)")
    return 1 if result.errors else 0


def _cmd_postings_list(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta

    from core.discovery.store import load_postings

    try:
        postings = sorted(load_postings().values(), key=lambda p: p.first_seen, reverse=True)
    except _discovery_errors() as exc:
        print(f"callback postings list: {exc}", file=sys.stderr)
        return 3
    now = datetime.now(tz=UTC)
    for p in postings:
        age = now - p.first_seen
        if args.days is not None and age > timedelta(days=args.days):
            continue
        print(f"{age.days:>3}d  {p.id:<40} {p.company} / {p.title}  [{p.location}]")
    return 0


def _cmd_postings_resolve(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.discovery.fetch import Fetcher
    from core.discovery.store import PageSource, resolve
    from core.paths import ensure_home

    ensure_home()
    try:
        found = resolve(
            args.url,
            args.company,
            cast("Fetcher", _fetcher()),
            now=datetime.now(tz=UTC),
            pages=cast("PageSource", _page_fetcher()),
        )
    except _discovery_errors() as exc:
        print(f"callback postings resolve: {exc}", file=sys.stderr)
        return 3
    print(f"{found.id}  {found.company} / {found.title}")
    return 0


def _cmd_postings_take(args: argparse.Namespace) -> int:
    from core.discovery.store import take

    try:
        path = take(args.posting_id, args.job)
    except _discovery_errors() as exc:
        print(f"callback postings take: {exc}", file=sys.stderr)
        return 3
    print(f"posting saved to {path}")
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    from core.paths import ensure_home
    from core.triage import Profile, load_profile, save_profile

    ensure_home()
    try:
        current = load_profile()
        updates = {
            k: v
            for k, v in (
                ("work_authorization", args.work_authorization),
                ("graduation", args.graduation),
                ("degree", args.degree),
                ("resume_pages", args.pages),
            )
            if v is not None
        }
        # Validated, not copied: `model_copy` would store a page limit of 0.
        profile = Profile.model_validate({**current.model_dump(), **updates})
        save_profile(profile)
    except (ValueError, OSError) as exc:
        print(f"callback profile: {exc}", file=sys.stderr)
        return 3
    for key, value in profile.model_dump().items():
        print(f"{key:<20} {value if value is not None else '(not set)'}")
    return 0


def _print_eligibility(eligibility: object) -> None:
    from core.triage import Eligibility

    assert isinstance(eligibility, Eligibility)
    for f in eligibility.findings:
        print(f"  {f.level.upper():<9} {f.rule}: {f.reason}")
        print(f'            "{f.quote[:160]}"')


def _cmd_triage(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta

    from core.discovery.store import load_postings
    from core.profile.store import StoreError, load_facts
    from core.triage import load_profile, rank

    now = datetime.now(tz=UTC)
    try:
        postings = list(load_postings().values())
        profile = load_profile()
        facts = load_facts()
    except (*_discovery_errors(), StoreError, ValueError) as exc:
        print(f"callback triage: {exc}", file=sys.stderr)
        return 3
    if args.days is not None:
        postings = [p for p in postings if now - p.first_seen <= timedelta(days=args.days)]
    for r in rank(postings, profile, facts, now=now)[: args.limit]:
        have, missing = r.coverage
        print(
            f"{r.score:>6.2f}  {r.eligibility.verdict:<9} {r.age_days:>3}d  "
            f"{r.posting.company} / {r.posting.title}  [{r.posting.id}]"
        )
        if have or missing:
            evidenced, gaps = ", ".join(have) or "-", ", ".join(missing) or "-"
            print(f"        evidenced: {evidenced}   missing: {gaps}")
        _print_eligibility(r.eligibility)
    return 0


def _cmd_eligibility(args: argparse.Namespace) -> int:
    from core.paths import resolve_inside
    from core.triage import check_eligibility, load_profile

    try:
        text = resolve_inside(f"inbox/{args.job}.md").read_text(encoding="utf-8")
        eligibility = check_eligibility(text, load_profile())
    except (OSError, ValueError) as exc:
        print(f"callback eligibility: {exc}", file=sys.stderr)
        return 3
    print(eligibility.verdict.upper())
    _print_eligibility(eligibility)
    return {"eligible": 0, "stretch": 0, "unknown": 1, "knockout": 2}[eligibility.verdict]


def _cmd_fit(args: argparse.Namespace) -> int:
    from core.paths import resolve_inside
    from core.profile.store import StoreError, load_facts
    from core.triage import fit, load_profile

    try:
        text = resolve_inside(f"inbox/{args.job}.md").read_text(encoding="utf-8")
        result = fit(text, load_profile(), load_facts())
    except (OSError, ValueError, StoreError) as exc:
        print(f"callback fit: {exc}", file=sys.stderr)
        return 3
    print(f"{result.eligibility.verdict.upper()}  {result.coverage:.0%} of what it names")
    _print_eligibility(result.eligibility)
    for tech, fids in result.evidenced:
        print(f"  have     {tech}  ({', '.join(fids)})")
    for tech in result.missing:
        print(f"  missing  {tech}")
    return {"eligible": 0, "stretch": 0, "unknown": 1, "knockout": 2}[result.eligibility.verdict]


def _cmd_agenda(_args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.agenda import agenda, render
    from core.apply import ApplyError, load_applications
    from core.discovery.store import load_postings
    from core.profile.store import StoreError, load_facts
    from core.triage import load_profile

    try:
        result = agenda(
            list(load_postings().values()),
            load_applications(),
            load_profile(),
            load_facts(),
            now=datetime.now(tz=UTC),
        )
    except (ApplyError, StoreError, OSError, ValueError) as exc:
        print(f"callback agenda: {exc}", file=sys.stderr)
        return 3
    print(render(result))
    return 0


def _cmd_template_save(args: argparse.Namespace) -> int:
    from core.templates import TemplateError, save

    try:
        out = save(args.job, args.name)
    except (TemplateError, OSError) as exc:
        print(f"callback template save: {exc}", file=sys.stderr)
        return 3
    print(f"saved {out}")
    return 0


def _cmd_template_list(_args: argparse.Namespace) -> int:
    from core.templates import listing

    templates = listing()
    for template in templates:
        print(f"{template.name:<24} {template.bytes_:>6} bytes")
    if not templates:
        print("no templates saved; `callback template save <job> --name <name>`")
    return 0


def _cmd_template_use(args: argparse.Namespace) -> int:
    from core.templates import TemplateError, use

    try:
        out = use(args.name, args.job, overwrite=args.overwrite)
    except (TemplateError, OSError) as exc:
        print(f"callback template use: {exc}", file=sys.stderr)
        return 3
    print(f"{out}\nEdit it for this posting, then run `callback build {out} --job {args.job}`.")
    return 0


def _cmd_answers(args: argparse.Namespace) -> int:
    from core.answers import AnswersError, build_answers
    from core.profile.store import StoreError, load_facts

    try:
        result, out_dir = build_answers(args.doc, load_facts())
    except (AnswersError, StoreError, OSError) as exc:
        print(f"callback answers: {exc}", file=sys.stderr)
        return 3
    print(f"{result.verdict.upper()}  {out_dir / 'answers.md'}")
    for finding in result.findings:
        print(f"  {finding}")
    return 0 if result.verdict == "pass" else 2


def _cmd_outreach(args: argparse.Namespace) -> int:
    import yaml
    from pydantic import ValidationError

    from core.outreach import Outreach, check_outreach, render_outreach
    from core.paths import resolve_inside
    from core.profile.store import StoreError, load_facts

    try:
        doc = Outreach.model_validate(yaml.safe_load(args.doc.read_text(encoding="utf-8")))
        posting = resolve_inside(f"inbox/{doc.job_id}.md").read_text(encoding="utf-8")
        result = check_outreach(doc, load_facts(), posting)
    except (OSError, yaml.YAMLError, ValidationError, StoreError) as exc:
        print(f"callback outreach: {exc}", file=sys.stderr)
        return 3
    print(render_outreach(doc))
    print(f"\n{result.verdict.upper()}", file=sys.stderr)
    for finding in result.findings:
        print(f"  {finding}", file=sys.stderr)
    return 0 if result.verdict == "pass" else 2


def _cmd_finalize(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.finalize import FinalizeError, finalize

    try:
        final = finalize(args.job, now=datetime.now(tz=UTC), accept_warnings=args.accept_warnings)
    except (FinalizeError, OSError) as exc:
        print(f"callback finalize: {exc}", file=sys.stderr)
        return 3
    print(f"sealed: {final}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from core.finalize import verify, verify_ledger

    problems = (verify(args.job) if args.job else []) + verify_ledger()
    for problem in problems:
        print(f"CHANGED  {problem}")
    if not problems:
        print("unchanged since sealed" if args.job else "ledger intact")
    return 2 if problems else 0


def _cmd_interview(args: argparse.Namespace) -> int:
    from core.interview import InterviewError, prepare

    try:
        path = prepare(args.job)
    except (InterviewError, OSError) as exc:
        print(f"callback interview: {exc}", file=sys.stderr)
        return 3
    print(f"prep pack: {path}")
    return 0


def _cmd_gaps(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime, timedelta

    from core.apply import ApplyError, load_applications
    from core.discovery.store import load_postings
    from core.insights import rejected_postings
    from core.profile.store import StoreError, load_facts
    from core.triage import gaps, load_profile, named_by

    try:
        postings = list(load_postings().values())
        if args.days is not None:
            cutoff = datetime.now(tz=UTC) - timedelta(days=args.days)
            postings = [p for p in postings if p.first_seen >= cutoff]
        found = gaps(postings, load_profile(), load_facts())
        texts, missing = rejected_postings(load_applications())
    except (*_discovery_errors(), StoreError, ApplyError, ValueError) as exc:
        print(f"callback gaps: {exc}", file=sys.stderr)
        return 3
    rejected = named_by([tech for tech, _ in found], texts)
    if found:
        print(f"{'postings':>8}  {'rejections':>10}  technology")
    for tech, count in found:
        print(f"{count:>8}  {rejected[tech]:>10}  {tech}")
    if missing:
        print(f"rejected with no saved posting, so not counted: {', '.join(missing)}")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from pydantic import ValidationError

    from core.apply import ApplyError, Target, apply

    try:
        target = Target(
            job_id=args.job,
            company=args.company,
            title=args.title,
            location=args.location,
            url=args.url,
            source=args.source,
        )
        packet = apply(
            target,
            now=datetime.now(tz=UTC),
            accept_warnings=args.accept_warnings,
            refresh=args.refresh,
            role_family=args.role_family,
        )
    except (ApplyError, ValidationError, OSError) as exc:
        print(f"callback apply: {exc}", file=sys.stderr)
        return 3
    print(f"packet: {packet}")
    for item in sorted(packet.iterdir()):
        print(f"  {item.name}")
    where = f" at {target.url}" if target.url else ""
    print(f"callback does not submit applications: submit this packet yourself{where}.")
    return 0


def _cmd_outcome(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.apply import ApplyError, record_outcome

    try:
        record_outcome(args.job, args.kind, now=datetime.now(tz=UTC))
    except ApplyError as exc:
        print(f"callback outcome: {exc}", file=sys.stderr)
        return 3
    return 0


def _cmd_void(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.apply import ApplyError, void_application

    try:
        void_application(args.job, now=datetime.now(tz=UTC))
    except (ApplyError, OSError) as exc:
        print(f"callback void: {exc}", file=sys.stderr)
        return 3
    return 0


def _cmd_funnel(_args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.apply import ApplyError, load_applications
    from core.funnel import funnel, render

    try:
        applications = load_applications()
    except ApplyError as exc:
        print(f"callback funnel: {exc}", file=sys.stderr)
        return 3
    if not applications:
        print("no applications recorded yet")
        return 0
    print(render(funnel(applications, now=datetime.now(tz=UTC))))
    return 0


def _cmd_insights(_args: argparse.Namespace) -> int:
    from core.apply import ApplyError, load_applications
    from core.insights import insights, render
    from core.recurring import recurring
    from core.recurring import render as render_recurring

    # First, and whatever else is missing: it matters before any application.
    print(render_recurring(recurring()))
    print()
    try:
        applications = load_applications()
    except ApplyError as exc:
        print(f"callback insights: {exc}", file=sys.stderr)
        return 3
    if not applications:
        print("no applications recorded yet")
        return 0
    print(render(insights(applications)))
    return 0


def _cmd_applications(_args: argparse.Namespace) -> int:
    from collections import Counter

    from core.apply import ApplyError, load_applications

    try:
        applications = load_applications()
    except ApplyError as exc:
        print(f"callback applications: {exc}", file=sys.stderr)
        return 3
    for a in applications:
        latest = a.outcomes[-1].kind if a.outcomes else "no response yet"
        when = f"{a.submitted_at:%Y-%m-%d}"
        print(f"{when}  {a.job_id:<24} {a.company} / {a.title}  [{a.source}]  {latest}")
    by_source = Counter(a.source for a in applications)
    summary = ", ".join(f"{n} {source}" for source, n in by_source.most_common())
    print(f"{len(applications)} application(s){': ' + summary if summary else ''}")
    return 0


def _cmd_letter(args: argparse.Namespace) -> int:
    from core.buildcmd import BuildError
    from core.letter.cmd import build_letter
    from core.profile.store import StoreError
    from core.resume.io import DocumentError

    try:
        result = build_letter(args.doc)
    except (BuildError, DocumentError, StoreError, OSError) as exc:
        print(f"callback letter: {exc}", file=sys.stderr)
        return 3
    print(f"{'FAIL' if result.findings else 'PASS'}  {result.out_dir / 'cover-letter.pdf'}")
    for finding in result.findings:
        print(f"  {finding}")
    return result.exit_code


def _cmd_export(args: argparse.Namespace) -> int:
    from core.export.text import check_text_export, to_text
    from core.resume.io import DocumentError, load_doc

    try:
        doc = load_doc(args.doc)
    except (DocumentError, OSError) as exc:
        print(f"callback export: {exc}", file=sys.stderr)
        return 3
    text = to_text(doc)
    verdict, missing = check_text_export(doc, text)
    sys.stdout.write(text)
    for field in missing:
        print(f"callback export: lost in the text export: {field}", file=sys.stderr)
    return 0 if verdict == "pass" else 2


def _github_fetcher() -> object:
    """The GitHub API fetcher; a single place tests replace."""
    from core.discovery.fetch import HttpFetcher
    from core.profile.github import GITHUB_API

    return HttpFetcher(frozenset({GITHUB_API}))


def _cmd_facts_github(args: argparse.Namespace) -> int:
    import yaml

    from core.discovery.fetch import DiscoveryError, Fetcher
    from core.paths import ensure_home, resolve_inside, write_private
    from core.profile.github import fetch_drafts

    ensure_home()
    try:
        drafted = fetch_drafts(args.user, cast("Fetcher", _github_fetcher()))
    except DiscoveryError as exc:
        print(f"callback facts github: {exc}", file=sys.stderr)
        return 3
    for name, reason in drafted.skipped:
        print(f"skipped {name}: {reason}")
    if not drafted.facts:
        print("callback facts github: no repository could be drafted as a fact", file=sys.stderr)
        return 1
    out = resolve_inside(f"inbox/github-{args.user.lower()}.yaml")
    bodies = [f.model_dump(mode="json", exclude_none=True) for f in drafted.facts]
    write_private(out, yaml.safe_dump(bodies, sort_keys=False, allow_unicode=True))
    print(
        f"{len(drafted.facts)} draft fact(s) written to {out}. Read and correct them, then "
        f"`callback facts add {out}` and confirm each with `callback facts confirm`."
    )
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    from core.ingest.extract import ExtractionError, extract_document, gap_audit
    from core.paths import ensure_home, resolve_inside, write_private

    try:
        result = extract_document(args.file)
    except (ExtractionError, OSError) as exc:
        print(f"callback import: {exc}", file=sys.stderr)
        return 3
    ensure_home()
    stem = _SAFE_STEM.sub("-", args.file.stem.casefold()).strip("-") or "resume"
    out = resolve_inside(f"inbox/{stem}.txt")
    write_private(out, result.text)

    unscreened = result.unscreened
    found = [f for f in result.findings if f not in unscreened]
    if found:
        print("HIDDEN CONTENT FOUND -- do not trust the extracted text as written:")
        print("\n".join(f"  {f.kind}: {f.detail}" for f in found))
    if unscreened:
        print("COULD NOT SCREEN -- these parts were not checked for hidden text:")
        print("\n".join(f"  {f.kind}: {f.detail}" for f in unscreened))
    print(f"{result.pages} page(s) read; text written to {out}")
    gaps = gap_audit(result.text)
    if gaps:
        print("Advice (never a verdict):")
        print("\n".join(f"  {g}" for g in gaps))
    return 4 if result.findings else 0


def _print_fact_problems(problems: dict[str, list[str]], *, to: TextIO) -> None:
    for fid, details in problems.items():
        print(f"  {fid}: a bullet quoting this fact as written would fail", file=to)
        for detail in details:
            print(f"      {detail}", file=to)


def _cmd_facts_add(args: argparse.Namespace) -> int:
    from core.factscmd import add_from_file, check_facts
    from core.paths import ensure_home
    from core.profile.store import StoreError

    ensure_home()
    try:
        added, rejected = add_from_file(args.file, replace=args.replace)
        problems = check_facts(added).problems if added else {}
    except (StoreError, OSError) as exc:
        print(f"callback facts add: {exc}", file=sys.stderr)
        return 3
    print(f"added {len(added)} fact(s), all unconfirmed until you run `callback facts confirm`")
    for fid, reason in rejected:
        print(f"  refused {fid}: {reason}", file=sys.stderr)
    if problems:
        _print_fact_problems(problems, to=sys.stderr)
        print("  fix the file and add it again with --replace before confirming", file=sys.stderr)
    return 1 if rejected else 0


def _cmd_facts_check(args: argparse.Namespace) -> int:
    from core.factscmd import FactsError, check_facts
    from core.profile.store import StoreError

    try:
        result = check_facts(list(args.ids))
    except (FactsError, StoreError) as exc:
        print(f"callback facts check: {exc}", file=sys.stderr)
        return 3
    for fid, reason in result.not_checked.items():
        print(f"  {fid}: not checked: {reason}")
    count = len(result.checked)
    if not count:
        print("no fact was checked")
        return 0
    noun = "fact" if count == 1 else "facts"
    if not result.problems:
        print(f"{count} {noun} checked: each can be quoted as written")
        return 0
    print(f"{count} {noun} checked, {len(result.problems)} with findings:")
    _print_fact_problems(result.problems, to=sys.stdout)
    return 1


def _cmd_facts_list(args: argparse.Namespace) -> int:
    from core.factscmd import state
    from core.profile.store import load_facts

    for fid, fact in load_facts().items():
        mark = state(fact)
        if args.unconfirmed and mark != "unconfirmed":
            continue
        mark = mark.upper() if mark == "unconfirmed" else mark
        print(f"{mark:<11} {fid:<24} {fact.kind:<18} {fact.text}")
    return 0


def _cmd_facts_confirm(args: argparse.Namespace) -> int:
    from core.factscmd import ConfirmError, confirm

    try:
        confirmed = confirm(list(args.ids), include_declined=args.include_declined)
    except ConfirmError as exc:
        print(f"callback facts confirm: {exc}", file=sys.stderr)
        return 3
    print(f"confirmed {len(confirmed)} fact(s)")
    return 0


def _cmd_facts_retire(args: argparse.Namespace) -> int:
    from core.factscmd import ConfirmError, retire

    try:
        retired = retire(list(args.ids))
    except ConfirmError as exc:
        print(f"callback facts retire: {exc}", file=sys.stderr)
        return 3
    print(f"retired {len(retired)} fact(s)")
    return 0


def _cmd_notes_list(args: argparse.Namespace) -> int:
    from core.notes import NoteError, load

    try:
        book = load()
    except NoteError as exc:
        print(f"callback notes list: {exc}", file=sys.stderr)
        return 3
    shown = [n for n in book.notes if args.all or n.status == "active"]
    if not shown:
        print("no notes" if args.all else "no active notes")
    for n in shown:
        status = "" if n.status == "active" else f" [{n.status}]"
        print(f"{n.id:<6} {n.kind:<10} {n.text}{status}")
    return 0


def _cmd_notes_activate(args: argparse.Namespace) -> int:
    from core.notes import NoteError, activate

    try:
        done = activate(args.id)
    except NoteError as exc:
        print(f"callback notes activate: {exc}", file=sys.stderr)
        return 3
    print(f"{args.id} is active" if done else f"{args.id} left as it was")
    return 0


def _cmd_notes_propose(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from core.notes import NoteError, propose
    from core.paths import ensure_home

    ensure_home()
    try:
        note = propose(args.kind, args.text, source_context=args.context, now=datetime.now(tz=UTC))
    except NoteError as exc:
        print(f"callback notes propose: {exc}", file=sys.stderr)
        return 3
    print(f"{note.id} is proposed and does nothing yet")
    print(f"to activate it, run in your own terminal: callback notes activate {note.id}")
    return 0


def _cmd_notes_retire(args: argparse.Namespace) -> int:
    from core.notes import NoteError, retire

    try:
        retire(args.id)
    except NoteError as exc:
        print(f"callback notes retire: {exc}", file=sys.stderr)
        return 3
    print(f"{args.id} is retired")
    return 0


def _cmd_notes_families(args: argparse.Namespace) -> int:
    from core.notes import NoteError, role_families, set_role_families

    try:
        if args.families is None:
            families = role_families()
        else:
            families = set_role_families([f.strip() for f in args.families.split(",") if f.strip()])
    except NoteError as exc:
        print(f"callback notes families: {exc}", file=sys.stderr)
        return 3
    print(", ".join(families) if families else "no role families")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    from core.buildcmd import BuildError, build
    from core.profile.store import StoreError
    from core.resume.io import DocumentError

    try:
        result = build(args.doc, args.job)
    except (BuildError, DocumentError, StoreError, OSError) as exc:
        print(f"callback build: {exc}", file=sys.stderr)
        return 3
    print(f"{result.report.verdict.upper()}  {result.out_dir / 'resume.pdf'}")
    for outcome in result.report.outcomes:
        print(f"  {outcome.gate:<10} {outcome.verdict}")
    print(f"report: {result.out_dir / 'report.md'}")
    return result.exit_code


def _cmd_init(_args: argparse.Namespace) -> int:
    from core.paths import ensure_home

    home = ensure_home()
    print(f"initialised {home}")
    return 0


def _cmd_delete_all(_args: argparse.Namespace) -> int:
    from core.paths import delete_all

    for removed in delete_all():
        print(removed)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.handler is None:
        parser.print_help()
        return 0
    from core.profile.store import StoreError

    try:
        result: int = args.handler(args)
    except StoreError as exc:
        print(f"callback: {exc}", file=sys.stderr)
        return 3
    return result


if __name__ == "__main__":
    raise SystemExit(main())
