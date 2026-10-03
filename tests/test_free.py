"""Which registers new code at a line may use."""

from pathlib import Path

import pytest

from asm68klint.cli import main
from asm68klint.linter import free_registers
from asm68klint.registers import format_list

SOURCE = """\
;--
; Foo
; In:       d0 = count, a0 = where
; Out:      d0 = sum
; Clobbers: d1-d2/a0
Foo:
	movem.l	d3/a2,-(sp)
	moveq	#0,d1
	move.l	a0,a2
.Loop	add.w	(a2)+,d1
	bsr	Helper
	subq.w	#1,d0
	bne	.Loop
	move.w	d1,d0
	movem.l	(sp)+,d3/a2
	rts

;--
; Helper
; In:       d1 = value
; Out:      -
; Clobbers: d2
Helper:
	move.w	d1,d2
	rts
"""


@pytest.fixture
def free(tmp_path: Path):
    """Return a function that says what is free and in use at a line of SOURCE."""
    path = tmp_path / "main.s"
    path.write_text(SOURCE)

    def at(line: int, **settings) -> tuple[str, str, str] | None:
        found = free_registers([path], path, line, **settings)
        return found and (found[0], format_list(found[1]), format_list(found[2]))

    return at


def test_before_anything_is_saved_only_what_may_be_changed_is_free(free):
    assert free(7) == ("Foo", "d1-d2", "d0/d3-d7/a0-a7")
    assert free(2) == ("Foo", "d1-d2", "d0/d3-d7/a0-a7")


def test_a_saved_register_is_free_until_it_is_restored(free):
    assert free(8) == ("Foo", "d1-d3/a2", "d0/d4-d7/a0-a1/a3-a7")
    assert free(16) == ("Foo", "d1-d2/a0", "d0/d3-d7/a1-a7")


def test_a_value_that_is_read_later_is_in_use(free):
    assert free(11) == ("Foo", "d2-d3/a0", "d0-d1/d4-d7/a1-a7")
    assert free(15) == ("Foo", "d1-d3/a0/a2", "d0/d4-d7/a1/a3-a7")


def test_reserved_registers_are_given_as_wanted(free):
    assert free(24, reserved=[]) == ("Helper", "d2", "d0-d1/d3-d7/a0-a7")


def test_a_line_outside_every_routine(free):
    assert free(17) is None
    assert free(99) is None


def test_the_command(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("main.s").write_text(SOURCE)
    assert main(["--free", "main.s:11", "main.s"]) == 0
    assert capsys.readouterr().out == (
        "main.s:11: in Foo: free d2-d3/a0; in use d0-d1/d4-d7/a1-a7\n"
    )
    assert main(["--free", "main.s:17", "main.s"]) == 2
    assert main(["--free", "main.s", "main.s"]) == 2
