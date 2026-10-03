"""The rules: what each finding's code means.

A code is a letter for the group and a number. Codes are stable: a rule that
goes away leaves its number unused.

``H``  routine headers
``R``  registers and the stack
``F``  control flow
``S``  source the linter cannot read
"""

from collections.abc import Iterable
from dataclasses import dataclass

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Rule:
    """One rule: its code, a name, its severity and what it reports."""

    code: str
    name: str
    severity: str
    summary: str


RULES = {
    rule.code: rule
    for rule in (
        Rule("H001", "missing-header", ERROR, "code that no routine header covers"),
        Rule("H002", "header-name", ERROR, "a header without a routine name"),
        Rule("H003", "header-field", ERROR, "a header without one of its fields"),
        Rule("H004", "header-empty-field", ERROR, "a header field with nothing in it"),
        Rule("H005", "header-clobbers", ERROR, "a Clobbers field that is not a list"),
        Rule("H006", "header-label", ERROR, "a header not followed by its label"),
        Rule("R001", "undeclared-change", ERROR, "a register changed, not declared"),
        Rule("R002", "stale-header", WARNING, "a register declared, never changed"),
        Rule("R003", "reserved-write", ERROR, "a write to a reserved register"),
        Rule("R004", "interrupt-preserve", ERROR, "an interrupt changes a register"),
        Rule("R005", "interrupt-clobbers", ERROR, "an interrupt declares Clobbers"),
        Rule("R006", "unbalanced-stack", ERROR, "the stack is not as it was found"),
        Rule("R007", "clobbered-read", WARNING, "a register read after a call lost it"),
        Rule("R008", "undeclared-input", WARNING, "a register read, not under In"),
        Rule("R009", "unset-output", WARNING, "an Out register some path never sets"),
        Rule("R010", "unused-input", WARNING, "an In register that is never read"),
        Rule("F001", "unknown-call", ERROR, "a call or jump that cannot be followed"),
        Rule("F002", "missing-label", ERROR, "a branch to a label that is not there"),
        Rule("F003", "runs-into-data", ERROR, "execution runs into data"),
        Rule("F004", "runs-off-end", ERROR, "execution runs off the end of the file"),
        Rule("F005", "unreachable", WARNING, "code that nothing reaches"),
        Rule("S001", "include-not-found", ERROR, "an include file that is missing"),
        Rule("S002", "unknown-instruction", ERROR, "an unknown instruction or macro"),
        Rule("S003", "macro", ERROR, "a macro that cannot be read or expanded"),
        Rule("S004", "register-list", ERROR, "a movem with an unreadable list"),
        Rule("S005", "annotation", ERROR, "a lint annotation that is wrong"),
    )
}


def chosen(select: Iterable[str] = (), ignore: Iterable[str] = ()) -> set[str]:
    """Return the codes of the rules to report.

    ``select`` and ``ignore`` hold codes or their beginnings (``R`` for every
    register rule). Nothing selected means every rule. A name that matches no
    rule raises ValueError.
    """

    def matching(prefixes: Iterable[str]) -> set[str]:
        found: set[str] = set()
        for prefix in prefixes:
            codes = {code for code in RULES if code.startswith(prefix.upper())}
            if not codes or not prefix:
                raise ValueError(f"{prefix!r} matches no rule")
            found |= codes
        return found

    return (matching(select) or set(RULES)) - matching(ignore)
