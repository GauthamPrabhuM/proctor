"""Parsing Claude Code JSONL session transcripts.

Claude Code appends one JSON object per line to ``~/.claude/projects/<slug>/
<session-id>.jsonl``. The format is not a published contract, so every field
access here is defensive: a record we do not recognise is skipped rather than
raised on, and a corrupt line never aborts the run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .types import Session, ToolCall, Turn, UserPrompt

log = logging.getLogger(__name__)

__all__ = ["Corpus", "estimate_tokens", "iter_transcript_files", "load"]

CHARS_PER_TOKEN = 4
"""Rough characters-per-token ratio for text we only see as a string.

Used for user prompts, pasted blocks, and tool results — none of which the API
reports usage for individually. It is an estimate, not a measurement; findings
derived from it are labelled as approximate.
"""

MIN_BLOCK_CHARS = 500
"""Paragraphs shorter than this are too small to be worth fingerprinting."""

PREVIEW_CHARS = 110

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
_WHITESPACE = re.compile(r"\s+")


def estimate_tokens(text: str) -> int:
    """Approximate the token count of a string. Never returns less than 1."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    return _WHITESPACE.sub(" ", text).strip()[:limit]


def _fingerprint(text: str) -> str:
    return hashlib.sha1(_WHITESPACE.sub(" ", text).strip().encode()).hexdigest()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class SharedBlock:
    """A large chunk of text that appeared verbatim in more than one session."""

    tokens: int
    preview: str
    sessions: set[str] = field(default_factory=set)

    @property
    def redundant_tokens(self) -> int:
        """Tokens paid for beyond the first occurrence."""
        return self.tokens * (len(self.sessions) - 1)


@dataclass
class Corpus:
    """Everything parsed out of a set of transcript files."""

    sessions: dict[str, Session] = field(default_factory=dict)
    shared_blocks: dict[str, SharedBlock] = field(default_factory=dict)
    files_read: int = 0
    files_skipped: int = 0
    lines_malformed: int = 0

    def with_turns(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.turns]

    def duplicated_blocks(self) -> list[SharedBlock]:
        return [b for b in self.shared_blocks.values() if len(b.sessions) > 1]


def iter_transcript_files(paths: Iterable[Path], since: datetime) -> Iterator[Path]:
    """Yield ``*.jsonl`` files under ``paths`` last modified at or after ``since``.

    ``paths`` entries may be files or directories; directories are walked
    recursively. Files whose mtime predates the window are skipped without being
    opened, which is what keeps a full-history ``~/.claude/projects`` cheap to
    audit.
    """
    seen: set[Path] = set()
    for raw in paths:
        root = Path(raw).expanduser()
        candidates = [root] if root.is_file() else sorted(root.rglob("*.jsonl"))
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError as exc:  # unreadable, vanished mid-walk, bad symlink
                log.debug("skipping %s: %s", path, exc)
                continue
            if mtime < since:
                continue
            yield path


def project_name(path: Path) -> str:
    """Recover a readable project path from Claude Code's directory slug.

    Claude Code encodes ``/Users/me/src/app`` as ``-Users-me-src-app``, so every
    dash becomes a separator — including the leading one, which is the root.
    Directory names that hyphenate a real path segment round-trip imperfectly;
    that is inherent to the encoding, not something we can recover.
    """
    slug = path.parent.name
    return slug.replace("-", "/") if slug else str(path.parent)


def _iter_text(content: object) -> Iterator[str]:
    """Yield every text string reachable from a message ``content`` field."""
    if isinstance(content, str):
        yield content
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            yield block["text"]
        elif kind == "tool_result":
            inner = block.get("content")
            if isinstance(inner, str):
                yield inner
            elif isinstance(inner, list):
                for sub in inner:
                    if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                        yield sub["text"]


def _tool_signature(name: str, tool_input: object) -> str:
    """Stable identity for a tool call, so repeats of it can be counted.

    Inputs are serialized with sorted keys so that key ordering differences do
    not read as distinct calls.
    """
    try:
        payload = json.dumps(tool_input, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(tool_input)
    return hashlib.sha1(f"{name}\x00{payload}".encode()).hexdigest()


def _tool_preview(name: str, tool_input: object) -> str:
    """Human-readable label for a tool call, e.g. ``Read(file_path=/a/b.py)``."""
    if not isinstance(tool_input, dict):
        return name
    interesting = ("file_path", "path", "pattern", "command", "url", "query")
    for key in interesting:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return f"{name}({key}={_preview(value, 70)})"
    return name


def _usage_tokens(usage: dict) -> tuple[int, int, int, int, int]:
    """Extract the five billable token counts from a ``usage`` object.

    Handles both the newer ``cache_creation`` breakdown (which separates 5-minute
    from 1-hour writes) and the older flat ``cache_creation_input_tokens``.
    """
    creation = usage.get("cache_creation")
    if isinstance(creation, dict) and "ephemeral_5m_input_tokens" in creation:
        write_5m = int(creation.get("ephemeral_5m_input_tokens") or 0)
        write_1h = int(creation.get("ephemeral_1h_input_tokens") or 0)
    else:
        write_5m = int(usage.get("cache_creation_input_tokens") or 0)
        write_1h = 0
    return (
        int(usage.get("input_tokens") or 0),
        write_5m,
        write_1h,
        int(usage.get("cache_read_input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
    )


class _FileParser:
    """Parses one transcript file into an existing :class:`Corpus`."""

    def __init__(self, corpus: Corpus, path: Path, since: datetime) -> None:
        self.corpus = corpus
        self.path = path
        self.since = since
        self.project = project_name(path)
        self.default_sid = path.stem
        # (session id, message id) -> index into that session's turn list. The
        # API can emit the same message id more than once across streaming
        # retries; we keep the copy with the most output tokens.
        self.turn_index: dict[tuple[str, str], int] = {}
        self.pending_tools: dict[str, ToolCall] = {}

    def session_for(self, sid: str) -> Session:
        session = self.corpus.sessions.get(sid)
        if session is None:
            session = Session(id=sid, project=self.project, path=self.path)
            self.corpus.sessions[sid] = session
        return session

    def run(self) -> None:
        with self.path.open("r", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    self.corpus.lines_malformed += 1
                    continue
                if isinstance(record, dict):
                    self.handle(record)

    def handle(self, record: dict) -> None:
        message = record.get("message")
        if not isinstance(message, dict):
            message = {}
        ts = parse_timestamp(record.get("timestamp"))
        if ts is not None and ts < self.since:
            return

        sid = record.get("sessionId") or self.default_sid
        session = self.session_for(sid)
        if ts is not None:
            if session.first_ts is None or ts < session.first_ts:
                session.first_ts = ts
            if session.last_ts is None or ts > session.last_ts:
                session.last_ts = ts

        record_type = record.get("type")
        if record_type == "user":
            self.handle_user(record, message, session, ts)
        elif record_type == "assistant":
            self.handle_assistant(record, message, session, ts)

    def handle_user(
        self, record: dict, message: dict, session: Session, ts: datetime | None
    ) -> None:
        content = message.get("content")
        if not isinstance(content, (str, list)):
            return

        # Fingerprint paragraph-sized chunks so a pasted block is still detected
        # when the text surrounding it differs between sessions.
        for text in _iter_text(content):
            for paragraph in _PARAGRAPH_SPLIT.split(text):
                if len(paragraph) < MIN_BLOCK_CHARS:
                    continue
                digest = _fingerprint(paragraph)
                block = self.corpus.shared_blocks.get(digest)
                if block is None:
                    block = SharedBlock(
                        tokens=estimate_tokens(paragraph),
                        preview=_preview(paragraph),
                    )
                    self.corpus.shared_blocks[digest] = block
                block.sessions.add(session.id)

        # Attribute tool results back to the call that produced them.
        if isinstance(content, list):
            for block_ in content:
                if not isinstance(block_, dict) or block_.get("type") != "tool_result":
                    continue
                use_id = block_.get("tool_use_id")
                if not isinstance(use_id, str):
                    continue
                call = self.pending_tools.pop(use_id, None)
                if call is None:
                    continue
                chars = sum(len(chunk) for chunk in _iter_text([block_]))
                call.result_tokens = chars // CHARS_PER_TOKEN if chars else 0

        if record.get("isMeta"):
            return

        # A genuine typed prompt: a bare string, or text blocks in a list whose
        # other blocks are tool results we have already accounted for.
        if isinstance(content, str):
            session.prompts.append(
                UserPrompt(estimate_tokens(content), _preview(content), ts)
            )
        else:
            for block_ in content:
                if isinstance(block_, dict) and block_.get("type") == "text":
                    text = block_.get("text") or ""
                    session.prompts.append(
                        UserPrompt(estimate_tokens(text), _preview(text), ts)
                    )

    def handle_assistant(
        self, record: dict, message: dict, session: Session, ts: datetime | None
    ) -> None:
        model = message.get("model") or ""
        content = message.get("content")
        if isinstance(content, list):
            for block_ in content:
                if not isinstance(block_, dict) or block_.get("type") != "tool_use":
                    continue
                name = str(block_.get("name") or "tool")
                tool_input = block_.get("input")
                call = ToolCall(
                    name=name,
                    signature=_tool_signature(name, tool_input),
                    preview=_tool_preview(name, tool_input),
                )
                session.tool_calls.append(call)
                use_id = block_.get("id")
                if isinstance(use_id, str):
                    self.pending_tools[use_id] = call

        usage = message.get("usage")
        if not isinstance(usage, dict) or model == "<synthetic>":
            return

        inp, write_5m, write_1h, read, out = _usage_tokens(usage)
        turn = Turn(
            ts=ts,
            model=model,
            input_tokens=inp,
            cache_write_5m=write_5m,
            cache_write_1h=write_1h,
            cache_read=read,
            output_tokens=out,
            sidechain=bool(record.get("isSidechain")),
            message_id=message.get("id"),
        )

        key = (session.id, turn.message_id or "")
        if turn.message_id and key in self.turn_index:
            position = self.turn_index[key]
            if turn.output_tokens >= session.turns[position].output_tokens:
                session.turns[position] = turn
            return
        session.turns.append(turn)
        if turn.message_id:
            self.turn_index[key] = len(session.turns) - 1


def load(paths: Sequence[Path], since: datetime) -> Corpus:
    """Parse every transcript under ``paths`` modified since ``since``."""
    corpus = Corpus()
    for path in iter_transcript_files(paths, since):
        try:
            _FileParser(corpus, path, since).run()
        except OSError as exc:
            log.warning("could not read %s: %s", path, exc)
            corpus.files_skipped += 1
            continue
        corpus.files_read += 1
    return corpus


def group_tool_calls(session: Session) -> dict[str, list[ToolCall]]:
    """Bucket a session's tool calls by signature, preserving order."""
    grouped: dict[str, list[ToolCall]] = defaultdict(list)
    for call in session.tool_calls:
        grouped[call.signature].append(call)
    return dict(grouped)
