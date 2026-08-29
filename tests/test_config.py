import argparse
import os

import pytest

from src.config import ENV_FLAG_MAP, add_config_arguments, apply_config, require_env


@pytest.fixture(autouse=True)
def _restore_environ():
    """apply_config writes os.environ directly, which monkeypatch would not undo.
    Snapshot the whole environment and restore it so no test leaks into another."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _parse(argv):
    parser = argparse.ArgumentParser()
    add_config_arguments(parser)
    return parser.parse_args(argv)


def _empty_env_file(tmp_path):
    """A real but empty fallback file, so load_dotenv never falls through to the
    developer's own .env during the test run."""
    path = tmp_path / "fallback_env.txt"
    path.write_text("")
    return str(path)


def test_each_flag_writes_its_variable(monkeypatch, tmp_path):
    empty = _empty_env_file(tmp_path)
    for dest, env_name in ENV_FLAG_MAP.items():
        monkeypatch.delenv(env_name, raising=False)
        flag = "--" + dest.replace("_", "-")
        value = f"value-for-{dest}"
        apply_config(_parse([flag, value, "--env-file", empty]))
        assert os.environ[env_name] == value


def test_flag_beats_preset_environment(monkeypatch, tmp_path):
    empty = _empty_env_file(tmp_path)
    monkeypatch.setenv("WATCH_FOLDER", "from-env")
    apply_config(_parse(["--watch-folder", "from-flag", "--env-file", empty]))
    assert os.environ["WATCH_FOLDER"] == "from-flag"


def test_environment_beats_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / "fallback_env.txt"
    env_file.write_text("WATCH_FOLDER=from-file\n")
    monkeypatch.setenv("WATCH_FOLDER", "from-env")
    apply_config(_parse(["--env-file", str(env_file)]))
    assert os.environ["WATCH_FOLDER"] == "from-env"


def test_env_file_fills_when_nothing_else_set(monkeypatch, tmp_path):
    env_file = tmp_path / "fallback_env.txt"
    env_file.write_text("WATCH_FOLDER=from-file\n")
    monkeypatch.delenv("WATCH_FOLDER", raising=False)
    apply_config(_parse(["--env-file", str(env_file)]))
    assert os.environ["WATCH_FOLDER"] == "from-file"


def test_require_env_exits_2_and_names_every_missing(monkeypatch, capsys):
    monkeypatch.delenv("SMO_TEST_A", raising=False)
    monkeypatch.delenv("SMO_TEST_B", raising=False)
    with pytest.raises(SystemExit) as exc:
        require_env("SMO_TEST_A", "SMO_TEST_B")
    assert exc.value.code == 2
    stderr = capsys.readouterr().err
    assert "SMO_TEST_A" in stderr
    assert "SMO_TEST_B" in stderr


def test_require_env_passes_when_all_present(monkeypatch):
    monkeypatch.setenv("SMO_TEST_A", "x")
    require_env("SMO_TEST_A")
