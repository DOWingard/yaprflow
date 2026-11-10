import os

import pytest

from yaprflow.config import ConfigError, load_env_file


def test_env_file_fills_unset_variables_only(tmp_path, monkeypatch):
    path = tmp_path / "env"
    path.write_text('# comment\n\nYAPRFLOW_T_A=one\nexport YAPRFLOW_T_B="two words"\nYAPRFLOW_T_C=file\n')
    monkeypatch.delenv("YAPRFLOW_T_A", raising=False)
    monkeypatch.delenv("YAPRFLOW_T_B", raising=False)
    monkeypatch.setenv("YAPRFLOW_T_C", "env")
    assert load_env_file(path) == ["YAPRFLOW_T_A", "YAPRFLOW_T_B"]
    assert os.environ["YAPRFLOW_T_A"] == "one"
    assert os.environ["YAPRFLOW_T_B"] == "two words"
    assert os.environ["YAPRFLOW_T_C"] == "env"
    for k in ("YAPRFLOW_T_A", "YAPRFLOW_T_B"):
        monkeypatch.delenv(k)


def test_env_file_missing_or_malformed(tmp_path):
    assert load_env_file(tmp_path / "absent") == []
    bad = tmp_path / "env"
    bad.write_text("not a pair\n")
    with pytest.raises(ConfigError, match=":1:"):
        load_env_file(bad)
