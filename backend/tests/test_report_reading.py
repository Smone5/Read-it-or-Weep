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
    out = tmp_path_factory.mktemp("report") / "sample.docx"
    builder = getattr(make_sample_report, "build", None) or getattr(make_sample_report, "main", None)
    if builder is None:
        pytest.skip("sample report generator has no build() or main()")
    try:
        builder(out)
    except TypeError:
        pytest.skip("sample report generator takes no output path")
    return build_structure(parse_docx(out.read_bytes()))


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


# --- charts drawn from a Word chart's own numbers ------------------------------------------------------

def test_native_charts_are_drawn_large_enough_to_read_on_a_phone():
    from PIL import Image

    chart = ChartData(kind="bar", title="Revenue by region", value_axis="USD millions",
                      series=[Series(name="Q3", categories=["North", "South", "East", "West"], values=[52.1, 41.7, 48.9, 41.5])])
    image = Image.open(io.BytesIO(chart.render_png()))
    assert image.width >= 800 and image.height >= 550      # squarer than a page chart, with bigger lettering
    assert "North = 52.1" in chart.as_text()
