"""Each detector gets a transcript that should trip it and one that should not.

The negative cases matter more than the positive ones: a cost auditor that
cries wolf gets muted, and then it may as well not exist.
"""

from pathlib import Path

import pytest

from conftest import assistant_turn, tool_result, user_prompt, write_transcript
from proctor.analyze import audit
from proctor.detectors import KINDS, select
from proctor.transcripts import load


def kinds(report):
    return {f.kind for f in report.findings}


def find(report, kind):
    matches = [f for f in report.findings if f.kind == kind]
    assert matches, f"expected a {kind} finding, got {sorted(kinds(report))}"
    return matches[0]


def run(logs: Path, since):
    return audit(load([logs], since), days=30)


# --- cold context -----------------------------------------------------------


def test_cold_context_flags_uncached_reprocessing(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(input_tokens=50_000, cache_write_5m=50_000),
            assistant_turn(offset=10, input_tokens=50_000, cache_read=0),
            assistant_turn(offset=20, input_tokens=60_000, cache_read=0),
        ],
    )
    finding = find(run(logs, since), "cache_miss")
    assert finding.evidence["cold_turns"] == 2
    assert finding.evidence["uncached_tokens"] == 110_000
    assert finding.waste > 0


def test_cold_context_ignores_the_first_turn(logs: Path, since):
    """The opening turn of any session is uncached by definition."""
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(input_tokens=200_000),
            assistant_turn(offset=10, input_tokens=100, cache_read=200_000),
        ],
    )
    assert "cache_miss" not in kinds(run(logs, since))


def test_cold_context_ignores_small_turns(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [assistant_turn()]
        + [assistant_turn(offset=i, input_tokens=400) for i in range(1, 20)],
    )
    assert "cache_miss" not in kinds(run(logs, since))


# --- ballooned sessions -----------------------------------------------------


def test_balloon_flags_big_context_thin_output(logs: Path, since):
    records = [
        assistant_turn(offset=i, input_tokens=100, cache_read=150_000, output_tokens=50)
        for i in range(14)
    ]
    write_transcript(logs, "proj", "sess-1", records)
    finding = find(run(logs, since), "balloon")
    assert finding.evidence["peak_context_tokens"] >= 120_000
    assert finding.evidence["turns"] == 14


def test_balloon_ignores_productive_long_sessions(logs: Path, since):
    """Large context is fine when the session is actually producing output."""
    records = [
        assistant_turn(
            offset=i, input_tokens=100, cache_read=150_000, output_tokens=20_000
        )
        for i in range(14)
    ]
    write_transcript(logs, "proj", "sess-1", records)
    assert "balloon" not in kinds(run(logs, since))


def test_balloon_ignores_short_sessions(logs: Path, since):
    records = [
        assistant_turn(offset=i, input_tokens=100, cache_read=200_000, output_tokens=5)
        for i in range(4)
    ]
    write_transcript(logs, "proj", "sess-1", records)
    assert "balloon" not in kinds(run(logs, since))


# --- repeated context -------------------------------------------------------


def test_repeated_context_flags_cross_session_pastes(logs: Path, since):
    block = "PROJECT CONVENTIONS. " * 1000
    for name in ("sess-1", "sess-2", "sess-3"):
        write_transcript(
            logs,
            "proj",
            name,
            [
                user_prompt(f"hi\n\n{block}\n\nplease help", session=name),
                assistant_turn(session=name, offset=1),
            ],
        )
    finding = find(run(logs, since), "repeat")
    assert finding.evidence["sessions"] == 3
    assert finding.scope == "3 sessions"


def test_repeated_context_ignores_single_use_blocks(logs: Path, since):
    block = "ONE OFF. " * 200
    write_transcript(
        logs, "proj", "sess-1", [user_prompt(block), assistant_turn(offset=1)]
    )
    assert "repeat" not in kinds(run(logs, since))


# --- redundant tool calls ---------------------------------------------------


def test_redundant_tool_calls_are_flagged(logs: Path, since):
    records = []
    for i in range(4):
        records.append(
            assistant_turn(
                offset=i * 2,
                tool_uses=[
                    {"id": f"t{i}", "name": "Read", "input": {"file_path": "/big.py"}}
                ],
            )
        )
        records.append(tool_result(f"t{i}", "y" * 20_000, offset=i * 2 + 1))
    write_transcript(logs, "proj", "sess-1", records)

    finding = find(run(logs, since), "redundant_tool")
    assert finding.evidence["repeats"] == 4
    assert finding.evidence["worst_call"] == "Read(file_path=/big.py)"


def test_two_identical_calls_are_not_enough_to_flag(logs: Path, since):
    records = []
    for i in range(2):
        records.append(
            assistant_turn(
                offset=i * 2,
                tool_uses=[
                    {"id": f"t{i}", "name": "Read", "input": {"file_path": "/big.py"}}
                ],
            )
        )
        records.append(tool_result(f"t{i}", "y" * 20_000, offset=i * 2 + 1))
    write_transcript(logs, "proj", "sess-1", records)
    assert "redundant_tool" not in kinds(run(logs, since))


def test_repeated_cheap_calls_are_not_flagged(logs: Path, since):
    records = []
    for i in range(6):
        records.append(
            assistant_turn(
                offset=i * 2,
                tool_uses=[{"id": f"t{i}", "name": "Bash", "input": {"command": "ls"}}],
            )
        )
        records.append(tool_result(f"t{i}", "a few files", offset=i * 2 + 1))
    write_transcript(logs, "proj", "sess-1", records)
    assert "redundant_tool" not in kinds(run(logs, since))


# --- oversized prompts ------------------------------------------------------


def test_oversized_prompt_is_measured_against_the_median(logs: Path, since):
    records = [user_prompt("short question", offset=i) for i in range(8)]
    records.append(user_prompt("Z" * 200_000, offset=9))
    records.append(assistant_turn(offset=10))
    write_transcript(logs, "proj", "sess-1", records)

    finding = find(run(logs, since), "fat_prompt")
    assert finding.evidence["prompt_tokens"] == 50_000
    assert finding.evidence["median_tokens"] < 100


def test_uniformly_large_prompts_are_not_all_flagged(logs: Path, since):
    """If every prompt is big, none of them is an outlier."""
    records = [user_prompt("W" * 40_000, offset=i) for i in range(8)]
    records.append(assistant_turn(offset=9))
    write_transcript(logs, "proj", "sess-1", records)
    assert "fat_prompt" not in kinds(run(logs, since))


# --- subagent burn ----------------------------------------------------------


def test_subagent_burn_is_flagged_when_sidechains_dominate(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(input_tokens=100_000, output_tokens=100),
            assistant_turn(
                offset=5, input_tokens=400_000, output_tokens=100, sidechain=True
            ),
        ],
    )
    finding = find(run(logs, since), "sidechain")
    assert finding.evidence["share"] > 0.4


def test_light_subagent_use_is_not_flagged(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(input_tokens=500_000, output_tokens=100),
            assistant_turn(
                offset=5, input_tokens=10_000, output_tokens=50, sidechain=True
            ),
        ],
    )
    assert "sidechain" not in kinds(run(logs, since))


# --- model mismatch ---------------------------------------------------------


def test_model_mismatch_prices_the_cheaper_alternative(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [assistant_turn(model="claude-fable-5", input_tokens=200_000, output_tokens=100)],
    )
    finding = find(run(logs, since), "model_mismatch")
    assert finding.evidence["alternative_cost"] < finding.evidence["actual_cost"]


def test_cheap_models_never_trip_model_mismatch(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(
                model="claude-haiku-4-5", input_tokens=500_000, output_tokens=100
            )
        ],
    )
    assert "model_mismatch" not in kinds(run(logs, since))


def test_premium_model_doing_heavy_output_is_not_flagged(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(
                model="claude-opus-5", input_tokens=200_000, output_tokens=50_000
            )
        ],
    )
    assert "model_mismatch" not in kinds(run(logs, since))


# --- registry ---------------------------------------------------------------


def test_registry_kinds_are_unique():
    assert len(KINDS) == len(set(KINDS))


def test_select_filters_by_kind():
    assert [d.kind for d in select(only=["balloon"])] == ["balloon"]
    assert "balloon" not in [d.kind for d in select(skip=["balloon"])]


@pytest.mark.parametrize("kind", KINDS)
def test_every_detector_survives_an_empty_corpus(kind, logs: Path, since):
    write_transcript(logs, "proj", "sess-1", [assistant_turn()])
    report = audit(load([logs], since), detectors=select(only=[kind]))
    assert report.findings == []
