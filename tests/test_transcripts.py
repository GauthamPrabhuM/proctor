from datetime import timedelta
from pathlib import Path

from conftest import (
    BASE_TIME,
    assistant_turn,
    tool_result,
    user_prompt,
    write_transcript,
)
from proctor.transcripts import (
    estimate_tokens,
    group_tool_calls,
    load,
    parse_timestamp,
    project_name,
)


def test_parses_usage_into_turns(logs: Path, since):
    write_transcript(
        logs,
        "-Users-me-src-app",
        "sess-1",
        [
            assistant_turn(input_tokens=1000, cache_read=500, output_tokens=200),
            assistant_turn(offset=10, input_tokens=10, cache_read=1500),
        ],
    )
    corpus = load([logs], since)
    session = corpus.sessions["sess-1"]

    assert session.n_turns == 2
    assert session.input_tokens == 1010
    assert session.cache_read == 2000
    assert session.output_tokens == 250
    assert session.project == "/Users/me/src/app"


def test_project_slug_is_decoded():
    assert project_name(Path("-Users-me-src-app/x.jsonl")) == "/Users/me/src/app"


def test_duplicate_message_ids_keep_the_largest_output(logs: Path, since):
    """Streaming retries re-emit the same message id; only one should count."""
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(message_id="msg_a", output_tokens=10),
            assistant_turn(message_id="msg_a", offset=1, output_tokens=900),
            assistant_turn(message_id="msg_a", offset=2, output_tokens=40),
        ],
    )
    session = load([logs], since).sessions["sess-1"]
    assert session.n_turns == 1
    assert session.output_tokens == 900


def test_synthetic_and_usageless_records_are_ignored(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(model="<synthetic>", output_tokens=999),
            {"type": "assistant", "sessionId": "sess-1", "message": {"model": "x"}},
            assistant_turn(offset=5, output_tokens=7),
        ],
    )
    session = load([logs], since).sessions["sess-1"]
    assert session.n_turns == 1
    assert session.output_tokens == 7


def test_malformed_lines_do_not_abort_the_file(logs: Path, since):
    path = write_transcript(logs, "proj", "sess-1", [assistant_turn(output_tokens=5)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")
        handle.write("\n")
        handle.write('"a bare string"\n')

    corpus = load([logs], since)
    assert corpus.lines_malformed == 1
    assert corpus.sessions["sess-1"].output_tokens == 5


def test_files_older_than_the_window_are_skipped(logs: Path):
    write_transcript(logs, "proj", "sess-1", [assistant_turn()])
    future = BASE_TIME + timedelta(days=365)
    corpus = load([logs], future)
    assert corpus.with_turns() == []


def test_meta_records_are_not_counted_as_user_prompts(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            user_prompt("a real question", offset=1),
            user_prompt("<system injected>", offset=2, meta=True),
            assistant_turn(offset=3),
        ],
    )
    session = load([logs], since).sessions["sess-1"]
    assert [p.preview for p in session.prompts] == ["a real question"]


def test_identical_blocks_across_sessions_are_fingerprinted(logs: Path, since):
    block = "CONTEXT BLOCK. " * 60  # comfortably over the 500-char floor
    for name in ("sess-1", "sess-2"):
        write_transcript(
            logs,
            "proj",
            name,
            [
                user_prompt(f"intro\n\n{block}\n\noutro", session=name),
                assistant_turn(session=name, offset=1),
            ],
        )
    duplicated = load([logs], since).duplicated_blocks()
    assert len(duplicated) == 1
    assert duplicated[0].sessions == {"sess-1", "sess-2"}
    assert duplicated[0].redundant_tokens == duplicated[0].tokens


def test_short_blocks_are_not_fingerprinted(logs: Path, since):
    for name in ("sess-1", "sess-2"):
        write_transcript(
            logs,
            "proj",
            name,
            [user_prompt("short", session=name), assistant_turn(session=name)],
        )
    assert load([logs], since).duplicated_blocks() == []


def test_tool_calls_are_paired_with_their_results(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(
                tool_uses=[
                    {"id": "t1", "name": "Read", "input": {"file_path": "/a/b.py"}}
                ]
            ),
            tool_result("t1", "x" * 4000, offset=1),
            assistant_turn(
                offset=2,
                tool_uses=[
                    {"id": "t2", "name": "Read", "input": {"file_path": "/a/b.py"}}
                ],
            ),
            tool_result("t2", "x" * 4000, offset=3),
        ],
    )
    session = load([logs], since).sessions["sess-1"]
    grouped = group_tool_calls(session)

    assert len(grouped) == 1, "identical calls should share one signature"
    calls = next(iter(grouped.values()))
    assert len(calls) == 2
    assert calls[0].result_tokens == 1000
    assert calls[0].preview == "Read(file_path=/a/b.py)"


def test_tool_signature_ignores_key_order(logs: Path, since):
    write_transcript(
        logs,
        "proj",
        "sess-1",
        [
            assistant_turn(
                tool_uses=[{"id": "t1", "name": "Grep", "input": {"a": 1, "b": 2}}]
            ),
            assistant_turn(
                offset=1,
                tool_uses=[{"id": "t2", "name": "Grep", "input": {"b": 2, "a": 1}}],
            ),
        ],
    )
    session = load([logs], since).sessions["sess-1"]
    assert len(group_tool_calls(session)) == 1


def test_legacy_flat_cache_creation_field_is_understood(logs: Path, since):
    record = assistant_turn()
    record["message"]["usage"] = {
        "input_tokens": 10,
        "cache_creation_input_tokens": 700,
        "cache_read_input_tokens": 0,
        "output_tokens": 5,
    }
    write_transcript(logs, "proj", "sess-1", [record])
    session = load([logs], since).sessions["sess-1"]
    assert session.cache_write == 700
    assert session.turns[0].cache_write_1h == 0


def test_estimate_tokens_never_returns_zero():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd" * 25) == 25


def test_parse_timestamp_handles_z_suffix_and_junk():
    assert parse_timestamp("2026-08-01T12:00:00Z").year == 2026
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(None) is None


def test_a_bare_file_path_can_be_audited(logs: Path, since):
    path = write_transcript(logs, "proj", "sess-1", [assistant_turn(output_tokens=3)])
    corpus = load([path], since)
    assert corpus.sessions["sess-1"].output_tokens == 3
