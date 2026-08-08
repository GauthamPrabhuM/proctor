from proctor.term import Style, human_money, sparkline, wrap


def test_wrap_respects_the_width():
    lines = wrap("the quick brown fox jumps over the lazy dog", 12)
    assert all(len(line) <= 12 for line in lines)
    assert " ".join(lines) == "the quick brown fox jumps over the lazy dog"


def test_wrap_breaks_words_longer_than_the_width():
    """A pasted URL or hash must not blow out the column."""
    lines = wrap("x" * 50, 10)
    assert lines == ["x" * 10] * 5


def test_wrap_handles_empty_input():
    assert wrap("", 10) == []


def test_sparkline_scales_to_the_peak():
    assert sparkline([0, 1]) == "▁█"
    assert sparkline([]) == ""
    assert sparkline([0, 0, 0]) == "▁▁▁"
    assert len(sparkline([1, 2, 3, 4])) == 4


def test_style_is_a_no_op_when_disabled():
    plain = Style(False)
    assert plain.bold("hi") == "hi"
    assert plain.red("hi") == "hi"


def test_style_emits_ansi_when_enabled():
    assert Style(True).bold("hi") == "\033[1mhi\033[0m"


def test_no_color_env_disables_colour(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert Style.detect().enabled is False


def test_human_money_formats_thousands():
    assert human_money(1234.5) == "$1,234.50"
