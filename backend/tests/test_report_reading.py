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
