"""Regression tests for security, gameplay fairness, showcraft, and ingestion audit fixes."""
from __future__ import annotations

import io
import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from ingest import other_parsers, structure as struct_mod
from ingest.model import Block, ParsedDocument
from routers import admin as admin_router
from services import badges, bank, engine, host_service, settings_store, stats


def test_abandoned_runs_always_receive_warmup_only_badge():
    rules = settings_store.DEFAULTS["game"]
    show_run = {
        "mode": "show",
        "report_answered": 3,
        "report_correct": 3,
        "strikes": 0,
        "status": "abandoned",
    }
    badge = badges.evaluate_run(show_run, rules, status="abandoned")
    assert badge["label"] == badges.WARMUP_ONLY["label"]
    assert badge["tier"] == 0

    sd_run = {
        "mode": "sudden_death",
        "report_answered": 3,
        "report_correct": 3,
        "strikes": 0,
        "status": "abandoned",
    }
    sd_badge = badges.evaluate_run(sd_run, rules, status="abandoned")
    assert sd_badge["label"] == badges.WARMUP_ONLY["label"]
    assert sd_badge["tier"] == 0


def test_sudden_death_short_run_cannot_claim_last_one_standing_without_cleared_bank():
    short = badges.evaluate_sudden_death(correct=3, answered=3, strikes=0, max_strikes=3)
    assert short["label"] != "Last One Standing"

    genuine_clear = badges.evaluate_sudden_death(correct=3, answered=3, strikes=0, max_strikes=3, cleared_bank=True)
    assert genuine_clear["label"] == "Last One Standing"

    full_ten = badges.evaluate_sudden_death(correct=10, answered=10, strikes=0, max_strikes=3)
    assert full_ten["label"] == "Last One Standing"


def test_scoring_parity_sudden_death_streak_speed_weights_and_second_chance():
    rules = settings_store.DEFAULTS["game"]
    item_mcq = {"slot": "report", "points_multiplier": 1.0, "wager": None, "round_no": 3}
    item_ff = {"slot": "fact_fake", "points_multiplier": 1.0, "wager": None, "round_no": 3}
    item_sc = {"slot": "second_chance", "points_multiplier": 0.5, "wager": None, "round_no": 5}

    # Sudden Death adds streak bonus (+10 * min(streak, 5))
    sd_zero = engine._points({"mode": "sudden_death", "streak": 0}, rules, item_mcq, True, False, 0.5)
    sd_five = engine._points({"mode": "sudden_death", "streak": 5}, rules, item_mcq, True, False, 0.5)
    assert sd_five - sd_zero == 50

    # Speed Run weights MCQ 2x Fact-or-Fake so button-mashing 50/50 statements isn't dominant
    sp_mcq = engine._points({"mode": "speed", "streak": 0}, rules, item_mcq, True, False, 1.0)
    sp_ff = engine._points({"mode": "speed", "streak": 0}, rules, item_ff, True, False, 1.0)
    assert sp_mcq == 150
    assert sp_ff == 75

    # Second Chance awards half of the full base+streak award (strictly additive bonus)
    show_full = engine._points({"mode": "show", "streak": 4}, rules, item_mcq, True, False, 1.0)
    show_sc = engine._points({"mode": "show", "streak": 4}, rules, item_sc, True, False, 1.0)
    assert show_sc == round(show_full * 0.5)


def test_readiness_blocks_pure_fact_fake_and_speed_run():
    # Two right Fact-or-Fake coin flips with 0 MCQ wins cannot clear a section
    assert stats._cleared({"recent_answered": 2, "recent_correct": 2, "recent_choice_correct": 0}) is False
    # One right MCQ clears a section
    assert stats._cleared({"recent_answered": 1, "recent_correct": 1, "recent_choice_correct": 1}) is True
    # Speed Run and second_chance are excluded from section readiness queries
    assert "r.mode <> 'speed'" in stats._SECTION_ANSWERS
    assert "'second_chance'" in stats._SECTION_ANSWERS


def test_deep_merge_allows_clearing_nullable_fields():
    base = {"report_url": "https://company.example/q3", "support_email": "help@company.example", "title": "Q3"}
    merged = settings_store.deep_merge(base, {"report_url": None, "support_email": None})
    assert merged["report_url"] is None
    assert merged["support_email"] is None
    assert merged["title"] == "Q3"


def test_csv_formula_injection_is_neutralized():
    for payload in ("=CMD|' /C calc'!A0", "+1+1", "-2+3", "@SUM(1+1)", "\t=1", "\r=1"):
        cleaned = stats._csv_cell(payload)
        assert isinstance(cleaned, str) and cleaned.startswith("'")
    assert stats._csv_cell("Normal Name") == "Normal Name"
    assert stats._csv_cell(42) == 42
    assert stats._csv_cell(None) is None


def test_svg_upload_filter_blocks_active_content_and_allows_static_logos():
    safe_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 64 64"><rect width="64" height="64" fill="#ff6b35"/></svg>'
    assert admin_router._UNSAFE_SVG_RE.search(safe_svg) is None

    for evil in (
        b"<svg><script>alert(1)</script></svg>",
        b'<svg><foreignObject><body onload="alert(1)"/></foreignObject></svg>',
        b'<svg><animate attributeName="href" to="javascript:alert(1)"/></svg>',
        b'<svg><use href="https://evil.example/a.svg#x"/></svg>',
        b'<svg><a xlink:href="data:text/html;base64,PHNjcmlwdD4="/></svg>',
        b'<svg onload="alert(1)"/>',
    ):
        assert admin_router._UNSAFE_SVG_RE.search(evil) is not None, f"missed unsafe svg: {evil!r}"


def test_bank_choose_prevents_replay_difficulty_inversion():
    easy_seen = {
        "id": "q-easy",
        "kind": "mcq",
        "source_type": "text",
        "difficulty": "easy",
        "section_id": "s1",
        "pinned": False,
        "origin": "generated",
        "times_served": 5,
        "times_correct": 4,
        "question_text": "What year did Denver win Super Bowl 50?",
        "options": ["2016", "2014", "2018", "2020"],
        "correct_index": 0,
    }
    hard_unseen = {
        "id": "q-hard",
        "kind": "mcq",
        "source_type": "text",
        "difficulty": "hard",
        "section_id": "s2",
        "pinned": False,
        "origin": "generated",
        "times_served": 0,
        "times_correct": 0,
        "question_text": "Which defensive tackle recovered the decisive fumble?",
        "options": ["Malik Jackson", "Derek Wolfe", "Sylvester Williams", "Vance Walker"],
        "correct_index": 0,
    }
    picked = bank.choose(
        [easy_seen, hard_unseen],
        seen={"q-easy": {"correct": True, "count": 1}},
        used=set(),
        difficulty="easy",
    )
    # Even on replay, an easy slot should pick the easy question rather than dumping a hard question into Q1.
    assert picked is not None and picked["id"] == "q-easy"


def test_structure_keeps_short_media_sections_and_splits_unpunctuated_text():
    parsed = ParsedDocument(
        title="Highlights",
        blocks=[
            Block(kind="heading", text="1. Watch the Winning Play", level=1),
            Block(kind="paragraph", text="Short clip:", links=["https://www.youtube.com/watch?v=V_F6byM7Xyo"]),
            Block(kind="heading", text="2. Details", level=1),
            Block(kind="paragraph", text=" ".join(["word"] * 50)),
        ],
    )
    structured = struct_mod.build_structure(parsed, "highlights.docx")
    assert len(structured.sections) == 2
    assert structured.sections[0].video_urls == ["https://www.youtube.com/watch?v=V_F6byM7Xyo"]

    unpunctuated = " ".join(f"token{i}" for i in range(600))
    pieces = struct_mod._split_long(unpunctuated)
    assert len(pieces) >= 2
    assert all(struct_mod.approx_tokens(p) <= struct_mod.MAX_TOKENS for p in pieces)


def test_pptx_parser_extracts_native_charts():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Quarterly Revenue"
    chart_data = CategoryChartData()
    chart_data.categories = ["Q1", "Q2", "Q3"]
    chart_data.add_series("Revenue ($M)", (12.5, 15.0, 18.2))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), chart_data)

    buf = io.BytesIO()
    prs.save(buf)
    parsed = other_parsers.parse_pptx(buf.getvalue())
    chart_blocks = [b for b in parsed.blocks if b.kind == "chart"]
    assert len(chart_blocks) == 1
    assert "Q1" in chart_blocks[0].text and "18.2" in chart_blocks[0].text


async def test_host_persona_and_sudden_death_stakes_are_accurate(monkeypatch):
    persona = host_service.build_persona(
        name="Dex", game_title="Read It or Weep", company="Mile High", sarcasm=5, quiz_title="Denver Broncos Fan Guide",
    )
    assert "corporate life" not in persona.lower()
    assert "opened the attachment" not in persona.lower()

    fake_item = {
        "id": "item-1",
        "slot": "music",
        "round_name": "Music Break",
        "answered_at": "2026-01-01T00:00:00Z",
        "is_correct": True,
        "timed_out": False,
        "correct_index": 0,
        "time_limit_sec": 20,
        "time_taken_ms": 5000,
        "lifelines_used": [],
        "music_track_id": "track-1",
        "payload": {"options": ["Jump", "Beat It", "Thriller", "Africa"], "strike_restored": False},
    }

    async def fake_fetchrow(sql, *args):
        if "run_items" in sql:
            return fake_item
        if "music_tracks" in sql:
            return {"artist": "Van Halen", "title": "Jump"}
        return None

    monkeypatch.setattr(host_service.db, "fetchrow", fake_fetchrow)
    run = {"id": "run-1", "mode": "sudden_death", "strikes": 0, "streak": 2, "decade": "80s", "score": 300}
    player = {"name": "Aaron"}

    # Without strike_restored=True, Sudden Death music win must NOT claim a life was won back
    no_life = await host_service.describe_cue("result", run=run, player=player, item_id="item-1")
    assert no_life is not None
    assert "won a life back" not in no_life.lower()

    fake_item["payload"]["strike_restored"] = True
    got_life = await host_service.describe_cue("result", run=run, player=player, item_id="item-1")
    assert got_life is not None
    assert "life back" in got_life.lower()


def test_tidy_line_strips_broken_here_we_opening_stub():
    assert host_service.tidy_line("[building] Here we... [announcing, big] Read It or Weep!") == "[announcing, big] Read It or Weep!"
    assert host_service.tidy_line("[building] Here we... Read It or Weep!") == "[building] Read It or Weep!"
    assert host_service.tidy_line("Here we… Read It or Weep!") == "Read It or Weep!"
    # Complete sentences remain untouched.
    assert host_service.tidy_line("[dramatic] Here we are at the final question.") == "[dramatic] Here we are at the final question."
    assert len(host_service._OPENING_STARTERS) >= 5
