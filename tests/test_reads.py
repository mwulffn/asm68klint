"""Registers read while they hold nothing of use."""

import pytest

from asm68klint.m68k import read_registers
from asm68klint.source import parse_statement

READS = {"select": ["R007", "R008", "R009", "R010"]}

HELPER = """
;--
; Helper
; In:       d0 = value
; Out:      d0 = result
; Clobbers: d1/a0
Helper:
	move.l	d0,d1
	move.l	d1,a0
	moveq	#0,d0
	rts
"""


def routine(body: str, takes: str = "-", gives: str = "-", clobbers: str = "-") -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    return (
        f";--\n; Foo\n; In:       {takes}\n; Out:      {gives}\n"
        f"; Clobbers: {clobbers}\nFoo:\n{body}"
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("move.w d0,d1", {"d0"}),
        ("add.w d0,d1", {"d0", "d1"}),
        ("moveq #0,d0", set()),
        ("clr.l d3", set()),
        ("st d3", set()),
        ("lea 4(a0,d1.w),a2", {"a0", "d1"}),
        ("move.l d0,(a1)+", {"d0", "a1"}),
        ("move.b -(a2),d0", {"a2"}),
        ("tst.w d4", {"d4"}),
        ("cmp.w d1,d2", {"d1", "d2"}),
        ("sub.l a1,a1", set()),
        ("eor.w d2,d2", set()),
        ("sub.w d1,d2", {"d1", "d2"}),
        ("movem.l d2-d3/a2,-(sp)", {"d2", "d3", "a2", "a7"}),
        ("movem.l (sp)+,d2-d3/a2", {"a7"}),
        ("dbra d7,.Loop", {"d7"}),
        ("exg d0,a0", {"d0", "a0"}),
        ("swap d0", {"d0"}),
        ("jsr (a3)", {"a3"}),
        ("btst #3,d0", {"d0"}),
        ("move.w Table(pc,d0.w),d1", {"d0"}),
        ("pea (a0)", {"a0"}),
        ("unlk a4", {"a4"}),
        ("move.w #sp_size,d0", set()),
    ],
)
def test_what_an_instruction_reads(line, expected):
    assert read_registers(parse_statement("x.s", 1, f"\t{line}")) == expected


def test_reading_what_a_call_clobbered(lint):
    body = """\
	moveq	#3,d1
	bsr	Helper
	add.w	d1,d0
	rts
"""
    source = routine(body, takes="d0 = n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == [
        "main.s:9: warning: d1 is read after the call to Helper clobbered it"
    ]


def test_a_write_after_the_call_makes_the_register_good_again(lint):
    body = """\
	bsr	Helper
	moveq	#3,d1
	add.w	d1,d0
	rts
"""
    source = routine(body, takes="d0 = n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == []


def test_only_the_path_through_the_call_counts(lint):
    body = """\
	moveq	#3,d1
	tst.w	d0
	beq	.Skip
	bsr	Helper
.Skip	move.w	d1,(a1)
	rts
"""
    source = routine(body, takes="d0 = n, a1 = where", clobbers="d0-d1/a0") + HELPER
    assert lint(source, READS) == [
        "main.s:11: warning: d1 is read after the call to Helper clobbered it"
    ]


def test_only_the_first_read_is_reported(lint):
    body = """\
	bsr	Helper
	move.w	d1,(a1)
	move.w	d1,2(a1)
	rts
"""
    source = routine(body, takes="d0 = n, a1 = where", clobbers="d0-d1/a0") + HELPER
    assert len(lint(source, READS)) == 1


def test_a_result_of_the_call_is_not_lost(lint):
    body = """\
	bsr	Helper
	move.w	d0,(a1)
	rts
"""
    source = routine(body, takes="d0 = n, a1 = where", clobbers="d0-d1/a0") + HELPER
    assert lint(source, READS) == []


def test_a_register_saved_around_the_call_is_not_lost(lint):
    body = """\
	moveq	#3,d1
	move.l	d1,-(sp)
	bsr	Helper
	move.l	(sp)+,d1
	add.w	d1,d0
	rts
"""
    source = routine(body, takes="d0 = n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == []


def test_an_annotated_call_loses_nothing(lint):
    body = """\
	jsr	-198(a6)		; lint: clobbers d0-d1/a0-a1
	move.l	d0,(a2)
	rts
"""
    source = routine(body, takes="a2 = where", clobbers="d0-d1/a0-a1")
    assert lint(source, READS) == []


def test_a_conditional_jump_to_another_routine_loses_nothing_here(lint):
    body = """\
	moveq	#3,d1
	tst.w	d0
	beq	Helper
	move.w	d1,d0
	rts
"""
    source = routine(body, takes="d0 = n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == []


def test_passing_a_lost_register_to_a_call(lint):
    body = """\
	bsr	Helper
	move.w	d1,d0
	bsr	Helper
	rts
"""
    source = routine(body, takes="d0 = n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == [
        "main.s:8: warning: d1 is read after the call to Helper clobbered it"
    ]


def test_returning_what_a_call_clobbered(lint):
    body = """\
	move.w	d0,d1
	bsr	Helper
	rts
"""
    source = routine(body, takes="d0 = n", gives="d1 = n", clobbers="d0/a0") + HELPER
    assert lint(source, READS) == [
        "main.s:9: warning: d1 is returned after the call to Helper clobbered it"
    ]
    tail = "\tmove.w\td0,d1\n\tbra\tHelper\n"
    source = routine(tail, takes="d0 = n", gives="d1 = n", clobbers="d0/a0") + HELPER
    assert lint(source, READS) == [
        "main.s:8: warning: d1 is returned by Foo after the jump to Helper clobbers it"
    ]


def test_reading_a_register_that_is_not_an_input(lint):
    body = """\
	add.w	d1,d0
	rts
"""
    assert lint(routine(body, takes="d0 = n", gives="d0 = sum"), READS) == [
        "main.s:7: warning: d1 is read but not listed under In of Foo"
    ]
    assert lint(routine(body, takes="d0/d1 = n", gives="d0 = sum"), READS) == []


def test_a_call_needs_its_inputs(lint):
    source = routine("\tbra\tHelper\n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == [
        (
            "main.s:7: warning: d0 is needed by the jump to Helper but not listed"
            " under In of Foo"
        )
    ]


def test_the_last_instruction_before_falling_through_may_set_the_input(lint):
    source = routine("\tmoveq\t#1,d0\n", gives="d0 = n", clobbers="d1/a0") + HELPER
    assert lint(source, READS) == []


def test_saving_a_register_is_not_reading_it(lint):
    body = """\
	movem.l	d2/a2,-(sp)
	move.l	d3,-(sp)
	link	a4,#-8
	moveq	#0,d2
	unlk	a4
	move.l	(sp)+,d3
	movem.l	(sp)+,d2/a2
	rts
"""
    assert lint(routine(body), READS) == []


def test_reserved_registers_always_hold_something(lint):
    body = """\
	move.w	4(a5),d0
	move.w	d0,2(a6)
	rts
"""
    assert lint(routine(body, clobbers="d0"), READS) == []
    assert lint(routine(body, clobbers="d0"), {**READS, "reserved": []}) == [
        "main.s:7: warning: a5 is read but not listed under In of Foo",
        "main.s:8: warning: a6 is read but not listed under In of Foo",
    ]


def test_an_out_register_that_a_path_does_not_set(lint):
    body = """\
	tst.w	d0
	beq	.Done
	moveq	#1,d1
.Done	rts
"""
    assert lint(routine(body, takes="d0 = n", gives="d1 = flag"), READS) == [
        "main.s:10: warning: Out register d1 of Foo is not set on every path"
    ]


def test_an_out_register_also_under_clobbers_need_not_be_set(lint):
    body = """\
	tst.w	d0
	beq	.Done
	moveq	#1,d1
.Done	rts
"""
    source = routine(body, takes="d0 = n", gives="Z = no, else d1 = n", clobbers="d1")
    assert lint(source, READS) == []


def test_an_input_that_is_never_read(lint):
    body = """\
	moveq	#0,d0
	rts
"""
    assert lint(routine(body, takes="d0 = n, d1 = m", gives="d0 = 0"), READS) == [
        "main.s:3: warning: d0 is listed under In of Foo but is never read",
        "main.s:3: warning: d1 is listed under In of Foo but is never read",
    ]


def test_an_input_passed_on_to_a_call_is_read(lint):
    source = routine("\tbra\tHelper\n", "d0 = n", "d0 = n", "d1/a0") + HELPER
    assert lint(source, READS) == []


def test_a_reserved_register_under_in_need_not_be_read(lint):
    body = "\tmoveq\t#0,d0\n\trts\n"
    assert lint(routine(body, takes="a5 = state", clobbers="d0"), READS) == []


def test_every_configuration_is_checked(lint):
    body = """\
	ifd	DEBUG
	moveq	#0,d1
	endc
	add.w	d1,d0
	rts
"""
    source = routine(body, takes="d0 = n", gives="d0 = n", clobbers="d1")
    assert lint(source, READS) == [
        "main.s:10: warning: d1 is read but not listed under In of Foo"
    ]
