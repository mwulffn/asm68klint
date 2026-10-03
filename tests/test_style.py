"""The style rules, which are off unless selected."""

import pytest

from asm68klint.style import write_only
from asm68klint.values import evaluate, scan

STYLE = {"select": ["T"], "platform": "amiga", "reserved": []}


def routine(body: str) -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    return f";--\n; Foo\n; In:       -\n; Out:      -\n; Clobbers: -\nFoo:\n{body}"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("12", 12),
        ("$1f+1", 32),
        ("%101", 5),
        ("0x10", 16),
        ("'0'", 48),
        ("2*SIZE+1", 9),
        ("(SIZE<<2)|1", 17),
        ("SIZE/3", 1),
        ("-SIZE", -4),
        ("~0&$ff", 255),
        ("1!2", 3),
        ("Unknown+1", None),
        ("SIZE+", None),
        ("1/0", None),
        ("", None),
    ],
)
def test_expressions(text, value):
    assert evaluate(text, {"SIZE": 4}) == value


def test_style_rules_are_off_unless_selected(lint):
    source = routine("\tmove\td0,d1\n\tbra.s\tFoo\n")
    assert lint(source, {"ignore": ["R"]}) == []
    assert lint(source, {"extend_select": ["T"], "ignore": ["R"]}) == [
        "main.s:7: warning: move has no size: write move.w",
        "main.s:8: warning: bra.s has a size: leave it to the assembler",
    ]


def test_instructions_that_need_no_size(lint):
    body = """\
	moveq	#0,d0
	lea	4(a0),a1
	bset	#1,d0
	st	d1
	swap	d0
	exg	d0,d1
	mulu	d1,d0
	bne	Foo
	dbra	d0,Foo
	rts
"""
    assert lint(routine(body), STYLE) == []


def test_reading_a_register_that_can_only_be_written(lint):
    body = """\
	clr.w	bltcon1(a6)
	move.w	#0,bltcon1(a6)
	move.w	dmacon(a6),d0
	move.w	dmaconr(a6),d0
	bset	#7,intena+1(a6)
	tst.w	$dff096
	move.w	$dff002,d0
	lea	color00(a6),a0
	move.w	d0,color00(a6)
	move.w	#bltsize,d0
	rts
"""
    assert lint(routine(body), STYLE) == [
        (
            "main.s:7: error: clr reads bltcon1 before it writes it, and it can only"
            " be written: write move #0"
        ),
        "main.s:9: error: move reads dmacon, which can only be written",
        "main.s:11: error: bset reads intena, which can only be written",
        "main.s:12: error: tst reads $dff096, which can only be written",
    ]
    assert lint(routine(body), {**STYLE, "platform": None}) == []
    later = lint(routine(body), {**STYLE, "cpu": "68020"})
    assert not any("clr reads" in finding for finding in later)


def test_write_only_names():
    assert write_only("bltsize(a6)") == "bltsize"
    assert write_only("_custom+color17") == "color17"
    assert write_only("vhposr(a6)") is None
    assert write_only("bltsizeof(a6)") is None
    assert write_only("#dmacon") is None


def test_word_and_long_fields_at_odd_offsets(lint):
    source = """\
COUNT	equ	3
	rsreset
t_flag	rs.b	1
t_clock	rs.w	1
t_name	rs.b	COUNT
t_next	rs.l	1
t_pad	rs.b	1
t_more	rs.b	0
t_fine	rs.w	2
t_SIZEOF rs.b	0
	rsset	$101
u_bad	rs.w	1
"""
    assert lint(source, STYLE) == [
        (
            "main.s:4: error: t_clock is a word field at an odd offset (1): a 68000"
            " cannot read or write it"
        ),
        (
            "main.s:9: error: t_fine is a word field at an odd offset (11): a 68000"
            " cannot read or write it"
        ),
        (
            "main.s:12: error: u_bad is a word field at an odd offset (257): a 68000"
            " cannot read or write it"
        ),
    ]


def test_a_field_is_not_reported_when_its_place_is_not_known(lint):
    source = """\
	rsreset
a_one	rs.b	1
	if	WIDE
a_two	rs.b	1
	endc
a_word	rs.w	1
	rsreset
b_many	rs.b	Unknown
b_word	rs.w	1
	rsreset
c_one	rs.b	1
	if	WIDE
c_two	rs.b	2
	else
c_two	rs.b	4
	endc
c_word	rs.w	1
"""
    assert lint(source, STYLE) == [
        (
            "main.s:17: error: c_word is a word field at an odd offset: a 68000"
            " cannot read or write it"
        )
    ]
    found = lint(source, {**STYLE, "define": ["WIDE=0"]})
    assert [finding.split(":")[1] for finding in found] == ["6", "17"]
    assert "(5)" in found[1]


def test_fields_from_a_macro_and_an_include(lint):
    source = '\tinclude\t"layout.i"\n\trsreset\nx_a\trs.b\t1\n\tWORD\tx_b\n'
    layout = (
        "WORD\tmacro\n\\1\trs.w\t1\n\tendm\n\trsreset\ny_a\trs.b\t3\ny_b\trs.l\t1\n"
    )
    assert lint(source, STYLE, layout_i=layout) == [
        (
            "layout.i:6: error: y_b is a long field at an odd offset (3): a 68000"
            " cannot read or write it"
        ),
        (
            "main.s:4: error: x_b is a word field at an odd offset (1): a 68000"
            " cannot read or write it (in macro WORD)"
        ),
    ]


def test_values_of_names():
    from asm68klint.source import parse_statement

    lines = [
        "A equ 2",
        "B = A*3",
        " rsreset",
        "f_a rs.w A",
        "f_b rs.b 1",
        "C set 1",
        "C set 2",
    ]
    values = scan([parse_statement("x.s", n, line) for n, line in enumerate(lines)], {})
    assert values.symbols == {"A": 2, "B": 6, "f_a": 0, "f_b": 4, "C": None}


def test_a_displacement_too_large_for_an_index(lint):
    body = """\
	move.b	t_near(a5,d0.w),d1
	move.b	t_far(a5,d0.w),d1
	move.b	(t_far,a5,d0.w),d1
	move.b	t_far(a5),d1
	move.b	-129(a0,d1.l),d2
	move.b	Unknown(a5,d0.w),d1
	move.b	Table(pc,d0.w),d1
	rts
"""
    source = "t_near\tequ\t127\nt_far\tequ\t128\n" + routine(body)
    found = lint(source, {**STYLE, "ignore": ["T003"]})
    assert found == [
        (
            "main.s:10: error: the displacement t_far is 128: with an index register"
            " it must be -128 to 127"
        ),
        (
            "main.s:11: error: the displacement t_far is 128: with an index register"
            " it must be -128 to 127"
        ),
        (
            "main.s:13: error: the displacement -129 is -129: with an index register"
            " it must be -128 to 127"
        ),
    ]
    assert lint(source, {**STYLE, "cpu": "68020"}) == []


def test_imports_and_exports(lint):
    main = """\
	xref	Used,Unused
	xdef	Foo,Lonely
	bsr	Used
"""
    other = "\txref\tFoo\n\tbsr\tFoo\n"
    options = {"select": ["T006", "T007"]}
    assert lint(main, options, other_s=other) == [
        "main.s:1: warning: Unused is imported and not used",
        "main.s:2: warning: Lonely is exported and no other file given uses it",
    ]
    assert lint(main, options) == ["main.s:1: warning: Unused is imported and not used"]


def test_a_default_is_taken_unless_the_name_is_given(lint):
    source = """\
	ifnd	SIZE
SIZE	equ	3
	endc
	rsreset
d_name	rs.b	SIZE
d_word	rs.w	1
	ifd	EXTRA
d_more	rs.b	1
	endc
d_long	rs.l	1
"""
    assert [line.split(":")[1] for line in lint(source, STYLE)] == ["6", "10"]
    assert lint(source, {**STYLE, "define": ["SIZE=4"]}) == []
    with_extra = lint(source, {**STYLE, "define": ["EXTRA"]})
    assert [line.split(":")[1] for line in with_extra] == ["6"]
