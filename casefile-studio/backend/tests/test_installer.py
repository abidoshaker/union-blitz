"""Static checks on the Windows batch scripts.

There is no Windows here to run them on, so these guard the failure modes that
are invisible until a user double-clicks the file: a batch script that dies
mid-way takes the console window with it, and you never learn why.

The bug these were written for: `refresh_path` prepended the registry PATH onto
the process PATH on every call, so three calls quadrupled it, and a PATH over
about 2,000 characters blew past cmd's 8,191-character variable limit at step
4 of 9.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BATCH_FILES = sorted(ROOT.glob("*.bat"))

# cmd.exe refuses a variable longer than this, and the batch dies on the spot.
CMD_VARIABLE_LIMIT = 8191


def text_of(path: Path) -> str:
    """Read without newline translation - CRLF is the thing under test."""
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def read(path: Path) -> list[str]:
    return text_of(path).split("\r\n")


@pytest.fixture(scope="module")
def installer() -> Path:
    path = ROOT / "install-deps.bat"
    if not path.exists():
        pytest.skip("installer not present")
    return path


@pytest.mark.parametrize("batch", BATCH_FILES, ids=lambda p: p.name)
def test_batch_files_keep_crlf_endings(batch: Path):
    """cmd mis-parses labels and blocks in an LF-only .bat."""
    raw = batch.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n"), f"{batch.name} has bare LF lines"


@pytest.mark.parametrize("batch", BATCH_FILES, ids=lambda p: p.name)
def test_no_bare_exit_closes_the_console(batch: Path):
    """`exit` without /b ends the whole cmd session, window and all."""
    offenders = [
        n for n, line in enumerate(read(batch), 1)
        if re.match(r"^\s*exit\b", line, re.I) and not re.match(r"^\s*exit\s+/b", line, re.I)
    ]
    assert not offenders, f"{batch.name}: bare exit on line(s) {offenders}"


@pytest.mark.parametrize("batch", BATCH_FILES, ids=lambda p: p.name)
def test_every_label_that_is_called_exists(batch: Path):
    """A missing label aborts the batch, and a double-clicked window vanishes."""
    text = text_of(batch)
    defined = {
        line[1:].split()[0].lower()
        for line in read(batch)
        if line.startswith(":") and not line.startswith("::")
    }
    called = {m.group(1).lower() for m in re.finditer(r"\b(?:goto|call)\s+:(\w+)", text)}
    assert not (called - defined), f"{batch.name}: undefined label(s) {called - defined}"


@pytest.mark.parametrize("batch", BATCH_FILES, ids=lambda p: p.name)
def test_parentheses_balance(batch: Path):
    """cmd parses a parenthesised block whole; an unbalanced one derails it."""
    depth = 0
    for n, line in enumerate(read(batch), 1):
        stripped = line.strip()
        if stripped.lower().startswith("rem") or stripped.startswith("::"):
            continue
        code = re.sub(r'"[^"]*"', '""', stripped)      # parens inside quotes are text
        code = re.sub(r"\^[()]", "", code)             # and escaped ones are printed
        depth += code.count("(") - code.count(")")
        assert depth >= 0, f"{batch.name}: unmatched ')' at line {n}"
    assert depth == 0, f"{batch.name}: {depth} unclosed '(' at end of file"


def test_path_is_never_prepended_onto_itself(installer: Path):
    """The bug that closed the window at step 4 of 9.

    `set "PATH=%SOMETHING%;%PATH%"` inside a routine called once per install is
    unbounded growth: the registry values it reads are already in %PATH%, so
    every call adds the whole list again.
    """
    offenders = [
        (n, line.strip())
        for n, line in enumerate(read(installer), 1)
        if re.search(r'set\s+"?PATH=.*%PATH%', line, re.I)
    ]
    assert not offenders, (
        "install-deps.bat grows PATH by re-prepending it: " + repr(offenders)
    )


def test_the_path_merge_refuses_a_result_cmd_cannot_hold(installer: Path):
    """Refusing to set an over-long PATH is what keeps the script alive."""
    text = text_of(installer)
    caps = [int(m.group(1)) for m in re.finditer(r"Length\s+-lt\s+(\d+)", text)]
    assert caps, "the PATH merge has no length guard"
    assert all(cap < CMD_VARIABLE_LIMIT for cap in caps), (
        f"guard at {caps} is not under cmd's {CMD_VARIABLE_LIMIT}-character limit"
    )


def test_the_window_is_held_open_however_the_install_ends(installer: Path):
    """Whatever kills it next time, the user must get to read the screen."""
    text = text_of(installer)
    assert "CASEFILE_INSTALL_CHILD" in text, "no re-launch guard"
    assert re.search(r"cmd\s+/d\s+/s\s+/c", text), "the real work is not run in a child cmd"
    # Exactly one pause, in the parent: the child must not block behind it.
    pauses = [n for n, line in enumerate(read(installer), 1)
              if re.match(r"^\s*pause\s*$", line, re.I)]
    assert len(pauses) == 1, f"expected one pause in the parent, found {len(pauses)}"


def test_powershell_is_called_by_full_path(installer: Path):
    """PATH being in a bad state is the reason we are calling it at all."""
    text = text_of(installer)
    assert r"System32\WindowsPowerShell\v1.0\powershell.exe" in text
    bare = [n for n, line in enumerate(read(installer), 1)
            if re.search(r"(?<![\\\"%])\bpowershell\s+-", line, re.I)
            and "set \"PS=" not in line]
    assert not bare, f"bare powershell invocation on line(s) {bare}"
