"""What a review of the code found: a test for each thing that was wrong."""

from pathlib import Path

from asm68klint import lint_files
from asm68klint.fix import fix_files
from asm68klint.format import format_files
from asm68klint.linter import free_registers

FATAL = """
;--
; Fatal
; In:       d0 = code
; Out:      -
; Clobbers: d5
; lint: noreturn
Fatal:
	move.w	d0,d5
.Loop	bra	.Loop
"""


def routine(body: str, clobbers: str = "-", takes: str = "-", extra: str = "") -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    header = f";--\n; Foo\n; In:       {takes}\n; Out:      -\n; Clobbers: {clobbers}\n"
    return header + extra + f"Foo:\n{body}"


def test_a_routine_without_a_header_calls_one_that_does_not_return(lint):
    source = "Helper:\tmoveq\t#1,d0\n\tbsr\tFatal\n\trts\n" + FATAL
    found = lint(source, {"infer": True, "reserved": []})
    assert found == ["main.s:3: warning: unreachable code in Helper is not checked"]


def test_one_target_that_does_not_return_does_not_end_the_path(lint):
    body = "\tjsr\t(a0)\t; lint: targets Fatal, Work\n\tmoveq\t#0,d3\n\trts\n"
    work = (
        ";--\n; Work\n; In: -\n; Out: -\n; Clobbers: d0\nWork:\tmoveq\t#0,d0\n\trts\n"
    )
    found = lint(routine(body, "d0") + work + FATAL, {"ignore": ["R007", "R008"]})
    assert found == [
        "main.s:8: error: d3 is written but not listed under Out or Clobbers of Foo"
    ]


def test_what_a_routine_that_does_not_return_reads_is_still_read(lint, tmp_path):
    body = "\ttst.w\td1\n\tbne\tFatal\n\trts\n"
    reads = {"select": ["R008", "R010"], "reserved": []}
    assert lint(routine(body, takes="d0 = code, d1 = flag") + FATAL, reads) == []
    assert lint(routine(body, takes="d1 = flag") + FATAL, reads) == [
        (
            "main.s:8: warning: d0 is needed by the jump to Fatal but not listed"
            " under In of Foo"
        )
    ]
    path = tmp_path / "free.s"
    path.write_text(routine("\tmoveq\t#3,d0\n\tnop\n\tbsr\tFatal\n", "d0") + FATAL)
    found = free_registers([path], path, 8, reserved=[])
    assert found is not None
    assert "d0" in found[2]


def test_code_called_inside_a_routine_that_does_not_return_is_checked(lint):
    body = "\tbsr\t.Sub\n\tbra\tFoo\n.Sub\tmove.l\td0,-(sp)\n\trts\n"
    found = lint(routine(body, extra="; lint: noreturn\n"), {"reserved": []})
    assert found == [
        "main.s:11: error: the stack is not balanced when code called in Foo returns"
    ]


def test_falling_into_a_routine_that_does_not_return(lint):
    source = routine("\tmove.l\td2,-(sp)\n\tmoveq\t#3,d0\n", "d0") + FATAL
    assert lint(source, {"select": ["R001", "R006"]}) == []


def test_an_interrupt_handler_whose_header_has_no_clobbers(lint):
    source = ";--\n; Irq\n; In: -\n; Out: -\nIrq:\trte\n"
    assert lint(source) == ["main.s:1: error: header of Irq has no Clobbers field"]


def test_ignore_in_a_header_holds_for_the_style_rules(lint):
    source = routine("\tmove\td0,d1\n\trts\n", "d1", extra="; lint: ignore T003\n")
    assert lint(source, {"select": ["T003"]}) == []


def test_ignore_holds_on_lines_that_are_not_instructions(lint):
    fields = (
        "\trsreset\nt_a\trs.b\t1\nt_b\trs.w\t1\t; lint: ignore T002\nt_c\trs.b\t1\n"
    )
    fields += "\t; lint: ignore T002\nt_d\trs.w\t1\nt_e\trs.b\t1\nt_f\trs.w\t1\n"
    found = lint(fields, {"select": ["T002"]})
    assert [finding.split(":")[1] for finding in found] == ["8"]
    body = "\tmoveq\t#0,d0\n\tdc.w\t0\t; lint: ignore F003\n"
    assert lint(routine(body, "d0"), {"select": ["F003"]}) == []


def test_a_remark_may_follow_inline_and_noreturn(lint):
    body = '\tbsr\tSay\t; lint: inline  the text follows\n\tdc.b\t"hello",0\n\teven\n'
    body += "\trte\t; lint: noreturn - into the other task\n"
    say = ";--\n; Say\n; In: -\n; Out: -\n; Clobbers: -\nSay:\trts\n"
    assert lint(routine(body) + say) == []


LATIN = (
    b";--\r\n; Foo\r\n; In:       -\r\n; Out:      -\r\n; Clobbers: d5\r\n"
    b"Foo:\tmoveq\t#0,d0\t; r\xe6kke\r\n\trts\r\nName:\tdc.b\t'\xe6',0\r\n"
)


def test_fix_keeps_bytes_that_are_not_utf_8_and_the_line_endings(tmp_path: Path):
    path = tmp_path / "latin.s"
    path.write_bytes(LATIN)
    assert fix_files([path]) == {str(path): 1}
    assert path.read_bytes() == LATIN.replace(b"Clobbers: d5", b"Clobbers: d0")


def test_format_keeps_bytes_that_are_not_utf_8_and_the_line_endings(tmp_path: Path):
    path = tmp_path / "latin.s"
    path.write_bytes(LATIN.replace(b"Foo:\tmoveq\t#0,d0\t;", b"Foo:  MOVEQ #0,D0 ;"))
    assert format_files([path])[0] == [str(path)]
    after = path.read_bytes()
    assert b"Foo:\tmoveq\t#0,d0\t\t\t\t; r\xe6kke\r\n" in after
    assert b"'\xe6',0\r\n" in after
    assert after.count(b"\r\n") == LATIN.count(b"\r\n")
    assert lint_files([path], select=["S"]) == []
