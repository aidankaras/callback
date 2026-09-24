"""Describing a parse or validation failure without quoting what was parsed.

pydantic's and PyYAML's own messages reprint the input: a field's value, the
offending line with a caret beneath it. A document handed to a callback tool
may be any file on the machine, so a message that reached the model with its
contents would read files the session was never allowed to open. These keep
where the problem is and what kind it is, which is what fixing it needs.
"""

from __future__ import annotations

import re

import yaml
from pydantic import ValidationError


def validation_problems(exc: ValidationError) -> str:
    """Each error as `location: message`, without the input it rejected."""
    return "; ".join(
        f"{'.'.join(str(p) for p in error['loc']) or '(document)'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
    )


def yaml_problem(exc: yaml.YAMLError) -> str:
    """Where a YAML document fails to parse and why, without its text.

    `problem` can quote a name from the file ("found undefined alias 'x'"), so
    quoted parts go.
    """
    if not isinstance(exc, yaml.MarkedYAMLError):
        return "not valid YAML"
    where = ""
    if exc.problem_mark is not None:
        where = f" at line {exc.problem_mark.line + 1}, column {exc.problem_mark.column + 1}"
    problem = re.sub(r"'[^']*'", "'...'", exc.problem or "")
    return f"not valid YAML{where}" + (f": {problem}" if problem else "")
