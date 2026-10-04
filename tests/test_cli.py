import pytest

from qqroll import __version__
from qqroll.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"qqroll {__version__}"


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage: qqroll" in capsys.readouterr().out
