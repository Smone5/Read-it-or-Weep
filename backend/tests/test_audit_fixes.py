"""Regression tests for security, gameplay fairness, showcraft, and ingestion audit fixes."""
from __future__ import annotations

import io
import struct
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

import security
from ingest import charts, generate, other_parsers, structure as struct_mod
from ingest.model import Block, ParsedDocument
from routers import admin as admin_router, play as play_router
from services import badges, bank, engine, host_service, jukebox, settings_store, stats


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


async def test_player_token_quiz_grants_and_access_version(monkeypatch):
    token = security.issue_player_token("player-1", quiz_grants=["quiz-a:2", "quiz-b:1"])
    pid, grants = security.read_player_claims(token)
    assert pid == "player-1"
    assert grants == {"quiz-a:2", "quiz-b:1"}

    quiz_v2 = {"id": "quiz-a", "access_code": "SECRET", "access_version": 2, "allowed_email_domains": []}
    quiz_v3 = {"id": "quiz-a", "access_code": "SECRET", "access_version": 3, "allowed_email_domains": []}
    player = {"id": "player-1", "email": "alice@example.com", "_quiz_grants": grants}
    impersonator = {"id": "player-1", "email": "alice@example.com", "_quiz_grants": set()}

    async def fake_fetchval(*args, **kwargs):
        return 1  # player has a DB quiz_memberships row

    monkeypatch.setattr(play_router.db, "fetchval", fake_fetchval)

    # Matching membership + matching version grant passes.
    assert await play_router.has_quiz_access(quiz_v2, player) is True
    # Bumping access_version invalidates older token grants immediately even if DB membership exists.
    assert await play_router.has_quiz_access(quiz_v3, player) is False
    # Token issued on another open quiz without this quiz's grant cannot impersonate a member.
    assert await play_router.has_quiz_access(quiz_v2, impersonator) is False


def test_client_ip_proxy_validation_and_failed_only_code_throttle():
    def _req(peer: str, xff: str | None = None):
        headers = {"x-forwarded-for": xff} if xff else {}
        return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)

    # Untrusted peer cannot spoof X-Forwarded-For.
    assert play_router._client_ip(_req("203.0.113.5", "198.51.100.99")) == "203.0.113.5"
    # Trusted local/private proxy resolves right-most untrusted hop.
    assert play_router._client_ip(_req("127.0.0.1", "198.51.100.1, 203.0.113.7")) == "203.0.113.7"

    req = _req("198.51.100.42")
    bucket = "test-failed-code-bucket"
    # Checking without failures never blocks (valid corporate NAT logins don't self-DoS).
    for _ in range(50):
        play_router.check_failed_code_throttle(req, bucket, limit=3)
    for _ in range(3):
        play_router.record_failed_code(req, bucket)
    with pytest.raises(HTTPException) as exc:
        play_router.check_failed_code_throttle(req, bucket, limit=3)
    assert exc.value.status_code == 429


def test_picture_mode_auto_respects_skill():
    rules_auto = {**settings_store.DEFAULTS["game"], "picture_mode": "auto"}
    assert engine._picture_mode(rules_auto, {"skill": "recall"}) == "memory"
    assert engine._picture_mode(rules_auto, {"skill": "understanding"}) == "show"
    assert engine._picture_mode({**rules_auto, "picture_mode": "show"}, {"skill": "recall"}) == "show"
    assert engine._picture_mode({**rules_auto, "picture_mode": "memory"}, {"skill": "understanding"}) == "memory"


def test_summary_badge_info_last_one_standing_has_no_lower_tier_hint():
    run = {
        "mode": "sudden_death",
        "status": "completed",
        "badge": "Last One Standing",
        "report_correct": 4,
        "report_answered": 4,
        "strikes": 0,
    }
    info = badges.summary_badge_info(run, settings_store.DEFAULTS["game"])
    assert info["label"] == "Last One Standing"
    assert info["tier"] == 6
    assert info["nextLabel"] is None
    assert "Top survival rank" in (info["nextHint"] or "")


def test_clean_game_clamps_malicious_or_out_of_range_numeric_settings():
    cleaned = settings_store.clean_game({
        "ready_threshold": 999,
        "show": {"questions": 500, "timer_sec": 1, "lifelines": {"fifty": -5, "peek": 99}},
        "sudden_death": {"strikes": 0, "timer_start_sec": 15, "timer_min_sec": 90},
        "speed": {"clock_sec": 10, "wrong_penalty_sec": 999},
        "straight": {"pass_mark": 0, "max_questions": 9999},
        "live": {"questions": 1, "timer_sec": 999, "head_start": -50},
    })
    assert cleaned["ready_threshold"] == 100
    assert cleaned["show"]["questions"] == 30
    assert cleaned["show"]["timer_sec"] == 10
    assert cleaned["show"]["lifelines"]["fifty"] == 0
    assert cleaned["show"]["lifelines"]["peek"] == 5
    assert cleaned["sudden_death"]["strikes"] == 1
    assert cleaned["sudden_death"]["timer_min_sec"] <= cleaned["sudden_death"]["timer_start_sec"]
    assert cleaned["speed"]["clock_sec"] == 30
    assert cleaned["speed"]["wrong_penalty_sec"] == 30
    assert cleaned["straight"]["pass_mark"] == 1
    assert cleaned["straight"]["max_questions"] == 100
    assert cleaned["live"]["questions"] == 3
    assert cleaned["live"]["timer_sec"] == 120
    assert cleaned["live"]["head_start"] == 0


def test_jukebox_song_or_jargon_falls_back_when_all_tracks_in_avoid():
    tracks = [
        {"id": f"t{i}", "artist": f"Artist {i}", "title": f"Song {i}", "preview_url": f"https://ex/{i}", "artwork_url": None, "track_url": None}
        for i in range(5)
    ]
    avoid = {t["id"] for t in tracks}
    item = jukebox.song_or_jargon_item(tracks, avoid, set(), prefer_song=True)
    assert item is not None
    assert item["track"] is not None


def test_host_quote_stripping_preserves_trailing_dialect_apostrophes():
    assert host_service.tidy_line("\"Well bless it, darlin'!\"") == "Well bless it, darlin'!"
    assert host_service.tidy_line("'Keep runnin''") == "Keep runnin'"
    assert host_service.spoken_text("Well darlin'") == "Well darlin'"


def test_tight_edit_crossfades_without_clicks_or_dropping_voice():
    editor = host_service.TightEdit(0.25)
    # 0.2s loud voice -> 0.9s silence -> 0.2s loud voice (24 kHz mono 16-bit PCM)
    loud = struct.pack("<" + "h" * 4800, *([12000] * 4800))
    silent = b"\x00" * (21600 * 2)
    out = editor.feed(loud + silent + loud) + editor.finish()
    assert len(out) > 0
    # Silence was trimmed down far shorter than the 0.9s input gap, while both loud segments remain.
    assert len(out) < len(loud + silent + loud)
    assert len(out) > len(loud) * 2


def test_mcq_problem_catches_title_leak_precision_tell_and_normalizes_numbers():
    passage = "Revenue grew to $184.0 million in Q3 while headcount reached 420 employees across four offices."
    title_leak = generate.MCQDraft(
        title="The 184 Million Quarter",
        question="What was total company revenue reported for Q3?",
        correct_answer="$184 million",
        distractors=["$120 million", "$210 million", "$95 million"],
        explanation="Revenue reached $184 million in Q3.",
        source_quote="Revenue grew to $184.0 million in Q3",
        difficulty="easy",
        skill="recall",
    )
    assert generate.mcq_problem(title_leak, passage) == "title gives away the answer"

    # Normalized numbers: 184 matches 184.0 in passage, so with a clean title it passes.
    clean = title_leak.model_copy(update={"title": "Q3 Top Line"})
    assert generate.mcq_problem(clean, passage) is None

    precision_tell = generate.MCQDraft(
        title="Margin Check",
        question="What was the exact operating margin reported in Q3?",
        correct_answer="14.7%",
        distractors=["10%", "15%", "20%"],
        explanation="Operating margin was 14.7%.",
        source_quote="Operating margin came in at 14.7% in Q3.",
        difficulty="medium",
        skill="recall",
    )
    assert generate.mcq_problem(precision_tell, "Operating margin came in at 14.7% in Q3.") == "correct option is conspicuously more precise"


def test_charts_renders_stacked_bar_and_numeric_scatter_pngs():
    stacked = charts.ChartData(
        kind="bar",
        title="Stacked Mix",
        series=[
            charts.Series("Online", ["Q1", "Q2"], [10.0, 20.0]),
            charts.Series("Store", ["Q1", "Q2"], [5.0, 8.0]),
        ],
        stacked=True,
    )
    png1 = stacked.render_png()
    assert png1 is not None and png1[:8] == b"\x89PNG\r\n\x1a\n"

    scatter = charts.ChartData(
        kind="scatter",
        title="Spend vs Growth",
        series=[charts.Series("Regions", ["1.5", "2.5", "4.0"], [12.0, 18.5, 29.0])],
    )
    png2 = scatter.render_png()
    assert png2 is not None and png2[:8] == b"\x89PNG\r\n\x1a\n"


def test_parse_text_preserves_markdown_tables_and_code_blocks_and_pptx_chart_no_duplicate_chunk():
    md = (
        "# Metrics\n\n"
        "| Quarter | Revenue |\n"
        "| --- | --- |\n"
        "| Q1 | $10M |\n"
        "| Q2 | $14M |\n\n"
        "```python\n"
        "def score(x):\n\n"
        "    return x * 2\n"
        "```\n"
    )
    parsed = other_parsers.parse_text(md.encode("utf-8"), "metrics.md")
    kinds = [b.kind for b in parsed.blocks]
    assert "table" in kinds
    code_blocks = [b for b in parsed.blocks if "def score" in b.text]
    assert len(code_blocks) == 1 and "return x * 2" in code_blocks[0].text

    # Native PPTX chart now renders a PNG AND structure keeps it as a single image (no duplicate text chart chunk).
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Sales Growth"
    chart_data = CategoryChartData()
    chart_data.categories = ["2024", "2025", "2026"]
    chart_data.add_series("Sales ($M)", (22.0, 31.0, 45.0))
    slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(2), Inches(6), Inches(4), chart_data)
    buf = io.BytesIO()
    prs.save(buf)
    pptx_doc = other_parsers.parse_pptx(buf.getvalue())
    chart_blocks = [b for b in pptx_doc.blocks if b.kind == "chart"]
    assert len(chart_blocks) == 1 and chart_blocks[0].image is not None
    structured = struct_mod.build_structure(pptx_doc, "deck.pptx")
    assert len(structured.images) == 1
    assert not any(c.kind == "chart" for c in structured.chunks)
