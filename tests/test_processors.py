"""The instructions of the later processors and of the floating point unit."""

import pytest

from asm68klint.m68k import needs, read_registers, written_registers
from asm68klint.source import parse_statement


def routine(body: str, clobbers: str = "-") -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    return f";--\n; Foo\n; In:       -\n; Out:      -\n; Clobbers: {clobbers}\nFoo:\n{body}"


@pytest.mark.parametrize(
    ("line", "written", "read"),
    [
        ("movec d0,vbr", "", "d0"),
        ("movec vbr,a1", "a1", ""),
        ("moves.b (a0)+,d2", "d2 a0", "a0"),
        ("extb.l d3", "d3", "d3"),
        ("bfextu (a0){d1:8},d2", "d2", "a0 d1"),
        ("bfins d0,d1{4:8}", "d1", "d0 d1"),
        ("bfclr d4{0:3}", "d4", "d4"),
        ("bftst d4{0:3}", "", "d4"),
        ("divul.l d0,d1:d2", "d1 d2", "d0 d1 d2"),
        ("mulu.l (a0),d3:d4", "d3 d4", "a0 d3 d4"),
        ("cas d0,d1,(a0)", "d0", "d0 d1 a0"),
        ("pack d0,d1,#3", "d1", "d0 d1"),
        ("move.w (a0,d1.w*4),d0", "d0", "a0 d1"),
        ("move.l ([8,a2],d3.l*2,4),d0", "d0", "a2 d3"),
        ("move16 (a0)+,(a1)+", "a0 a1", "a0 a1"),
        ("fmove.x fp0,fp1", "fp1", "fp0"),
        ("fadd.x fp2,fp1", "fp1", "fp1 fp2"),
        ("fmove.l fp0,d0", "d0", "fp0"),
        ("fmove.w d1,fp3", "fp3", "d1"),
        ("fsqrt.x fp4", "fp4", "fp4"),
        ("ftst.l d0", "", "d0"),
        ("fcmp.x fp0,fp1", "", "fp0 fp1"),
        ("fmovem.x fp2-fp4,-(sp)", "", "fp2 fp3 fp4 a7"),
        ("fmovem.x (sp)+,fp2-fp4", "fp2 fp3 fp4", "a7"),
        ("fmove.l d0,fpcr", "", "d0"),
        ("fsincos.x fp0,fp1:fp2", "fp1 fp2", "fp0 fp1 fp2"),
        ("fdbne d5,.Loop", "d5", "d5"),
        ("fsne d6", "d6", ""),
    ],
)
def test_what_the_later_instructions_write_and_read(line, written, read):
    statement = parse_statement("x.s", 1, f"\t{line}")
    assert written_registers(statement) == set(written.split())
    assert read_registers(statement) == set(read.split())


def test_what_each_processor_has():
    assert needs("move", "68000", False) is None
    assert needs("movec", "68000", False) == "a 68010"
    assert needs("movec", "68010", False) is None
    assert needs("bfextu", "68010", False) == "a 68020"
    assert needs("pmove", "68020", False) == "a 68030"
    assert needs("move16", "68030", False) == "a 68040"
    assert needs("plpar", "68040", False) == "a 68060"
    assert needs("plpar", "68060", False) is None
    assert needs("fadd", "68030", False) == "a floating point unit"
    assert needs("fadd", "68030", True) is None
    assert needs("fbne", "68040", False) is None


def test_an_instruction_the_chosen_processor_has_not(lint):
    source = routine("\textb.l\td0\n\tfmove.x\tfp0,fp1\n\trts\n", "d0/fp1")
    assert lint(source) == []
    assert lint(source, {"cpu": "68020", "fpu": True}) == []
    assert lint(source, {"cpu": "68000"}) == [
        "main.s:7: error: extb needs a 68020, and this is for a 68000",
        "main.s:8: error: fmove needs a floating point unit, and this is for a 68000",
    ]


def test_floating_point_registers_are_followed_like_the_others(lint):
    body = """\
	fmovem.x	fp2-fp3,-(sp)
	fmove.x	fp4,-(sp)
	fmove.w	d0,fp2
	fmul.x	fp2,fp3
	fmove.x	fp3,fp4
	fmove.x	fp4,fp0
	fmove.x	(sp)+,fp4
	fmovem.x	(sp)+,fp2-fp3
	rts
"""
    assert lint(routine(body, "fp0")) == []
    assert lint(routine(body)) == [
        "main.s:12: error: fp0 is written but not listed under Out or Clobbers of Foo"
    ]


def test_a_floating_point_register_saved_in_part_is_not_preserved(lint):
    body = """\
	fmove.d	fp2,-(sp)
	fmove.w	d0,fp2
	fmove.d	(sp)+,fp2
	rts
"""
    assert lint(routine(body)) == [
        "main.s:8: error: fp2 is written but not listed under Out or Clobbers of Foo"
    ]


def test_the_frame_of_fsave_comes_off_with_frestore(lint):
    body = """\
	fsave	-(sp)
	fmovem.x	fp0-fp7,-(sp)
	fmovem.x	(sp)+,fp0-fp7
	frestore	(sp)+
	rte
"""
    assert lint(routine(body)) == []
    assert lint(routine(body.replace("\tfrestore\t(sp)+\n", ""))) == [
        "main.s:10: error: the stack is not balanced when Foo returns here"
    ]


def test_rtd_returns(lint):
    assert lint(routine("\tmoveq\t#0,d0\n\trtd\t#8\n", "d0")) == []


def test_the_size_may_be_written_without_its_dot(lint):
    body = "\tmovel\t#1,d0\n\taddqw\t#1,d1\n\tfmovex\tfp0,fp1\n\trts\n"
    assert lint(routine(body, "d0-d1/fp1")) == []
