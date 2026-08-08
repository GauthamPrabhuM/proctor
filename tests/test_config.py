import json

import pytest

from proctor.config import Config, ConfigError, default_config_path, load_config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PROCTOR_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def write(tmp_path, payload, name="config.json"):
    path = tmp_path / name
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return path


def test_no_config_anywhere_is_fine():
    config = load_config()
    assert config == Config()
    assert config.price_book().for_model("claude-opus-5").input == 5.0


def test_default_path_follows_xdg(tmp_path):
    assert default_config_path() == tmp_path / "xdg" / "proctor" / "config.json"


def test_default_path_is_picked_up_when_present(tmp_path):
    path = tmp_path / "xdg" / "proctor" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"days": 3}), encoding="utf-8")
    assert load_config().days == 3


def test_explicit_path_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCTOR_CONFIG", str(write(tmp_path, {"days": 1}, "env.json")))
    explicit = write(tmp_path, {"days": 99}, "explicit.json")
    assert load_config(explicit).days == 99


def test_a_named_but_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.json")


def test_invalid_json_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "{nope"))


def test_top_level_must_be_an_object(tmp_path):
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_config(write(tmp_path, [1, 2, 3]))


def test_pricing_must_be_an_object(tmp_path):
    with pytest.raises(ConfigError, match="'pricing' must be an object"):
        load_config(write(tmp_path, {"pricing": "cheap"}))


def test_pricing_entry_must_have_input_and_output(tmp_path):
    with pytest.raises(ConfigError, match=r"missing \['output'\]"):
        load_config(write(tmp_path, {"pricing": {"opus": {"input": 1.0}}}))


def test_log_dirs_accepts_a_bare_string(tmp_path):
    assert load_config(write(tmp_path, {"log_dirs": "~/logs"})).log_dirs == ["~/logs"]


def test_log_dirs_rejects_a_number(tmp_path):
    with pytest.raises(ConfigError, match="'log_dirs' must be"):
        load_config(write(tmp_path, {"log_dirs": 7}))


def test_price_overrides_reach_the_price_book(tmp_path):
    config = load_config(
        write(tmp_path, {"pricing": {"opus": {"input": 1, "output": 2}}})
    )
    price = config.price_book().for_model("claude-opus-5")
    assert (price.input, price.output) == (1.0, 2.0)
    assert price.cache_read == 0.1
