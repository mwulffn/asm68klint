"""Build the control-flow graph of a routine."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from asm68klint.annotations import Annotation, parse_annotation
from asm68klint.directives import (
    ELSE,
    ELSE_IF,
    END_IF,
    IF,
    code_label,
    condition,
    decided,
    is_data,
    is_ignored,
)
from asm68klint.findings import Finding
from asm68klint.flow import RETURNS, Call, Effect, Node, changed_by, direct_target
from asm68klint.m68k import BRANCHES, LOOPS, is_instruction
from asm68klint.platforms import Platform
from asm68klint.registers import parse_list
from asm68klint.routines import Routine
from asm68klint.source import UNSCOPED, Statement, is_local, label_key
from asm68klint.tables import table_used

# Looks up a routine by name: returns what a call of it does, or the reason it
# cannot be found.
Resolver = Callable[[str], Effect | str]
ANNOTATE = "add a lint: clobbers or lint: targets annotation"


@dataclass
class Graph:
    """The nodes of a routine and what was found while linking them."""

    nodes: list[Node] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    # Global labels inside the routine, by the node they point at.
    entries: dict[int, Statement] = field(default_factory=dict)
    problems: list[Finding] = field(default_factory=list)
    # The nodes that calls from inside the routine go to.
    local_entries: set[int] = field(default_factory=set)


def build_graph(
    routine: Routine,
    following: Routine | None,
    resolve: Resolver,
    platform: Platform | None = None,
    symbols: Mapping[str, str | None] | None = None,
    tables: Mapping[str, list[str]] | None = None,
) -> Graph:
    """Turn the code of a routine into nodes linked by control flow.

    ``following`` is the routine that comes next in the source, which execution
    falls into when it runs past the end of this one.
    """
    collector = _Collector(platform.silent if platform else frozenset(), symbols)
    for statement in routine.body:
        collector.add(statement)
    collector.finish()
    graph = collector.graph
    for node in graph.nodes:
        _link(node, graph, routine, following, resolve, platform, tables or {})
    _settle_local_calls(graph)
    return graph


def _settle_local_calls(graph: Graph) -> None:
    """Give the calls of code inside the routine what that code changes.

    Such code may call more of the same, so this goes round until nothing is
    added.
    """
    callers = [node for node in graph.nodes if node.local_calls]
    if not callers:
        return
    outside = {node.index: list(node.calls) for node in callers}
    names = {index: label for label, index in graph.labels.items()}
    changed = {index: frozenset() for node in callers for index in node.local_calls}
    graph.local_entries = set(changed)
    growing = True
    while growing:
        growing = False
        for node in callers:
            node.calls = outside[node.index] + [
                Call(
                    f"the call to {names[index].removeprefix(node.scope)}",
                    Effect(changed[index]),
                )
                for index in node.local_calls
            ]
        for index, before in changed.items():
            changed[index] = changed_by(graph.nodes, index)
            growing = growing or changed[index] != before


class _Collector:
    """Creates the nodes of a routine and records its labels and annotations."""

    def __init__(
        self,
        silent: frozenset[str] = frozenset(),
        symbols: Mapping[str, str | None] | None = None,
    ) -> None:
        self.graph = Graph()
        self.silent = silent  # macros of a platform that emit nothing
        self.symbols = symbols  # names the user has defined, or said are not
        self.scope = ""  # the last global label
        # Annotations waiting for the next instruction.
        self.pending: dict[str, Annotation] = {}
        # Annotations given on a macro call, which apply to every instruction
        # of that expansion: the expansion's number and the annotations.
        self.spread: tuple[int, dict[str, Annotation]] = (0, {})
        # Open conditionals: the fork that still has to learn where to go when
        # its condition fails (None after an else), and the skips so far. For
        # one that the text decides there is no fork: its branches are taken
        # or left out here, and it is [a branch was taken, this one is].
        self.conditionals: list[tuple[Node | None, list[Node]] | list[bool]] = []

    def add(self, statement: Statement) -> None:
        """Take in the next statement of the routine."""
        mnemonic = statement.mnemonic
        if self._left_out() and mnemonic not in IF | ELSE | ELSE_IF | END_IF:
            return
        self._read_annotation(statement)
        self._read_label(statement)
        if mnemonic is None or statement.is_macro_call or is_ignored(statement):
            return
        if mnemonic in self.silent:
            return
        if mnemonic in IF | ELSE | ELSE_IF | END_IF:
            self._conditional(statement)
        elif is_data(statement):
            self._node(statement, "data")
        else:
            node = self._node(statement, "code")
            if statement.expansion and statement.expansion == self.spread[0]:
                node.annotations.update(self.spread[1])
            node.annotations.update(self.pending)
            self.pending = {}
            if not is_instruction(mnemonic):
                node.errors.append(
                    (
                        "S002",
                        f"unknown instruction, directive or macro {statement.name!r}",
                    )
                )

    def finish(self) -> None:
        """Close the conditionals that the routine leaves open."""
        for item in self.conditionals:
            if isinstance(item, tuple):
                self._close(*item)

    def _left_out(self) -> bool:
        """True inside a branch that a decided conditional does not take."""
        return any(isinstance(item, list) and not item[1] for item in self.conditionals)

    def _node(self, statement: Statement, kind: str) -> Node:
        nodes = self.graph.nodes
        nodes.append(Node(statement, len(nodes), kind=kind, scope=self.scope))
        return nodes[-1]

    def _read_annotation(self, statement: Statement) -> None:
        annotation = parse_annotation(statement)
        if isinstance(annotation, Finding):
            self.graph.problems.append(annotation)
        elif annotation:
            self.pending[annotation.keyword] = annotation
        if statement.is_macro_call and self.pending:
            self.spread = (statement.expansion, self.pending)
            self.pending = {}

    def _read_label(self, statement: Statement) -> None:
        index = len(self.graph.nodes)
        if statement.label and is_local(statement.label):
            self.graph.labels[label_key(self.scope, statement.label)] = index
        elif code_label(statement):
            self.scope = statement.label
            self.graph.labels[self.scope] = index
            self.graph.entries.setdefault(index, statement)

    def _conditional(self, statement: Statement) -> None:
        """Turn conditional assembly into forks and skips.

        A conditional that is not opened inside the routine is ignored.
        """
        mnemonic = statement.mnemonic
        if mnemonic in IF:
            outcome = decided(statement, self.symbols)
            if self._left_out():
                self.conditionals.append([True, False])
            elif outcome is None:
                self.conditionals.append((self._fork(statement), []))
            else:
                self.conditionals.append([outcome, outcome])
        elif not self.conditionals:
            return
        elif isinstance(self.conditionals[-1], list):
            item = self.conditionals[-1]
            if mnemonic in END_IF:
                self.conditionals.pop()
            else:  # an elif that the text does not decide counts as true
                holds = (
                    mnemonic in ELSE or decided(statement, self.symbols) is not False
                )
                item[1] = not item[0] and holds
                item[0] = item[0] or item[1]
        elif mnemonic in END_IF:
            self._close(*self.conditionals.pop())
        else:
            waiting, skips = self.conditionals.pop()
            skips.append(self._node(statement, "skip"))
            if waiting:
                waiting.jumps.append(len(self.graph.nodes))
            waiting = self._fork(statement) if mnemonic in ELSE_IF else None
            self.conditionals.append((waiting, skips))

    def _fork(self, statement: Statement) -> Node:
        node = self._node(statement, "fork")
        node.condition, node.negated = condition(statement)
        return node

    def _close(self, waiting: Node | None, skips: list[Node]) -> None:
        """Send the ends of the branches of a conditional to what follows it."""
        for node in [*skips, waiting]:
            if node:
                node.jumps.append(len(self.graph.nodes))


def _link(
    node: Node,
    graph: Graph,
    routine: Routine,
    following: Routine | None,
    resolve: Resolver,
    platform: Platform | None,
    tables: Mapping[str, list[str]],
) -> None:
    """Work out where execution goes after a node and what a call there changes."""
    title = routine.header.title
    mnemonic = node.statement.mnemonic or ""
    operands = node.statement.operands
    text = f"{mnemonic} {','.join(operands)}"
    name = direct_target(operands[-1]) if operands else None
    clobbers = node.annotations.get("clobbers")
    targets = node.annotations.get("targets")

    def follow(index: int) -> None:
        if index < len(graph.nodes):
            node.successors.append(index)
        elif following:
            via = f"falling through into {following.header.title}"
            node.calls.append(Call(via, following.effect, tail=True))
            node.exit = node.exit or "tail"
        else:
            node.falls_off = True

    def key(target: str) -> str:
        """Return the name a label is kept under.

        Some assemblers take ``.Loop`` and ``.loop`` for one label: a label
        that is not there as written is looked for whatever its case.
        """
        name = label_key(node.scope, target)
        if name not in graph.labels:
            same = [label for label in graph.labels if label.lower() == name.lower()]
            if len(same) == 1:
                return same[0]
        return name

    def leave(via: str, effect: Effect | set[str], kind: str) -> None:
        """Record a call, or a jump out of the routine, that changes registers.

        Of registers an annotation names, no more is known than that.
        """
        if not isinstance(effect, Effect):
            effect = Effect(frozenset(effect))
        node.calls.append(Call(via, effect, tail=kind == "jump"))
        if kind == "jump":
            node.exit = "tail"

    def go_to(target: str, kind: str) -> None:
        """Call or jump to a label: one in this routine or another routine."""
        if kind == "jump" and key(target) in graph.labels:
            follow(graph.labels[key(target)])
            return
        if kind == "jump" and is_local(target) and not target.startswith(UNSCOPED):
            message = f"cannot find the label {target} that {title} branches to"
            node.errors.append(("F002", message))
            return
        found = resolve(target)
        if isinstance(found, str):
            node.errors.append(
                ("F001", f"cannot analyse the {kind} to {target}: {found}")
            )
        else:
            leave(f"the {kind} to {target}", found, kind)

    def transfer(kind: str) -> None:
        """Handle a call or jump, using the annotations where they are needed."""
        known = platform.call(node.statement) if platform else None
        through = table_used(operands[-1]) if operands and not name else None
        table = tables.get(key(through)) if through else None
        if name and kind == "jump" and key(name) in graph.labels:
            go_to(name, kind)
        elif name and is_local(name) and key(name) in graph.labels:
            node.local_calls.append(graph.labels[key(name)])
        elif clobbers and name:
            leave(f"the {kind} to {name}", clobbers.registers, kind)
        elif clobbers:
            leave(f"the indirect {kind} {text}", clobbers.registers, kind)
        elif name:
            go_to(name, kind)
        elif targets:
            for target in targets.labels:
                go_to(target, kind)
        elif table:
            # A row of branches in this routine is jumped into; a table of
            # offsets leads straight to where its entries say.
            row = graph.labels.get(key(through or ""), len(graph.nodes))
            rows = []
            while row < len(graph.nodes) and kind == "jump":
                if graph.nodes[row].statement.mnemonic not in ("bra", "jmp"):
                    break
                rows.append(row)
                row += 1
            for index in rows:
                follow(index)
            for target in () if rows else table:
                go_to(target, kind)
        elif known:
            leave(f"the system call {text}", known, kind)
        else:
            message = f"cannot analyse the indirect {kind} {text}; {ANNOTATE}"
            node.errors.append(("F001", message))

    node.exit = RETURNS.get(mnemonic)
    if node.exit or node.is_data:
        return
    if node.kind in ("fork", "skip"):
        if node.kind == "fork":
            follow(node.index + 1)
        for index in node.jumps:
            follow(index)
        return
    if mnemonic in ("bra", "jmp"):
        transfer("jump")
        return
    if mnemonic in ("bsr", "jsr"):
        transfer("call")
    elif mnemonic == "trap" and clobbers:
        leave(f"the {text}", clobbers.registers, "call")
    elif mnemonic == "trap" and platform and platform.call(node.statement):
        leave(f"the system call {text}", platform.call(node.statement), "call")
    elif mnemonic == "trap":
        message = f"cannot analyse {text}; add a lint: clobbers annotation"
        node.errors.append(("F001", message))
    elif mnemonic in BRANCHES or mnemonic in LOOPS:
        transfer("jump")
    elif mnemonic == "movem" and not any(map(parse_list, operands)):
        node.errors.append(("S004", f"cannot tell which registers {text} uses"))
    after = node.index + 1
    if "inline" in node.annotations:  # the code called returns after its data
        while after < len(graph.nodes) and graph.nodes[after].is_data:
            after += 1
    follow(after)
