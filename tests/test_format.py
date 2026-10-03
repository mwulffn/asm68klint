"""Laying source out in columns."""

import shutil
import subprocess
from pathlib import Path

import pytest

from asm68klint.cli import main
from asm68klint.format import format_line, format_text, operand_end, width

MESSY = """\
* A program written with little care for columns
SIZE     EQU   4
Mask = $ff
COUNT	set	0
        RSRESET
t_a  RS.B 1
t_b:	rs.b	SIZE-1		; the rest
VeryLongNameOfAField rs.w 1 ; a word

PUSH MACRO
  MOVE.L \\1,-(SP)
 ENDM

Start:  MOVEQ #0,D0            ; clear it
  MOVE.W  #SIZE,D1 set the count
 Loop: ADD.W (A0)+,D0
        DBRA D1,Loop ; round
	PUSH	D0
	lea	Txt(PC),A1 ; has a ';' in it? no: the text does
	cmp.b	#';',(a1)	;compare
	move.l	(A0,D1.W*1),d2
.Done	*	a comment after a label
	move.l (sp)+,D0
        RTS
Txt:	dc.b	"a ; b  D0",0	; quoted
   even
"""

TIDY = """\
* A program written with little care for columns
SIZE\t\tequ\t4
Mask\t\t=\t$ff
COUNT\tset\t0
\trsreset
t_a\t\trs.b\t1
t_b:\t\trs.b\tSIZE-1\t\t\t; the rest
VeryLongNameOfAField rs.w\t1\t\t; a word

PUSH\tmacro
\tmove.l\t\\1,-(sp)
\tendm

Start:\tmoveq\t#0,d0\t\t\t\t; clear it
\tmove.w\t#SIZE,d1\t\t\tset the count
Loop:\tadd.w\t(a0)+,d0
\tdbra\td1,Loop\t\t\t\t; round
\tPUSH\tD0
\tlea\tTxt(pc),a1\t\t\t; has a ';' in it? no: the text does
\tcmp.b\t#';',(a1)\t\t\t;compare
\tmove.l\t(a0,d1.w*1),d2
.Done\t*\ta comment after a label
\tmove.l\t(sp)+,d0
\trts
Txt:\tdc.b\t"a ; b  D0",0\t\t\t; quoted
\teven
"""


def test_a_messy_program_is_laid_out():
    assert format_text(MESSY) == TIDY


def test_laying_out_twice_changes_nothing_more():
    assert format_text(TIDY) == TIDY


@pytest.mark.skipif(not shutil.which("vasmm68k_mot"), reason="vasm is not installed")
def test_the_program_assembles_to_the_same_bytes(tmp_path: Path):
    outputs = []
    for name, text in (("messy", MESSY), ("tidy", TIDY)):
        source = tmp_path / f"{name}.s"
        source.write_text(text)
        output = tmp_path / f"{name}.bin"
        command = ["vasmm68k_mot", "-quiet", "-Fbin", "-m68020", "-o", str(output)]
        subprocess.run([*command, str(source)], check=True, capture_output=True)
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
    assert len(outputs[0]) > 20


@pytest.mark.parametrize(
    "line",
    [
        "",
        "; a comment",
        "\t; an indented comment",
        "* a starred comment",
        ";--",
        "; In:       d0 = n",
    ],
)
def test_comments_and_blank_lines_stay_as_they_are(line):
    assert format_line(line) == line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("  MOVE.L  D0,D1", "\tmove.l\td0,d1"),
        ("  Wait_Blit  D0", "\tWait_Blit\tD0"),
        ("\tmove.l\tSP_Save,d0", "\tmove.l\tSP_Save,d0"),
        ("\tmove.l\tD0Count(A5),d0", "\tmove.l\tD0Count(a5),d0"),
        ("\tdc.b\t'D0 ; x'", "\tdc.b\t'D0 ; x'"),
        ('\tINCLUDE\t"File.i"', '\tinclude\t"File.i"'),
        ("Label", "Label"),
        ("Label:   ; note", "Label:\t; note"),
        ("LongLabelName: move.w d0,d1", "LongLabelName:\tmove.w\td0,d1"),
        ("A_NAME_OF_SIXTEEN equ 1", "A_NAME_OF_SIXTEEN equ\t1"),
        ("\tmovem.l\tD0-D2/A0, -(SP)", "\tmovem.l\td0-d2/a0, -(sp)"),
        ("\tfmove.x\tFP0,FP1", "\tfmove.x\tfp0,fp1"),
        ("\trts trailing spaces   ", "\trts\t\t\t\t\ttrailing spaces"),
    ],
)
def test_lines(line, expected):
    assert format_line(line) == expected


def test_comments_of_lines_together_start_in_one_column():
    text = (
        "\tmoveq\t#0,d0 ; one\n"
        "\tmove.l\tSomething+Long(a5,d0.w),AnotherLongName(a4) ; two\n"
        "\trts ; three\n"
        "\n"
        "\trts ; alone\n"
        "\tmove.l\t#A+Very+Long+Expression+That+Goes+On+And+On+And+On+And+On+For+Ever,d0 ; far\n"
    )
    lines = format_text(text).splitlines()
    assert [width(line.split(";")[0]) for line in lines if line] == [64, 64, 64, 48, 88]
    assert lines[5].endswith(",d0\t; far")
    narrow = format_text(text, 32).splitlines()
    assert width(narrow[4].split(";")[0]) == 32


def test_where_operands_end():
    assert operand_end("\td0,d1 and words") == 6
    assert operand_end(" d0, d1 words") == 7
    assert operand_end(" 'a b',d0 x") == 9
    assert operand_end(" (a0,d1.w),d2 x") == 13
    assert operand_end(" (a0, d1.w),d2") == 5  # a blank ends them even in brackets
    assert operand_end(" <a b>,d0") == 9


def test_what_is_between_rem_and_erem_is_left():
    text = "\tREM\n  Any  text   D0\n\tEREM\n  NOP\n"
    assert format_text(text) == "\tREM\n  Any  text   D0\n\tEREM\n\tnop\n"


def test_source_for_the_gnu_assembler_is_passed_over():
    assert format_text("\t.text\n\t.globl _f\n_f:\tmove.l %d0,%d1\n") is None


def test_the_command(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("a.s").write_text(MESSY)
    Path("b.s").write_text(TIDY)
    Path("g.s").write_text("\t.text\n\t.globl _f\n_f:\tmove.l %d0,%d1\n")
    assert main(["--format", "--check", "a.s", "b.s", "g.s"]) == 1
    assert capsys.readouterr().out == (
        "g.s: not formatted: it is for the GNU assembler\na.s: would be formatted\n"
    )
    assert Path("a.s").read_text() == MESSY
    assert main(["--format", "--diff", "a.s"]) == 1
    assert "+SIZE\t\tequ\t4" in capsys.readouterr().out
    assert main(["--format", "a.s", "b.s"]) == 0
    assert capsys.readouterr().out == "a.s: formatted\n"
    assert Path("a.s").read_text() == TIDY
    assert main(["--format", "--check", "a.s", "b.s"]) == 0
    Path("asm68klint.toml").write_text("comment-column = 32\n")
    assert main(["--format", "--check", "a.s"]) == 1
    assert main(["--format", "--check", "--comment-column", "48", "a.s"]) == 0
