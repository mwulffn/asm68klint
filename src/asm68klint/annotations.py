"""Lint annotations: ``; lint: keyword arguments`` comments.

``clobbers``  the registers changed by the call or jump on that line
``targets``   the labels an indirect call or jump may go to
``allow``     reserved registers that may be written
``inline``    the code called takes the data that follows the call and
              returns after it
``out``       with ``clobbers``: the registers among them that hold a result;
              the others hold nothing of use after the call
``noreturn``  execution does not come back from here: a jump into another
              program, a return into another task
``ignore``    rules that are not to be reported here
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from asm68klint.findings import Finding
from asm68klint.registers import parse_list
from asm68klint.rules import RULES
from asm68klint.source import LABEL_PATTERN, Statement

KEYWORDS = ("clobbers", "targets", "allow", "inline", "out", "noreturn", "ignore")
# The annotations that may stand in a routine's header, for the whole routine.
IN_HEADER = ("allow", "noreturn", "ignore")
REGISTERS = ("clobbers", "allow", "out")  # the annotations that take registers
_ANNOTATION = re.compile(r"\blint:\s*(\S+)\s*(.*)", re.IGNORECASE)
_LABEL = re.compile(LABEL_PATTERN)


@dataclass
class Annotation:
    """One annotation, with its arguments parsed.

    ``labels`` holds the labels of ``targets`` and the rule codes of ``ignore``.
    """

    keyword: str
    registers: set[str]
    labels: list[str]


def is_rule(text: str) -> bool:
    """True for a rule's code or the beginning of some."""
    return bool(text) and any(code.startswith(text.upper()) for code in RULES)


def ignored(code: str, prefixes: Iterable[str]) -> bool:
    """True when a rule is among those an ``ignore`` annotation names."""
    return code.startswith(tuple(prefixes)) if prefixes else False


def parse_annotation(statement: Statement) -> Annotation | Finding | None:
    """Parse the annotation in a statement's comment, if it has one."""
    match = _ANNOTATION.search(statement.comment)
    if not match:
        return None
    keyword, argument = match.group(1).lower(), match.group(2).strip()
    items = [item.strip() for item in argument.split(",")]
    annotation = Annotation(keyword, set(), [])
    if keyword not in KEYWORDS:
        message = f"unknown lint annotation {match.group(1)!r}"
    elif keyword in ("inline", "noreturn"):
        return annotation  # what follows the word is a remark
    elif keyword == "ignore" and all(map(is_rule, items)):
        annotation.labels = [item.upper() for item in items]
        return annotation
    elif keyword == "targets" and all(_LABEL.fullmatch(item) for item in items):
        annotation.labels = items
        return annotation
    elif keyword in REGISTERS and (argument == "-" or all(map(parse_list, items))):
        for item in items:
            annotation.registers.update(parse_list(item) or [])
        return annotation
    else:
        message = f"cannot parse the lint annotation {keyword + ' ' + argument!r}"
    return Finding(statement.file, statement.line, "S005", message)
