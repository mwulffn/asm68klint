"""Build the control-flow graph of a routine."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

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
from asm68klint.flow import changed_by
from asm68klint.m68k import (
    BRANCHES,
    CALLS,
    JUMPS,
    LOOPS,
    RETURNS,
    direct_target,
    is_instruction,
    is_one_word,
)
from asm68klint.model import Call, Effect, Exit, Kind, Node
from asm68klint.platforms import Platform
from asm68klint.registers import parse_list
from asm68klint.routines import Routine
from asm68klint.source import UNSCOPED, Statement, is_local, label_key
from asm68klint.tables import table_used

# Looks up a routine by name: returns what a call of it does, or the reason it
# cannot be found.
Resolver = Callable[[str], Effect | str]
# Looks up the routine that has a label inside it: returns that routine and
# the one that follows it, or None.
Borrow = Callable[[str], tuple[Routine, Routine | None] | None]
ANNOTATE = "add a lint: clobbers or lint: targets annotation"


class _Transfer(StrEnum):
    """The two ways to go to other code; the values are the words in messages."""

    CALL = "call"
    JUMP = "jump"


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
    # For the end of each routine whose code is here (see ``Node.limit``): the
    # routine that follows it in the source.
    after: dict[int, Routine | None] = field(default_factory=dict)
    folded: dict[str, str] | None = None
    # The calls of other routines: the node, which of its calls, and the name
    # of the routine or (for falling through into it) the routine itself.
    links: list[tuple[int, int, str | Routine]] = field(default_factory=list)
    resolve: Resolver | None = None

    def whatever_case(self) -> dict[str, str]:
        """Return the labels by their names in lower case, where that is one label."""
        if self.folded is None:
            counts: dict[str, list[str]] = {}
            for label in self.labels:
                counts.setdefault(label.lower(), []).append(label)
            self.folded = {
                low: names[0] for low, names in counts.items() if len(names) == 1
            }
        return self.folded


def build_graph(
    routine: Routine,
    following: Routine | None,
    resolve: Resolver,
    platform: Platform | None = None,
    symbols: Mapping[str, str | None] | None = None,
    tables: Mapping[str, list[str]] | None = None,
    borrow: Borrow | None = None,
) -> Graph:
    """Turn the code of a routine into nodes linked by control flow.

    ``following`` is the routine that comes next in the source, which execution
    falls into when it runs past the end of this one. With ``borrow``, a jump
    to a label inside another routine takes that routine's code in, so that it
    is followed from here with the stack as it is at the jump: code that
    several routines end in, say, which takes off the stack what each put on.
    """
    collector = _Collector(platform.silent if platform else frozenset(), symbols)
    collector.take(routine, following)
    graph = collector.graph
    taken = {id(routine)}
    position = 0
    while borrow and position < len(graph.nodes):
        node = graph.nodes[position]
        position += 1
        name = _jumps_to(node)
        if name and label_key(node.scope, name) not in graph.labels:
            found = borrow(name)
            if found and id(found[0]) not in taken:
                taken.add(id(found[0]))
                collector.take(*found, borrowed=True)
    graph.resolve = resolve
    linker = _Linker(graph, routine, resolve, platform, tables or {})
    for node in graph.nodes:
        linker.link(node)
    _settle_local_calls(graph)
    return graph


def refresh(graph: Graph) -> None:
    """Look up again what the routines a graph's code calls do.

    For when that has changed since the graph was built: what routines
    without headers do is worked out in rounds.
    """
    for index, position, target in graph.links:
        calls = graph.nodes[index].calls
        effect = target.effect if isinstance(target, Routine) else None
        if isinstance(target, str) and graph.resolve:
            effect = graph.resolve(target)
        if isinstance(effect, Effect):
            calls[position] = replace(calls[position], effect=effect)
    _settle_local_calls(graph)


def _jumps_to(node: Node) -> str | None:
    """Return the label an instruction jumps or branches to, if it names one."""
    statement = node.statement
    jumps = statement.mnemonic in JUMPS | BRANCHES | LOOPS
    if node.kind != Kind.CODE or not jumps or not statement.operands:
        return None
    return direct_target(statement.operands[-1])


def _skips_one(node: Node, graph: Graph) -> bool:
    """True for a short branch to ``*+4`` over an instruction of one word.

    ``beq.s *+4`` followed by ``rts`` is a way to write a conditional return.
    Where the next instruction is, or may be, longer than a word, the branch
    cannot be followed.
    """
    statement = node.statement
    short = statement.size in ("s", "b") and statement.operands[-1:] == ("*+4",)
    if not short or node.index + 2 > node.limit - 1:
        return False
    return is_one_word(graph.nodes[node.index + 1].statement)


def _settle_local_calls(graph: Graph) -> None:
    """Give the calls of code inside the routine what that code changes.

    Such code may call more of the same, so this goes round until nothing is
    added.
    """
    callers = [node for node in graph.nodes if node.local_calls]
    if not callers:
        return
    outside = {
        node.index: [call for call in node.calls if not call.local] for node in callers
    }
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
                    local=True,
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

    def take(
        self, routine: Routine, following: Routine | None, borrowed: bool = False
    ) -> None:
        """Take in the code of a routine."""
        nodes = self.graph.nodes
        start = len(nodes)
        self.scope = ""
        self.pending = {}
        self.conditionals = []
        for statement in routine.body:
            self.add(statement)
        self.finish()
        if len(nodes) in self.graph.labels.values() and routine.body:
            last = routine.body[-1]  # a label with nothing after it
            self._node(Statement(last.file, last.line, ""), Kind.END)
        for node in nodes[start:]:
            node.borrowed = borrowed
            node.limit = len(nodes)
        self.graph.after[len(nodes)] = following

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
            self._node(statement, Kind.DATA)
        else:
            node = self._node(statement, Kind.CODE)
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

    def _node(self, statement: Statement, kind: Kind) -> Node:
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
            skips.append(self._node(statement, Kind.SKIP))
            if waiting:
                waiting.jumps.append(len(self.graph.nodes))
            waiting = self._fork(statement) if mnemonic in ELSE_IF else None
            self.conditionals.append((waiting, skips))

    def _fork(self, statement: Statement) -> Node:
        node = self._node(statement, Kind.FORK)
        node.condition, node.negated = condition(statement)
        return node

    def _close(self, waiting: Node | None, skips: list[Node]) -> None:
        """Send the ends of the branches of a conditional to what follows it."""
        for node in [*skips, waiting]:
            if node:
                node.jumps.append(len(self.graph.nodes))


def _text(node: Node) -> str:
    """Return an instruction as the messages show it."""
    return f"{node.statement.mnemonic or ''} {','.join(node.statement.operands)}"


class _Linker:
    """Works out where execution goes after each node and what a call there changes."""

    def __init__(
        self,
        graph: Graph,
        routine: Routine,
        resolve: Resolver,
        platform: Platform | None,
        tables: Mapping[str, list[str]],
    ) -> None:
        self.graph = graph
        self.title = routine.header.title
        self.resolve = resolve
        self.platform = platform
        self.tables = tables

    def link(self, node: Node) -> None:
        """Give a node its successors, its calls and the way the routine ends there."""
        mnemonic = node.statement.mnemonic or ""
        operands = node.statement.operands
        conditional = mnemonic in BRANCHES or mnemonic in LOOPS
        if "noreturn" in node.annotations and node.kind == Kind.CODE:
            if conditional:  # only the branch taken is gone for good
                self._follow(node, node.index + 1)
            return
        if "out" in node.annotations and "clobbers" not in node.annotations:
            message = (
                "lint: out says which of the registers of lint: clobbers are results"
            )
            node.errors.append(("S005", message + ": there is no lint: clobbers here"))
        node.exit = Exit(RETURNS[mnemonic]) if mnemonic in RETURNS else None
        if node.exit or node.is_data:
            return
        if node.kind in (Kind.FORK, Kind.SKIP):
            if node.kind == Kind.FORK:
                self._follow(node, node.index + 1)
            for index in node.jumps:
                self._follow(node, index)
            return
        if mnemonic in JUMPS:
            self._transfer(node, _Transfer.JUMP)
            return
        if mnemonic in CALLS:
            self._transfer(node, _Transfer.CALL)
        elif mnemonic == "trap":
            self._trap(node)
        elif conditional:
            self._transfer(node, _Transfer.JUMP)
        elif mnemonic == "movem" and not any(map(parse_list, operands)):
            message = f"cannot tell which registers {_text(node)} uses"
            node.errors.append(("S004", message))
        made = [call for call in node.calls if not call.tail]
        if made and not any(call.effect.returns for call in made):
            return  # none of the routines called here returns
        after = node.index + 1
        if "inline" in node.annotations:  # the code called returns after its data
            nodes = self.graph.nodes
            while after < len(nodes) and nodes[after].is_data:
                after += 1
        self._follow(node, after)

    def _follow(self, node: Node, index: int) -> None:
        """Go on to the node that comes next, or out of the end of the routine."""
        following = self.graph.after.get(node.limit)
        if index < node.limit:
            node.successors.append(index)
        elif following:
            via = f"falling through into {following.header.title}"
            self.graph.links.append((node.index, len(node.calls), following))
            node.calls.append(Call(via, following.effect, tail=True))
            node.exit = node.exit or Exit.TAIL
        else:
            node.falls_off = True

    def _key(self, node: Node, target: str) -> str:
        """Return the name a label is kept under.

        Some assemblers take ``.Loop`` and ``.loop`` for one label: a label
        that is not there as written is looked for whatever its case.
        """
        name = label_key(node.scope, target)
        if name not in self.graph.labels:
            return self.graph.whatever_case().get(name.lower(), name)
        return name

    def _leave(
        self, node: Node, via: str, effect: Effect | set[str], kind: _Transfer
    ) -> None:
        """Record a call, or a jump out of the routine, that changes registers.

        Of registers an annotation names, no more is known than that.
        """
        if not isinstance(effect, Effect):
            out = node.annotations.get("out")
            results = out.registers if out else set()
            lost = frozenset(effect - results) if out else frozenset()
            effect = Effect(frozenset(effect | results), garbage=lost)
        node.calls.append(Call(via, effect, tail=kind == _Transfer.JUMP))
        if kind == _Transfer.JUMP:
            node.exit = Exit.TAIL

    def _go_to(self, node: Node, target: str, kind: _Transfer) -> None:
        """Call or jump to a label: one in this routine or another routine."""
        labels = self.graph.labels
        if kind == _Transfer.JUMP and self._key(node, target) in labels:
            node.successors.append(labels[self._key(node, target)])
            return
        local = is_local(target) and not target.startswith(UNSCOPED)
        if kind == _Transfer.JUMP and local:
            message = f"cannot find the label {target} that {self.title} branches to"
            node.errors.append(("F002", message))
            return
        found = self.resolve(target)
        if isinstance(found, str):
            message = f"cannot analyse the {kind} to {target}: {found}"
            node.errors.append(("F001", message))
        else:
            self.graph.links.append((node.index, len(node.calls), target))
            self._leave(node, f"the {kind} to {target}", found, kind)

    def _transfer(self, node: Node, kind: _Transfer) -> None:
        """Handle a call or jump, using the annotations where they are needed."""
        labels = self.graph.labels
        operands = node.statement.operands
        text = _text(node)
        name = direct_target(operands[-1]) if operands else None
        clobbers = node.annotations.get("clobbers")
        targets = node.annotations.get("targets")
        known = self.platform.call(node.statement) if self.platform else None
        through = table_used(operands[-1]) if operands and not name else None
        table = self.tables.get(self._key(node, through)) if through else None
        if name and kind == _Transfer.JUMP and self._key(node, name) in labels:
            self._go_to(node, name, kind)
        elif name and is_local(name) and self._key(node, name) in labels:
            node.local_calls.append(labels[self._key(node, name)])
        elif clobbers and name:
            self._leave(node, f"the {kind} to {name}", clobbers.registers, kind)
        elif clobbers:
            self._leave(node, f"the indirect {kind} {text}", clobbers.registers, kind)
        elif name:
            self._go_to(node, name, kind)
        elif targets:
            for target in targets.labels:
                self._go_to(node, target, kind)
        elif through and table:
            self._through_table(node, through, table, kind)
        elif known:
            self._leave(node, f"the system call {text}", known, kind)
        elif kind == _Transfer.JUMP and _skips_one(node, self.graph):
            node.successors.append(node.index + 2)
        else:
            message = f"cannot analyse the indirect {kind} {text}; {ANNOTATE}"
            node.errors.append(("F001", message))

    def _through_table(
        self, node: Node, through: str, table: list[str], kind: _Transfer
    ) -> None:
        """Go where a jump table leads.

        A row of branches in this routine is jumped into; a table of offsets
        leads straight to where its entries say.
        """
        nodes = self.graph.nodes
        row = self.graph.labels.get(self._key(node, through), len(nodes))
        rows = []
        while row < len(nodes) and kind == _Transfer.JUMP:
            if nodes[row].statement.mnemonic not in JUMPS:
                break
            rows.append(row)
            row += 1
        node.successors.extend(rows)
        for target in () if rows else table:
            self._go_to(node, target, kind)

    def _trap(self, node: Node) -> None:
        """Handle a trap: a call of the system, or of what an annotation says."""
        text = _text(node)
        clobbers = node.annotations.get("clobbers")
        known = self.platform.call(node.statement) if self.platform else None
        if clobbers:
            self._leave(node, f"the {text}", clobbers.registers, _Transfer.CALL)
        elif known:
            self._leave(node, f"the system call {text}", known, _Transfer.CALL)
        else:
            message = f"cannot analyse {text}; add a lint: clobbers annotation"
            node.errors.append(("F001", message))
