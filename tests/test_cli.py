import io
import json
from pathlib import Path

import pytest

from conftest import assistant_turn, write_transcript
from proctor.cli import EXIT_OK, EXIT_OVER_BUDGET, EXIT_USAGE, run


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep a developer's real ~/.config/proctor out of the test run."""
    monkeypatch.delenv("PROCTOR_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


@pytest.fixture
def populated(logs: Path):
    write_transcript(
        logs,
        "-Users-me-src-app",
        "sess-1",
        [
            assistant_turn(input_tokens=80_000, output_tokens=100),
            assistant_turn(offset=10, input_tokens=90_000, output_tokens=100),
        ],
    )
    return logs


def invoke(argv):
    buffer = io.StringIO()
    code = run(argv, stream=buffer)
    return code, buffer.getvalue()


def test_terminal_report_renders(populated):
    code, output = invoke([str(populated), "--no-color"])
    assert code == EXIT_OK
    assert "estimated spend" in output
    assert "top sessions by cost" in output
    assert "COLD CONTEXT" in output


def test_json_output_is_valid_and_stable(populated):
    code, output = invoke([str(populated), "--json"])
    assert code == EXIT_OK

    payload = json.loads(output)
    assert payload["schema_version"] == 1
    assert payload["totals"]["turns"] == 2
    assert payload["sessions"][0]["id"] == "sess-1"
    assert payload["sessions"][0]["project"] == "/Users/me/src/app"
    assert payload["flagged_waste_usd"] > 0


def test_missing_log_directory_is_a_usage_error(tmp_path):
    code, _ = invoke([str(tmp_path / "nope")])
    assert code == EXIT_USAGE


def test_empty_log_directory_is_a_usage_error(logs):
    code, _ = invoke([str(logs)])
    assert code == EXIT_USAGE


def test_fail_over_gates_on_flagged_waste(populated):
    over, _ = invoke([str(populated), "--json", "--fail-over", "0"])
    assert over == EXIT_OVER_BUDGET

    under, _ = invoke([str(populated), "--json", "--fail-over", "1000000"])
    assert under == EXIT_OK


def test_only_and_skip_filter_findings(populated):
    _, only = invoke([str(populated), "--json", "--only", "balloon"])
    assert json.loads(only)["findings"] == []

    _, skipped = invoke([str(populated), "--json", "--skip", "cache_miss"])
    assert all(f["kind"] != "cache_miss" for f in json.loads(skipped)["findings"])


def test_project_filter_excludes_other_projects(populated):
    _, matched = invoke([str(populated), "--json", "--project", "src/app"])
    assert json.loads(matched)["sessions"]

    code, _ = invoke([str(populated), "--json", "--project", "not-a-project"])
    assert code == EXIT_USAGE


def test_html_output_is_self_contained(populated, tmp_path):
    target = tmp_path / "report.html"
    code, _ = invoke([str(populated), "--json", "--html", str(target)])
    assert code == EXIT_OK

    markup = target.read_text(encoding="utf-8")
    assert "<title>proctor" in markup
    assert 'src="http' not in markup and 'href="http' not in markup.replace(
        'href="https://github.com/GauthamPrabhuM/proctor"', ""
    )


def test_html_escapes_project_names(logs, tmp_path):
    write_transcript(logs, "-tmp-<script>alert(1)", "sess-x", [assistant_turn()])
    target = tmp_path / "r.html"
    invoke([str(logs), "--json", "--html", str(target)])
    markup = target.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;" in markup


def test_invalid_days_is_rejected(populated):
    code, _ = invoke([str(populated), "--days", "0"])
    assert code == EXIT_USAGE


def test_config_file_supplies_defaults(populated, tmp_path, monkeypatch):
    config = tmp_path / "proctor.json"
    config.write_text(
        json.dumps(
            {
                "log_dirs": [str(populated)],
                "top": 3,
                "pricing": {"opus": {"input": 0.0, "output": 0.0}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROCTOR_CONFIG", str(config))

    code, output = invoke(["--json"])
    assert code == EXIT_OK
    # Zeroed prices mean zero spend — proof the override was applied.
    assert json.loads(output)["totals"]["estimated_cost_usd"] == 0.0


def test_broken_config_is_reported_not_swallowed(tmp_path, monkeypatch):
    config = tmp_path / "bad.json"
    config.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("PROCTOR_CONFIG", str(config))
    assert invoke(["--json"])[0] == EXIT_USAGE


def test_unknown_finding_kind_is_rejected(populated):
    with pytest.raises(SystemExit):
        invoke([str(populated), "--only", "nonsense"])
