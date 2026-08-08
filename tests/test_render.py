import io

from proctor.render import html, json_report, terminal
from proctor.term import Style
from proctor.types import Finding, Report, Session, Totals, Turn


def sample_report() -> Report:
    session = Session(id="abcdef123456", project="/Users/me/app", path=None)
    session.turns = [
        Turn(ts=None, model="claude-opus-5", input_tokens=1000, output_tokens=100)
    ]
    session.cost = 1.25
    return Report(
        sessions=[session],
        findings=[
            Finding(
                kind="cache_miss",
                waste=0.5,
                detail="Something expensive happened.",
                scope="abcdef12",
                project="/Users/me/app",
                evidence={"cold_turns": 2},
            )
        ],
        totals=Totals(
            cost=1.25,
            input_tokens=1000,
            cache_read=9000,
            output_tokens=100,
            turns=1,
        ),
        daily={"2026-08-01": 0.5, "2026-08-02": 0.75},
        by_model={"claude-opus-5": 1.25},
        days=30,
    )


def test_terminal_renders_every_section():
    buffer = io.StringIO()
    terminal.render(sample_report(), buffer, Style(False))
    output = buffer.getvalue()

    for expected in (
        "estimated spend",
        "daily burn",
        "spend by model",
        "top sessions by cost",
        "COLD CONTEXT",
        "Something expensive happened.",
    ):
        assert expected in output


def test_terminal_reports_a_clean_bill_of_health():
    report = sample_report()
    report.findings = []
    buffer = io.StringIO()
    terminal.render(report, buffer, Style(False))
    assert "Clean bill of health" in buffer.getvalue()


def test_terminal_notes_unreadable_files():
    report = sample_report()
    report.skipped_files = 3
    buffer = io.StringIO()
    terminal.render(report, buffer, Style(False))
    assert "3 transcript file(s) unreadable" in buffer.getvalue()


def test_terminal_says_how_many_findings_were_hidden():
    report = sample_report()
    report.findings = report.findings * 10
    buffer = io.StringIO()
    terminal.render(report, buffer, Style(False), top=1)
    assert "8 more finding(s)" in buffer.getvalue()


def test_json_round_trips():
    payload = json_report.to_dict(sample_report())
    assert payload["findings"][0]["evidence"]["cold_turns"] == 2
    assert payload["sessions"][0]["id"] == "abcdef123456"
    assert payload["totals"]["cache_hit_rate"] > 0


def test_html_is_self_contained_and_theme_aware():
    markup = html.build(sample_report())
    assert "prefers-color-scheme: dark" in markup
    assert "<script" not in markup
    assert "@import" not in markup
    assert "COLD CONTEXT" in markup


def test_html_handles_an_empty_report():
    markup = html.build(Report())
    assert "Nothing flagged" in markup
    assert "No dated turns" in markup


def test_html_escape_covers_quotes_and_angle_brackets():
    assert html.escape('<a href="x">&') == "&lt;a href=&quot;x&quot;&gt;&amp;"


def test_daily_chart_fills_quiet_days():
    """A gap in activity must occupy real width, or the x-axis lies."""
    report = sample_report()
    report.daily = {"2026-08-01": 1.0, "2026-08-05": 2.0}

    series = html._daily_series(report)
    assert [d for d, _ in series] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
    ]
    assert [c for _, c in series] == [1.0, 0.0, 0.0, 0.0, 2.0]


def test_quiet_days_draw_no_mark():
    """A zero day must not render a stub bar that reads as spend."""
    report = sample_report()
    report.daily = {"2026-08-01": 1.0, "2026-08-03": 2.0}

    markup = html.build(report)
    # Three columns, but only the two days with spend carry a fill.
    assert markup.count('class="col') == 3
    assert markup.count('class="fill"') == 2


def test_empty_daily_series_is_handled():
    report = sample_report()
    report.daily = {}
    assert html._daily_series(report) == []
    assert "No dated turns" in html.build(report)


def test_cache_hit_status_thresholds():
    assert html._hit_class(0.99) == "good"
    assert html._hit_class(html.HIT_RATE_GOOD) == "good"
    assert html._hit_class(0.70) == "warn"
    assert html._hit_class(0.10) == "crit"


def test_status_colour_is_never_the_only_signal():
    """A colour-blind reader must still get the hit rate as a number."""
    markup = html.build(sample_report())
    assert "0%</span>" in markup or "%</span>" in markup
