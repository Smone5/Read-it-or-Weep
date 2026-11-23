"""Reading a Word report: sections, tables, pictures and the checks that keep questions honest."""
import io
import sys
from pathlib import Path

import pytest

from ingest import generate
from ingest.charts import ChartData, Series
from ingest.docx_parser import parse_docx
from ingest.structure import build_structure

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """The sample report, built fresh (it is generated, not stored in the repository)."""
    sys.path.insert(0, str(SAMPLES))
    try:
        import make_sample_report
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"sample report generator not available: {exc}")
    path = make_sample_report.build()                      # written under samples/out, which is not kept in git
    return build_structure(parse_docx(Path(path).read_bytes()))


def test_sections_follow_the_report_s_headings(report):
    headings = [section.heading for section in report.sections]
    assert len(headings) == 8
    assert headings[0].startswith("1.") and "Executive Summary" in headings[0]


def test_tables_and_pictures_are_kept(report):
    kinds = {chunk.kind for chunk in report.chunks}
    assert "table" in kinds and "text" in kinds
    assert len(report.images) == 5                         # four charts and a diagram; the repeated logo is dropped
    assert all(image.width >= 300 for image in report.images)


def test_passages_are_a_readable_size(report):
    assert all(chunk.token_count <= 560 for chunk in report.chunks)
    assert all(chunk.text.strip() for chunk in report.chunks)


# --- the checks every drafted question must pass before a second model even sees it -------------------

def test_a_quote_must_really_be_in_the_passage():
    passage = "Revenue rose 6.1% to $184.2 million, driven by the Phoenix cross-dock."
    assert generate.quote_is_grounded("Revenue rose 6.1% to $184.2 million", passage)
    assert not generate.quote_is_grounded("Revenue fell 6.1% to $148.2 million", passage)


def test_mcq_problem_catches_conspicuously_longer_correct_answer():
    draft = generate.MCQDraft(
        title="Length tell",
        question="Which initiative drove the third-quarter margin improvement?",
        correct_answer="Consolidating regional freight hubs into the Phoenix cross-dock facility",
        distractors=["Raising fuel surcharges", "Cutting night shifts", "Pausing new leases"],
        explanation="The Phoenix cross-dock drove the margin gain.",
        source_quote="driven by the Phoenix cross-dock",
        difficulty="medium",
        skill="understanding",
    )
    assert generate.mcq_problem(draft, None) == "correct option is conspicuously longer"


# --- charts drawn from a Word chart's own numbers ------------------------------------------------------

def test_native_charts_are_drawn_large_enough_to_read_on_a_phone():
    from PIL import Image

    chart = ChartData(kind="bar", title="Revenue by region", value_axis="USD millions",
                      series=[Series(name="Q3", categories=["North", "South", "East", "West"], values=[52.1, 41.7, 48.9, 41.5])])
    image = Image.open(io.BytesIO(chart.render_png()))
    assert image.width >= 800 and image.height >= 550      # squarer than a page chart, with bigger lettering
    assert "North = 52.1" in chart.as_text()


def test_real_world_pdf_hardening_helpers():
    from ingest.model import Block, ParsedDocument
    from ingest.other_parsers import _clean_pdf_title, _looks_like_heading

    assert _clean_pdf_title("printmgr file") is None
    assert _clean_pdf_title("Microsoft Word - Q3_Final.docx") is None
    assert _clean_pdf_title("2024 Annual Report") == "2024 Annual Report"
    assert not _looks_like_heading("($Billions")
    assert not _looks_like_heading("https://doi.org/10.6028/NIST.AMS.600-16")
    assert _looks_like_heading("US Manufacturing Supply Chain")

    # Giant cover-page-collapsed section splits cleanly into real chapters, drops TOC/References, merges duplicates.
    filler = " ".join(["revenue"] * 450)
    parsed = ParsedDocument(title="Annual Report", blocks=[
        Block(kind="heading", text="Cover Title", level=1),
        Block(kind="heading", text="Table of Contents", level=4),
        Block(kind="paragraph", text="Executive Summary ... 1"),
        Block(kind="heading", text="Executive Summary", level=4),
        Block(kind="paragraph", text=filler),
        Block(kind="heading", text="Supply Chain", level=4),
        Block(kind="paragraph", text=filler),
        Block(kind="heading", text="Supply Chain", level=4),
        Block(kind="paragraph", text=filler),
        Block(kind="heading", text="References", level=4),
        Block(kind="paragraph", text=filler),
    ])
    structured = build_structure(parsed, "annual.pdf")
    headings = [s.heading for s in structured.sections]
    assert "Executive Summary" in headings and "Supply Chain" in headings
    assert headings.count("Supply Chain") == 1
    assert "Table of Contents" not in headings and "References" not in headings


def test_build_structure_strips_nul_bytes_and_control_chars():
    from ingest.model import Block, ParsedDocument
    from ingest.structure import sanitize_text

    assert sanitize_text("alpha\x00beta\x01\x10\ngamma\t\ud800delta") == "alphabeta\ngamma\tdelta"

    parsed = ParsedDocument(
        title="LaTeX\x00 Paper",
        blocks=[
            Block(kind="heading", text="2.2 Trajectory\x00 Debiasing", level=1),
            Block(kind="paragraph", text="State x = \x00B, M\x01 where M is memory."),
            Block(kind="table", text="| Col\x00 |\n| --- |\n| Val\x01 |", caption="Table\x00 1"),
        ],
        warnings=["Note\x00 warning"],
    )
    structured = build_structure(parsed, "paper.pdf")
    assert "\x00" not in structured.title
    assert all("\x00" not in s.heading and "\x01" not in s.heading for s in structured.sections)
    assert all("\x00" not in c.text and "\x01" not in c.text and "\x00" not in c.heading_path for c in structured.chunks)
    assert all("\x00" not in w for w in structured.warnings)


def test_pdf_parser_preserves_whitespace_spans_and_strips_nul():
    import pymupdf
    from ingest.other_parsers import parse_pdf

    doc = pymupdf.open()
    page = doc.new_page()
    # Emit words separated by explicit space glyphs plus a formula-style line
    page.insert_text((72, 72), "Introduction", fontsize=18)
    page.insert_text((72, 110), "Gameplay data exhibit two biases that limit control learning.", fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()

    parsed = parse_pdf(pdf_bytes, "test.pdf")
    combined = " ".join(b.text for b in parsed.blocks)
    assert "Gameplay data exhibit two biases" in combined
    assert "\x00" not in combined


async def test_large_document_triage_triviality_filter_and_curation():
    from ingest.model import Chunk, ImageItem, Section, StructuredDocument

    # Build a synthetic 50-page-style document with 10 core sections + 2 appendix sections (140 chunks, 60 images)
    sections = [Section(ordinal=i, level=1, heading=f"{i + 1}. Core Results & Architecture {i + 1}", word_count=1200) for i in range(10)]
    sections += [
        Section(ordinal=10, level=1, heading="Appendix A: Hyperparameter Details", word_count=2000),
        Section(ordinal=11, level=1, heading="Appendix B: Related Work & Citations", word_count=2000),
    ]
    prose = " ".join(["architecture", "tradeoff", "memory", "training", "scaling"] * 45)
    chunks = []
    for sec in sections:
        for j in range(12):
            chunks.append(Chunk(
                ordinal=len(chunks),
                section=sec.ordinal,
                kind="text",
                heading_path=sec.heading,
                text=prose,
                token_count=260,
            ))
    images = []
    for i in range(60):
        sec_idx = i % 12
        is_chart = i in (1, 13, 25)
        images.append(ImageItem(
            ordinal=i,
            section=sec_idx,
            data=b"x",
            mime="image/png",
            width=800 if is_chart else 400,
            height=500 if is_chart else 250,
            sha256=f"h{i}",
            origin="chart" if is_chart else "embedded",
            caption=f"Figure {i}: Scaling results" if is_chart else None,
            context="Section overview",
        ))
    doc = StructuredDocument(title="Large Research Paper", sections=sections, chunks=chunks, images=images, warnings=[], word_count=20000)

    picked_chunks = generate.select_chunks(doc)
    assert len(picked_chunks) == generate.MAX_CHUNKS_PER_PASS
    # Every core section (0..9) gets coverage before appendix sections dominate
    picked_sections = {doc.chunks[i].section for i in picked_chunks}
    assert set(range(10)).issubset(picked_sections)
    assert sum(doc.chunks[i].section >= 10 for i in picked_chunks) == 0

    picked_images = generate.select_images(doc)
    assert len(picked_images) == generate.MAX_IMAGES_PER_PASS
    assert {1, 13, 25}.issubset({img.ordinal for img in picked_images})

    # Trivial or low-importance questions are rejected by _accept_mcqs
    report = generate.GenerationReport()
    d = generate.MCQDraft(
        title="Minor detail",
        question="What batch size was listed in Appendix Table 12?",
        correct_answer="128",
        distractors=["64", "256", "512"],
        source_quote="batch size 128",
        explanation="The batch size was 128.",
        skill="recall",
        difficulty="hard",
    )
    accepted = generate._accept_mcqs(
        [(d, ["128", "64", "256", "512"], 0), (d, ["128", "64", "256", "512"], 0), (d, ["128", "64", "256", "512"], 0)],
        [
            generate.MCQCheck(number=1, answer_index=0, importance=2, problems=[]),
            generate.MCQCheck(number=2, answer_index=0, importance=4, problems=["trivial"]),
            generate.MCQCheck(number=3, answer_index=0, importance=5, problems=[]),
        ],
        report,
    )
    assert len(accepted) == 1
    assert accepted[0][1]["importance"] == 5

    # Curate 100 candidate MCQs + 40 statements down to focused caps with section balance
    candidates = []
    for i in range(100):
        candidates.append(generate.ReadyQuestion(
            kind="mcq",
            source_type="image" if i < 15 else "text",
            section_index=i % 10,
            question_text=f"Question {i}?",
            options=["A", "B", "C", "D"],
            correct_index=0,
            skill="understanding" if i % 2 == 0 else "recall",
            difficulty="medium",
            quality={"checked": True, "importance": 5 if i % 3 == 0 else 3},
        ))
    for i in range(40):
        candidates.append(generate.ReadyQuestion(
            kind="fact_fake",
            source_type="text",
            section_index=i % 10,
            question_text=f"Statement {i}.",
            is_true=(i % 2 == 0),
            quality={"checked": True, "importance": 4},
        ))
    curated = await generate.curate_bank(doc, candidates, report, use_editor=False)
    kept_mcqs = [q for q in curated if q.kind == "mcq"]
    kept_stmts = [q for q in curated if q.kind == "fact_fake"]
    assert len(kept_mcqs) == generate.DEFAULT_MAX_MCQS
    assert len(kept_stmts) == generate.DEFAULT_MAX_STATEMENTS
    assert sum(q.source_type == "image" for q in kept_mcqs) <= generate.MAX_IMAGE_MCQS
    assert {q.section_index for q in kept_mcqs} == set(range(10))


async def test_agentic_critique_revise_and_closed_book_hardening(monkeypatch):
    import numpy as np
    from ingest.model import Chunk, Section, StructuredDocument

    sections = [
        Section(ordinal=0, level=1, heading="1. Architecture", word_count=400),
        Section(ordinal=1, level=1, heading="2. Results", word_count=400),
    ]
    chunks = [
        Chunk(
            ordinal=0, section=0, kind="text", heading_path="1. Architecture",
            text="The Phoenix cross-dock eliminated manual pallet re-wrapping by introducing automated shrink sleeves, cutting dwell time by 38%.",
            token_count=90,
        ),
        Chunk(
            ordinal=1, section=1, kind="text", heading_path="2. Results",
            text="Regional margins climbed from 11.4% to 14.7% after Phoenix dwell time dropped by 38%, while Dallas stayed flat at 10.9%.",
            token_count=90,
        ),
    ]
    doc = StructuredDocument(title="Q3 Operations Review", sections=sections, chunks=chunks, images=[], warnings=[], word_count=800)
    embs = np.asarray([[1.0, 0.0], [0.6, 0.8]], dtype=np.float32)

    # 1. _split_checked_mcqs routes fixable high-importance flaw to reviser, rejects trivial immediately
    flawed = generate.MCQDraft(
        title="Dwell drop",
        question="Why did the Phoenix cross-dock cut dwell time by 38%?",
        correct_answer="Automated shrink sleeves replaced manual re-wrapping",
        distractors=[
            "Warehouse staff stopped taking any afternoon coffee breaks",
            "Drivers were told to drive twice as fast on the highway",
            "Regional managers decided to ignore all incoming shipments",
        ],
        source_quote="eliminated manual pallet re-wrapping by introducing automated shrink sleeves, cutting dwell time by 38%",
        explanation="Automated shrink sleeves replaced manual pallet re-wrapping.",
        skill="understanding",
        difficulty="medium",
    )
    hardened = generate.MCQDraft(
        title="Sleeve speed",
        question="What operational change cut dwell time by 38% at the Phoenix cross-dock?",
        correct_answer="Automated shrink sleeves replaced manual pallet re-wrapping",
        distractors=[
            "Overnight rail shuttles replaced regional truck relays",
            "Cross-dock staging shifted from Phoenix to the Dallas hub",
            "Double-deck trailer loading replaced single-pallet docks",
        ],
        source_quote="eliminated manual pallet re-wrapping by introducing automated shrink sleeves, cutting dwell time by 38%",
        explanation="Automated shrink sleeves eliminated manual pallet re-wrapping and cut dwell time by 38%.",
        skill="understanding",
        difficulty="medium",
    )

    rep = generate.GenerationReport()
    accepted, fixable = generate._split_checked_mcqs(
        [(flawed, ["A", "B", "C", "D"], 0), (flawed, ["A", "B", "C", "D"], 0)],
        [
            generate.MCQCheck(number=1, answer_index=0, importance=5, problems=["guessable"], critique="Replace joke distractors with real operations alternatives."),
            generate.MCQCheck(number=2, answer_index=0, importance=2, problems=["trivial"], critique=""),
        ],
        rep,
    )
    assert accepted == []
    assert len(fixable) == 1
    assert "joke distractors" in fixable[0][1]
    assert rep.rejected.get("checker flagged: trivial") == 1

    # 2. _verify_and_revise runs Checker -> Reviser -> Re-Verify and marks revised=True
    calls = []

    async def fake_parse(*, model, system, user, schema, **kwargs):
        calls.append(schema.__name__)
        if schema is generate.CheckResult:
            # First check flags guessable; second check (post-revision) passes!
            if calls.count("CheckResult") == 1:
                return generate.CheckResult(
                    questions=[generate.MCQCheck(number=1, answer_index=0, importance=5, problems=["guessable"], critique="Distractors are silly.")],
                    statements=[],
                )
            # Find which shuffled index holds the correct answer in the prompt text
            idx = 0
            for line in user.splitlines():
                if "Automated shrink sleeves" in line and "[" in line:
                    idx = int(line.split("[")[1].split("]")[0])
                    break
            return generate.CheckResult(
                questions=[generate.MCQCheck(number=1, answer_index=idx, importance=5, problems=[])],
                statements=[],
            )
        if schema is generate.RevisedMCQs:
            return generate.RevisedMCQs(questions=[hardened])
        if schema is generate.BlindTestResult:
            # Find the index of "Automated shrink sleeves" and claim high-confidence common-sense crack!
            idx = 0
            for line in user.splitlines():
                if "Automated shrink sleeves" in line and "[" in line:
                    idx = int(line.split("[")[1].split("]")[0])
                    break
            return generate.BlindTestResult(
                attempts=[generate.BlindAttempt(number=1, guess_index=idx, confidence="high", reason="option_pattern", clue="Distractors looked unrealistic.")]
            )
        raise AssertionError(f"unexpected schema {schema}")

    monkeypatch.setattr(generate.llm, "parse", fake_parse)

    ready = await generate._verify_and_revise(
        doc, chunks[0], [chunks[1]], [flawed], [], generate.GenerationReport(), primary_index=0, allow_revise=True,
    )
    assert len(ready) == 1
    assert ready[0].quality.get("revised") is True
    assert "RevisedMCQs" in calls

    # 3. _closed_book_stress_test detects a closed-book crack and hardens it via Reviser + Checker
    stressed = await generate._closed_book_stress_test(doc, embs, ready, generate.GenerationReport())
    assert len(stressed) == 1
    assert stressed[0].quality.get("hardened") is True


async def test_picture_round_blocks_screenshots_appendix_galleries_and_eye_tests(monkeypatch):
    from ingest.model import Chunk, ImageItem, Section, StructuredDocument

    sections = [
        Section(ordinal=0, level=1, heading="2. Scaling Results", word_count=600),
        Section(ordinal=1, level=1, heading="E Rollout Visualization and Qualitative Case Study", word_count=400),
    ]
    chunks = [
        Chunk(ordinal=0, section=0, kind="text", heading_path="2. Scaling Results",
              text="Diverse game mixtures continue lowering validation loss past 800 hours whereas single-game runs plateau early.",
              token_count=90),
    ]
    core_chart = ImageItem(
        ordinal=0, section=0, data=b"png", mime="image/png", width=900, height=600, sha256="c0",
        origin="chart", caption="Figure 4: Scaling loss across mixtures", context="Scaling results",
    )
    appendix_shot = ImageItem(
        ordinal=1, section=1, data=b"png", mime="image/png", width=900, height=600, sha256="s1",
        origin="embedded", caption=None, context="Gameplay rollout frame",
    )
    doc = StructuredDocument(title="GameScaling", sections=sections, chunks=chunks,
                             images=[core_chart, appendix_shot], warnings=[], word_count=1000)

    # Appendix rollout gallery image is excluded when core visuals exist
    picked = generate.select_images(doc)
    assert [img.ordinal for img in picked] == [0]

    # Raw screenshot never yields Picture Round questions, even if the model returned one
    eye_test_draft = generate.MCQDraft(
        title="Party of how many?",
        question="How many party character portraits are stacked along the right edge of the gameplay screen?",
        correct_answer="4",
        distractors=["3", "5", "6"],
        source_quote="Four portraits on the right",
        explanation="Four portraits appear on the right.",
        skill="recall",
        difficulty="easy",
    )

    async def fake_screenshot_parse(*, schema, **kwargs):
        if schema is generate.ImageDrafts:
            return generate.ImageDrafts(kind="screenshot", informative=True, description="Gameplay screenshot.", questions=[eye_test_draft])
        raise AssertionError("checker should not run for rejected screenshot")

    monkeypatch.setattr(generate.llm, "parse", fake_screenshot_parse)
    rep1 = generate.GenerationReport()
    qs1, note1 = await generate._questions_for_image(doc, appendix_shot, rep1)
    assert qs1 == []
    assert note1[0] == "screenshot" and note1[2] is True
    assert rep1.rejected.get("skipped non-analytical visual (screenshot/photo)") == 1

    # Even on a diagram, an "I Spy" counting question is blocked by the deterministic eye-test guard
    async def fake_eye_test_parse(*, schema, **kwargs):
        if schema is generate.ImageDrafts:
            return generate.ImageDrafts(kind="diagram", informative=True, description="System diagram.", questions=[eye_test_draft])
        raise AssertionError("checker should not run after eye-test rejection")

    monkeypatch.setattr(generate.llm, "parse", fake_eye_test_parse)
    rep2 = generate.GenerationReport()
    qs2, _ = await generate._questions_for_image(doc, core_chart, rep2)
    assert qs2 == []
    assert any("eye-test" in k for k in rep2.rejected)




