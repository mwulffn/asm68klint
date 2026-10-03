"""The annotations out, noreturn and ignore."""

READS = {"select": ["R007"], "reserved": []}


def routine(body: str, clobbers: str = "-", extra: str = "") -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    header = f";--\n; Foo\n; In:       -\n; Out:      -\n; Clobbers: {clobbers}\n"
    return header + extra + f"Foo:\n{body}"


def test_out_says_which_register_of_an_annotated_call_is_its_result(lint):
    body = """\
	jsr	(a2)			; lint: clobbers d0-d1/a0
	move.l	d0,(a3)
	move.l	a0,(a3)
	rts
"""
    assert lint(routine(body, "d0-d1/a0"), READS) == []
    with_out = routine("\t; lint: out d0\n" + body, "d0-d1/a0")
    assert lint(with_out, READS) == [
        "main.s:10: warning: a0 is read after the indirect call jsr (a2) clobbered it"
    ]


def test_out_adds_its_registers_to_what_the_call_changes(lint):
    body = "\t; lint: out d0\n\tjsr\t(a2)\t; lint: clobbers d1\n\trts\n"
    assert lint(routine(body, "d1")) == [
        (
            "main.s:8: error: d0 is clobbered by the indirect call jsr (a2) but not"
            " listed under Out or Clobbers of Foo"
        )
    ]


def test_out_without_clobbers_is_an_error(lint):
    body = "\tjsr\tBar\t; lint: out d0\n\trts\n"
    other = ";--\n; Bar\n; In: -\n; Out: -\n; Clobbers: -\nBar:\trts\n"
    found = lint(routine(body) + other)
    assert len(found) == 1
    assert "there is no lint: clobbers here" in found[0]


def test_an_instruction_execution_does_not_come_back_from(lint):
    body = """\
	movem.l	d2-d3,-(sp)
	tst.w	d0
	beq	.Fatal
	movem.l	(sp)+,d2-d3
	rts
.Fatal	move.l	4.w,sp
	moveq	#0,d5
	jmp	$f80002			; lint: noreturn
"""
    assert lint(routine(body)) == []
    plain = lint(routine(body.replace("; lint: noreturn", "")))
    assert any("cannot analyse" in finding for finding in plain)


def test_a_return_into_another_task(lint):
    body = """\
	move.l	NextStack,sp
	movem.l	(sp)+,d0-d7/a0-a6
	rte				; lint: noreturn
"""
    free = {"reserved": []}
    assert lint(routine(body), free) == []
    assert len(lint(routine(body.replace("; lint: noreturn", "")), free)) > 5


def test_a_conditional_branch_that_does_not_come_back(lint):
    body = """\
	tst.w	d0
	bmi	Elsewhere		; lint: noreturn
	moveq	#0,d1
	rts
"""
    assert lint(routine(body, "d1")) == []


NEVER = """
;--
; Fatal
; In:       -
; Out:      -
; Clobbers: -
; lint: noreturn
Fatal:
	move.l	4.w,sp
	moveq	#0,d5
.Loop	bra	.Loop
"""


def test_a_routine_that_does_not_return(lint):
    body = """\
	movem.l	d2-d3,-(sp)
	tst.w	d0
	beq	Fatal
	bpl	.Fine
	bsr	Fatal
	moveq	#0,d6
.Fine	movem.l	(sp)+,d2-d3
	rts
"""
    assert lint(routine(body) + NEVER) == [
        "main.s:12: warning: unreachable code in Foo is not checked"
    ]
    returning = NEVER.replace("; lint: noreturn\n", "")
    assert len(lint(routine(body) + returning)) >= 2


def test_ignore_on_a_line(lint):
    body = """\
	moveq	#0,d0			; lint: ignore R001
	; lint: ignore R
	moveq	#0,d1
	moveq	#0,d2
	rts
"""
    assert lint(routine(body)) == [
        "main.s:10: error: d2 is written but not listed under Out or Clobbers of Foo"
    ]


def test_ignore_in_a_header_holds_for_the_routine(lint):
    body = "\tmoveq\t#0,d0\n\tmove.l\td1,-(sp)\n\trts\n"
    assert len(lint(routine(body))) == 2
    assert lint(routine(body, extra="; lint: ignore R001, R006\n")) == []
    one = lint(routine(body, extra="; lint: ignore R001\n"))
    assert one == ["main.s:10: error: the stack is not balanced when Foo returns here"]


def test_ignore_wants_rules_that_are_there(lint):
    found = lint(routine("\trts\t; lint: ignore X9\n"))
    assert found == ["main.s:7: error: cannot parse the lint annotation 'ignore X9'"]


def test_ignore_works_for_the_style_rules(lint):
    body = "\tmove\td0,d1\t; lint: ignore T003\n\tmove\td0,d1\n\trts\n"
    found = lint(routine(body, "d1"), {"select": ["T003"]})
    assert found == ["main.s:8: warning: move has no size: write move.w"]


def test_only_some_annotations_may_stand_in_a_header(lint):
    found = lint(routine("\trts\n", extra="; lint: out d0\n"))
    assert found == [
        "main.s:6: error: the lint annotation 'out' cannot be used in a header"
    ]
