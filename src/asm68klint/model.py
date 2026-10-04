"""What a routine's control-flow graph is made of: nodes, calls and effects."""

from dataclasses import dataclass, field
from enum import StrEnum

from asm68klint.annotations import Annotation
from asm68klint.source import Statement


class Kind(StrEnum):
    """What a node stands for."""

    CODE = "code"  # an instruction
    DATA = "data"  # a data directive
    # The end of a routine that has a label and nothing after it.
    END = "end"
    # Conditional assembly. A fork (if) goes on to the next node when its
    # condition holds and to ``Node.jumps`` when it does not; a skip (else),
    # at the end of a branch, goes to ``Node.jumps``, past the endc.
    FORK = "fork"
    SKIP = "skip"


class Exit(StrEnum):
    """How a routine ends at a node."""

    RTS = "rts"
    RTE = "rte"
    TAIL = "tail"  # a jump elsewhere


@dataclass(frozen=True)
class Effect:
    """What running some other code does to the registers.

    ``changed`` are the registers that may not hold what they held before.
    ``inputs`` are the ones it reads, and ``garbage`` the changed ones that
    hold nothing of use afterwards; both are empty where that is not known.
    """

    changed: frozenset[str] = frozenset()
    inputs: frozenset[str] = frozenset()
    garbage: frozenset[str] = frozenset()
    # False when the code may read more registers than ``inputs`` says.
    inputs_known: bool = False
    returns: bool = True  # False when execution does not come back from it


@dataclass(frozen=True)
class Call:
    """A call or jump to other code, and the registers that code changes.

    A tail call leaves the routine: its effect counts where the routine ends,
    not on the instructions that follow.
    """

    via: str  # for messages: "the call to Foo"
    effect: Effect
    tail: bool = False
    local: bool = False  # a call of code in the same routine

    @property
    def registers(self) -> frozenset[str]:
        """The registers the call may change."""
        return self.effect.changed


@dataclass
class Node:
    """One instruction in a routine, with where execution goes next."""

    statement: Statement
    index: int = 0
    successors: list[int] = field(default_factory=list)
    exit: Exit | None = None  # how the routine ends here, if it does
    kind: Kind = Kind.CODE
    jumps: list[int] = field(default_factory=list)  # see ``Kind.FORK``
    condition: str = ""  # what a fork tests
    negated: bool = False  # the fork tests for the opposite of ``condition``
    scope: str = ""  # the global label that local labels here belong to
    annotations: dict[str, Annotation] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    # True for code of another routine that this one jumps into, taken in to
    # be followed from here; what is wrong in it is reported in its own.
    borrowed: bool = False
    limit: int = 0  # the node after the last of the routine this one is from
    # Calls of code in the same routine: the nodes they go to.
    local_calls: list[int] = field(default_factory=list)
    falls_off: bool = False  # execution runs past the end of the file
    # Why it cannot be analysed: (rule code, message) pairs.
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def leaves(self) -> Exit | None:
        """How the routine ends here for its caller: ``exit``, or None.

        A jump to code that execution does not come back from ends the path
        and not the routine: nothing is handed back there.
        """
        gone = self.exit == Exit.TAIL and not any(
            call.tail and call.effect.returns for call in self.calls
        )
        return None if gone else self.exit

    @property
    def is_data(self) -> bool:
        """True for a data directive."""
        return self.kind == Kind.DATA
