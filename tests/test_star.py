"""The one-time star line: only in a terminal, only once, never when switched off."""
import io

from readanydoc import star


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_shown_once_in_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("READANYDOC_NO_STAR", raising=False)
    monkeypatch.delenv("CI", raising=False)
    out = Tty()
    assert star.maybe_ask(out) is True and "github.com/arpitpatel25/ReadAnyDoc" in out.getvalue()
    assert star.maybe_ask(Tty()) is False


def test_never_in_pipes_ci_or_when_off(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("CI", raising=False)
    assert star.maybe_ask(io.StringIO()) is False          # not a terminal
    monkeypatch.setenv("READANYDOC_NO_STAR", "1")
    assert star.maybe_ask(Tty()) is False
    monkeypatch.delenv("READANYDOC_NO_STAR")
    monkeypatch.setenv("CI", "true")
    assert star.maybe_ask(Tty()) is False
    assert not star.marker().exists()
