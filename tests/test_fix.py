"""Writing headers, and output as JSON."""

import json
from pathlib import Path

import pytest

from asm68klint import lint_files
from asm68klint.cli import main
from asm68klint.fix import fix_files

WRONG = """\
;--
; Foo
; In:       d0 = n
; Out:      d0 = twice n
; Clobbers: d5
Foo:
	move.w	d0,d1
	add.w	d1,d0
	bsr	Bar
	rts

Bar:	moveq	#0,d2
	move.w	d1,(a1)
	rts
"""


@pytest.fixture
def source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put the source with a wrong header and a missing one in a file."""
    monkeypatch.chdir(tmp_path)
    path = Path("main.s")
    path.write_text(WRONG)
    return path


def test_a_wrong_clobbers_field_is_rewritten_and_a_header_added(source):
    assert fix_files([source], infer=True) == {"main.s": 2}
    assert source.read_text() == WRONG.replace(
        "Clobbers: d5", "Clobbers: d1-d2"
    ).replace(
        "Bar:\tmoveq",
        ";--\n; Bar\n; In:       d1/a1\n; Out:      -\n; Clobbers: d2\nBar:\tmoveq",
    )
    found = lint_files([source], select=["H", "R00", "F", "S"])
    assert [finding.code for finding in found] == ["R008"]  # a1, which Foo passes on
    assert fix_files([source], infer=True) == {}


def test_without_infer_only_headers_that_are_there_are_touched(source):
    assert fix_files([source]) == {}  # the call of Bar cannot be followed
    source.write_text(WRONG.replace("\tbsr\tBar\n", ""))
    assert fix_files([source]) == {"main.s": 1}
    assert "; Clobbers: d1\n" in source.read_text()
    assert "; Bar" not in source.read_text()


def test_a_header_for_the_gnu_assembler_has_its_comment_mark(source):
    source.write_text("\t.text\n\t.globl\tbar\nbar:\tmoveq\t#0,%d2\n\trts\n")
    assert fix_files([source], infer=True, reserved=[]) == {"main.s": 1}
    text = source.read_text()
    assert "|--\n| bar\n| In:       -\n| Out:      -\n| Clobbers: d2\nbar:" in text
    assert lint_files([source], reserved=[]) == []


def test_the_command_and_json(source, capsys):
    assert main(["--json", "--infer", "main.s"]) == 1
    rows = json.loads(capsys.readouterr().out)
    assert {row["code"] for row in rows} >= {"R001", "R002"}
    assert set(rows[0]) == {"file", "line", "code", "message", "severity"}
    assert main(["--json", "--infer", "--effects", "main.s"]) == 0
    assert json.loads(capsys.readouterr().out)[1] == {
        "file": "main.s",
        "line": 12,
        "name": "Bar",
        "in": "d1/a1",
        "out": "-",
        "clobbers": "d2",
        "header": False,
    }
    assert main(["--json", "--infer", "--free", "main.s:13", "main.s"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["routine"] == "Bar"
    assert "d2" in answer["free"]
    assert "d1" in answer["in_use"]
    assert main(["--fix", "--infer", "main.s"]) == 0
    assert capsys.readouterr().out == "main.s: 2 headers written\n"
