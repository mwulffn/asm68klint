"""The annotations out, noreturn and ignore."""

from asm68klint.linter import free_registers

READS = {"select": ["R007"], "reserved": []}


def routine(body: str, clobbers: str = "-", extra: str = "", takes: str = "-") -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    header = f";--\n; Foo\n; In:       {takes}\n; Out:      -\n; Clobbers: {clobbers}\n"
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
