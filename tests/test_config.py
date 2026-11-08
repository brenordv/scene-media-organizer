import argparse
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.config import ENV_FLAG_MAP, add_config_arguments, apply_config, parse_command_line, require_env


@pytest.fixture(autouse=True)
def _restore_environ(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """apply_config writes os.environ directly, which monkeypatch would not undo.
    Snapshot the whole environment and restore it so no test leaks into another."""
    saved = dict(os.environ)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_config_arguments(parser)
    return parser.parse_args(argv)


def _empty_env_file(tmp_path: Path) -> str:
    """A real but empty fallback file, so load_dotenv never falls through to the
    developer's own .env during the test run."""
    path = tmp_path / "fallback_env.txt"
    path.write_text("")
    return str(path)


def test_each_flag_writes_its_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = _empty_env_file(tmp_path)
    for dest, env_name in ENV_FLAG_MAP.items():
        monkeypatch.delenv(env_name, raising=False)
        flag = "--" + dest.replace("_", "-")
        value = f"value-for-{dest}"
        apply_config(_parse([flag, value, "--env-file", empty]))
        assert os.environ[env_name] == value


def test_flag_beats_preset_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = _empty_env_file(tmp_path)
    monkeypatch.setenv("WATCH_FOLDER", "from-env")
    apply_config(_parse(["--watch-folder", "from-flag", "--env-file", empty]))
    assert os.environ["WATCH_FOLDER"] == "from-flag"


def test_environment_beats_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "fallback_env.txt"
    env_file.write_text("WATCH_FOLDER=from-file\n")
    monkeypatch.setenv("WATCH_FOLDER", "from-env")
    apply_config(_parse(["--env-file", str(env_file)]))
    assert os.environ["WATCH_FOLDER"] == "from-env"


def test_env_file_fills_when_nothing_else_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "fallback_env.txt"
    env_file.write_text("WATCH_FOLDER=from-file\n")
    monkeypatch.delenv("WATCH_FOLDER", raising=False)
    apply_config(_parse(["--env-file", str(env_file)]))
    assert os.environ["WATCH_FOLDER"] == "from-file"


def test_require_env_exits_2_and_names_every_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SMO_TEST_A", raising=False)
    monkeypatch.delenv("SMO_TEST_B", raising=False)
    with pytest.raises(SystemExit) as exc:
        require_env("SMO_TEST_A", "SMO_TEST_B")
    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "SMO_TEST_A" in stderr
    assert "SMO_TEST_B" in stderr


def test_require_env_passes_when_all_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMO_TEST_A", "x")
    require_env("SMO_TEST_A")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py")
    add_config_arguments(parser)
    return parser


def test_parse_command_line_accepts_known_flags() -> None:
    args = parse_command_line(_parser(), ["--watch-folder", "/watch"])

    assert args.watch_folder == "/watch"


@pytest.mark.parametrize(
    "argv",
    [["--postgres-password", "hunter2"], ["--postgres-password=hunter2"], ["--telegram-bot-token", "hunter2"]],
)
def test_parse_command_line_removed_secret_flag_exits_2_without_echoing_the_value(
    capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_command_line(_parser(), argv)

    stderr = capsys.readouterr().err
    assert exc.value.code == 2
    assert "no longer a command-line flag" in stderr
    assert "hunter2" not in stderr


def test_parse_command_line_unknown_flag_exits_2_naming_it(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        parse_command_line(_parser(), ["--bogus", "value"])

    stderr = capsys.readouterr().err
    assert exc.value.code == 2
    assert "Unrecognized option: --bogus" in stderr
    assert "value" not in stderr.replace("Unrecognized arguments", "")
