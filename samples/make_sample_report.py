"""Generate a realistic (fictional) business report as a Word document for testing.

    backend/.venv/bin/python samples/make_sample_report.py

Writes samples/out/Meridian_Q3_FY2026_Business_Review.docx with real heading styles,
bullet lists, tables, chart images, a diagram and a small repeated logo — the mix of
content a real report has, so ingestion and question generation can be exercised.
"""
from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from matplotlib.patches import FancyBboxPatch

OUT_DIR = Path(__file__).resolve().parent / "out"
NAVY, TEAL, AMBER, GREY, RED = "#1B2A4A", "#1F9E89", "#E8A33D", "#8892A0", "#C8553D"


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_revenue_by_region() -> io.BytesIO:
    regions = ["Southwest", "Midwest", "Southeast", "Northeast", "Pacific"]
    q3 = [52.6, 44.1, 38.9, 27.3, 21.3]
    q2 = [47.0, 43.2, 36.1, 26.8, 19.4]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    x = range(len(regions))
    ax.bar([i - 0.2 for i in x], q2, 0.4, label="Q2 FY2026", color=GREY)
    bars = ax.bar([i + 0.2 for i in x], q3, 0.4, label="Q3 FY2026", color=TEAL)
    for b, v in zip(bars, q3):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.8, f"{v:.1f}", ha="center", fontsize=9)
    ax.set_xticks(list(x), regions)
    ax.set_ylabel("Revenue ($ millions)")
    ax.set_title("Revenue by region, Q2 vs Q3 FY2026", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    return _png(fig)


def chart_on_time_delivery() -> io.BytesIO:
    months = ["Apr", "May", "Jun", "Jul", "Aug", "Sep"]
    otd = [93.1, 93.6, 94.7, 95.4, 96.0, 96.9]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.plot(months, otd, marker="o", color=NAVY, linewidth=2.2)
    ax.axhline(96.0, color=AMBER, linestyle="--", linewidth=1.4)
    ax.text(0.05, 96.12, "FY2026 target: 96.0%", color=AMBER, fontsize=9)
    for m, v in zip(months, otd):
        ax.text(m, v - 0.42, f"{v:.1f}%", ha="center", fontsize=9, color=NAVY)
    ax.set_ylim(92, 97.6)
    ax.set_ylabel("On-time delivery (%)")
    ax.set_title("Monthly on-time delivery, April–September 2026", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    return _png(fig)


def chart_nps_by_segment() -> io.BytesIO:
    segments = ["Retail", "Healthcare", "Industrial", "Food & Beverage", "E-commerce"]
    nps = [58, 64, 47, 52, 31]
    colors = [TEAL if v >= 50 else (AMBER if v >= 40 else RED) for v in nps]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    bars = ax.barh(segments[::-1], nps[::-1], color=colors[::-1])
    for b, v in zip(bars, nps[::-1]):
        ax.text(v + 1, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=10)
    ax.set_xlim(0, 75)
    ax.set_xlabel("Net Promoter Score")
    ax.set_title("Customer NPS by segment, Q3 FY2026", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    return _png(fig)


def chart_headcount_mix() -> io.BytesIO:
    labels = ["Drivers", "Warehouse", "Operations & planning", "Technology", "Corporate"]
    values = [1840, 1265, 410, 172, 133]
    fig, ax = plt.subplots(figsize=(6.2, 3.9))
    ax.pie(values, labels=[f"{l}\n{v:,}" for l, v in zip(labels, values)], startangle=90,
           colors=[NAVY, TEAL, AMBER, GREY, RED], wedgeprops={"width": 0.42, "edgecolor": "white"},
           textprops={"fontsize": 9})
    ax.set_title("Headcount by function, end of Q3 FY2026 (3,820 total)", fontweight="bold", color=NAVY)
    return _png(fig)


def diagram_northstar_phases() -> io.BytesIO:
    phases = [
        ("Phase 1", "Route planning\n(RouteIQ)", "Live since\nJune 9", TEAL),
        ("Phase 2", "Dock scheduling", "Pilot in Phoenix\nand Columbus", AMBER),
        ("Phase 3", "Customer portal", "Go-live\nJanuary 2027", GREY),
        ("Phase 4", "Predictive\nmaintenance", "Go-live\nApril 2027", GREY),
    ]
    fig, ax = plt.subplots(figsize=(7.6, 2.7))
    ax.set_xlim(0, 4.05)
    ax.set_ylim(0, 1)
    ax.axis("off")
    for i, (name, what, when, color) in enumerate(phases):
        ax.add_patch(FancyBboxPatch((i + 0.06, 0.2), 0.84, 0.62, boxstyle="round,pad=0.02",
                                    facecolor=color, edgecolor="none", alpha=0.95))
        ax.text(i + 0.48, 0.71, name, ha="center", va="center", color="white", fontweight="bold", fontsize=11)
        ax.text(i + 0.48, 0.52, what, ha="center", va="center", color="white", fontsize=9)
        ax.text(i + 0.48, 0.31, when, ha="center", va="center", color="white", fontsize=8, style="italic")
        if i < len(phases) - 1:
            ax.annotate("", xy=(i + 1.05, 0.51), xytext=(i + 0.92, 0.51),
                        arrowprops={"arrowstyle": "->", "color": NAVY, "lw": 1.8})
    ax.text(2.02, 0.95, "Project Northstar rollout", ha="center", fontweight="bold", color=NAVY, fontsize=12)
    return _png(fig)


def tiny_logo() -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(0.9, 0.3))
    ax.axis("off")
    ax.text(0.5, 0.5, "MERIDIAN", ha="center", va="center", fontsize=8, fontweight="bold", color=NAVY)
    return _png(fig)


def add_table(doc: Document, header: list[str], rows: list[list[str]], caption: str) -> None:
    cap = doc.add_paragraph(caption, style="Caption")
    cap.alignment = WD_ALIGN_PARAGRAPH.LEFT
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    for i, h in enumerate(header):
        cell = table.rows[0].cells[i]
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.font.bold = True
    for row in rows:
        cells = table.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = v
    doc.add_paragraph()


def add_figure(doc: Document, image: io.BytesIO, caption: str, width: float = 6.0) -> None:
    doc.add_picture(image, width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(caption, style="Caption")


def bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


def build() -> Path:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.core_properties.title = "Meridian Logistics – Q3 FY2026 Business Review"
    doc.core_properties.author = "Office of the COO"

    logo = tiny_logo()
    doc.add_picture(logo, width=Inches(1.1))
    title = doc.add_paragraph("Meridian Logistics", style="Title")
    title.runs[0].font.color.rgb = RGBColor(0x1B, 0x2A, 0x4A)
    doc.add_paragraph("Q3 FY2026 Business Review", style="Subtitle")
    doc.add_paragraph("Prepared for the Quarterly Leadership Meeting, October 22, 2026. "
                      "Fiscal Q3 covers July 1 through September 30, 2026. Internal – do not distribute.")

    # ------------------------------------------------------------------ 1
    doc.add_heading("1. Executive Summary", level=1)
    doc.add_paragraph(
        "Meridian closed the third quarter of fiscal 2026 with revenue of $184.2 million, up 11.4% from "
        "$165.3 million in the same quarter last year and 6.7% above the second quarter. Operating margin "
        "reached 8.9%, our highest since the company was founded in 2009, helped by better route density "
        "and a 4.1% drop in fuel cost per mile.")
    doc.add_paragraph(
        "The quarter's defining event was the opening of the Phoenix cross-dock on August 4. In its first "
        "eight weeks the facility handled 38,000 pallets and allowed us to retire two leased overflow "
        "sites in Tempe and Glendale. On-time delivery finished September at 96.9%, beating our full-year "
        "target of 96.0% for the first time.")
    doc.add_paragraph("Three points need the leadership team's attention in this meeting:")
    bullets(doc, [
        "E-commerce customers remain our least satisfied segment, with a Net Promoter Score of 31.",
        "Driver turnover rose to 27% annualized, above our ceiling of 22%.",
        "Project Northstar Phase 2 (dock scheduling) is six weeks behind plan and needs a decision on "
        "whether to add a second pilot team.",
    ])

    # ------------------------------------------------------------------ 2
    doc.add_heading("2. Financial Performance", level=1)
    doc.add_heading("2.1 Revenue", level=2)
    doc.add_paragraph(
        "Revenue growth was broad-based, but the Southwest region contributed the most new dollars, "
        "rising from $47.0 million in Q2 to $52.6 million in Q3 as Phoenix came online. The Pacific region "
        "is still our smallest at $21.3 million, although it grew fastest in percentage terms at 9.8% "
        "quarter over quarter.")
    add_figure(doc, chart_revenue_by_region(), "Figure 1. Revenue by region, Q2 versus Q3 FY2026 ($ millions).")
    doc.add_paragraph(
        "Contract logistics now represents 41% of revenue, up from 36% a year ago. Spot freight fell to "
        "18% of revenue, which is deliberate: we have been declining low-margin spot loads since March.")

    doc.add_heading("2.2 Profitability and cost", level=2)
    add_table(doc, ["Metric", "Q3 FY2025", "Q2 FY2026", "Q3 FY2026"], [
        ["Revenue ($M)", "165.3", "172.6", "184.2"],
        ["Gross margin", "21.8%", "22.9%", "24.1%"],
        ["Operating margin", "6.2%", "7.7%", "8.9%"],
        ["Fuel cost per mile", "$0.71", "$0.73", "$0.70"],
        ["Cost per delivered pallet", "$41.20", "$39.85", "$38.10"],
        ["Free cash flow ($M)", "9.4", "11.2", "14.8"],
    ], "Table 1. Key financial metrics.")
    doc.add_paragraph(
        "Cost per delivered pallet fell to $38.10, a 4.4% improvement on the prior quarter. Roughly "
        "two-thirds of that gain came from RouteIQ, the route-planning tool delivered in Phase 1 of "
        "Project Northstar; the rest came from closing the two leased overflow sites. Free cash flow of "
        "$14.8 million funded the Phoenix fit-out without drawing on the revolving credit facility.")
    doc.add_paragraph(
        "Capital expenditure for the quarter was $22.5 million, of which $13.0 million was Phoenix. "
        "Full-year capital expenditure guidance is unchanged at $68 million.")

    # ------------------------------------------------------------------ 3
    doc.add_heading("3. Operations", level=1)
    doc.add_heading("3.1 Service levels", level=2)
    doc.add_paragraph(
        "On-time delivery improved every month of the quarter. The September result of 96.9% is the "
        "first month above the 96.0% target since the target was raised from 94.5% in January.")
    add_figure(doc, chart_on_time_delivery(), "Figure 2. Monthly on-time delivery against the FY2026 target.")
    doc.add_paragraph(
        "Damage claims fell to 0.31% of shipments from 0.44% in Q2. The largest single contributor was "
        "the new pallet-wrap standard introduced at the Columbus hub in July, which cut damage on mixed "
        "loads by nearly half.")

    doc.add_heading("3.2 Network and facilities", level=2)
    add_table(doc, ["Facility", "Type", "Pallets handled (Q3)", "Utilization", "Notes"], [
        ["Columbus, OH", "Hub", "212,000", "91%", "New pallet-wrap standard since July"],
        ["Dallas, TX", "Hub", "187,500", "88%", "Third shift added September 14"],
        ["Atlanta, GA", "Hub", "164,300", "84%", "Roof repair completed"],
        ["Phoenix, AZ", "Cross-dock", "38,000", "62%", "Opened August 4"],
        ["Reno, NV", "Spoke", "41,700", "97%", "At capacity; expansion under review"],
    ], "Table 2. Facility throughput and utilization, Q3 FY2026.")
    doc.add_paragraph(
        "Reno is the constraint in the western network. At 97% utilization it cannot absorb peak-season "
        "volume, and we expect to divert about 6,000 pallets to Phoenix in November and December. A "
        "proposal to expand Reno by 40,000 square feet, at an estimated cost of $9.2 million, will come "
        "to the capital committee in December.")

    doc.add_heading("3.3 Safety", level=2)
    doc.add_paragraph(
        "The recordable incident rate was 2.4 per 200,000 hours worked, down from 3.1 in Q2. There were "
        "no lost-time incidents at Phoenix during its start-up period. Forklift near-miss reports rose "
        "to 118 from 74, which we read as a positive sign of reporting culture rather than added risk, "
        "since the new near-miss app launched in July made reporting much easier.")

    # ------------------------------------------------------------------ 4
    doc.add_heading("4. Customers", level=1)
    doc.add_paragraph(
        "Overall Net Promoter Score rose three points to 52. Healthcare customers are our strongest "
        "advocates at 64, reflecting the temperature-controlled lanes launched last year. E-commerce "
        "remains the outlier at 31.")
    add_figure(doc, chart_nps_by_segment(), "Figure 3. Net Promoter Score by customer segment.")
    doc.add_paragraph(
        "Interviews with fourteen e-commerce accounts point to one dominant complaint: delivery windows "
        "that are too wide. These customers want a two-hour window; we currently quote four hours. The "
        "customer portal planned for Northstar Phase 3 is intended to close this gap by showing live "
        "arrival estimates.")
    doc.add_heading("4.1 Wins and losses", level=2)
    bullets(doc, [
        "Won: Halvorsen Medical Supply, a five-year contract worth $31 million, starting November 1.",
        "Won: Brightline Grocers' Southwest distribution, worth about $12 million a year.",
        "Lost: Kettering Outdoor moved its Northeast volume, about $4.5 million a year, to a competitor "
        "citing price.",
        "Renewed: Castellan Industrial for three years with a 3.5% rate increase.",
    ])
    doc.add_paragraph(
        "Customer concentration improved slightly. Our top ten customers now account for 38% of revenue, "
        "down from 42% a year ago, and no single customer exceeds 7%.")

    # ------------------------------------------------------------------ 5
    doc.add_heading("5. People", level=1)
    doc.add_paragraph(
        "Headcount ended the quarter at 3,820, up 146 from the end of Q2, with most of the increase in "
        "warehouse roles for Phoenix.")
    add_figure(doc, chart_headcount_mix(), "Figure 4. Headcount by function at the end of Q3 FY2026.", width=5.0)
    doc.add_paragraph(
        "Driver turnover is the main people risk. Annualized turnover reached 27% against a ceiling of "
        "22%. Exit interviews cite schedule predictability more often than pay. In response we will "
        "pilot fixed weekly routes for 120 drivers in the Midwest region starting November 3, and we "
        "raised the referral bonus from $1,500 to $2,500 on September 1.")
    doc.add_paragraph(
        "Employee engagement, measured in the August pulse survey, was 74%, up two points. Participation "
        "was 81%. The lowest-scoring item was 'I have the tools I need to do my job' among warehouse "
        "staff, which is consistent with the delays to dock scheduling described in section 6.")

    # ------------------------------------------------------------------ 6
    doc.add_heading("6. Project Northstar", level=1)
    doc.add_paragraph(
        "Project Northstar is our four-phase program to replace the legacy transport management system. "
        "The total approved budget is $24 million over three years; $9.6 million has been spent to date.")
    add_figure(doc, diagram_northstar_phases(), "Figure 5. Project Northstar phases and status.", width=6.3)
    doc.add_paragraph(
        "Phase 1, the RouteIQ route-planning tool, has been live across all regions since June 9 and is "
        "now planning 94% of daily routes. Planners override it on roughly 6% of routes, mostly for "
        "customer-specific delivery restrictions the tool does not yet model.")
    doc.add_paragraph(
        "Phase 2, dock scheduling, is being piloted in Phoenix and Columbus and is six weeks behind "
        "plan. The cause is integration with the yard-management vendor, whose interface changed in "
        "August. The program office recommends adding a second pilot team at a cost of $380,000 to "
        "recover four of the six weeks. Without it, Phase 2 completes in March 2027 instead of January.")
    doc.add_paragraph(
        "Phase 3, the customer portal, is scheduled to go live in January 2027, and Phase 4, predictive "
        "maintenance for the tractor fleet, in April 2027. Phase 3 depends on Phase 2 data, so a slip in "
        "Phase 2 puts the customer portal date at risk.")

    # ------------------------------------------------------------------ 7
    doc.add_heading("7. Risks", level=1)
    add_table(doc, ["Risk", "Likelihood", "Impact", "Owner", "Mitigation"], [
        ["Driver turnover stays above 22%", "High", "High", "Priya Raman (CHRO)",
         "Fixed-route pilot; higher referral bonus"],
        ["Northstar Phase 2 slips to March", "Medium", "High", "Daniel Okafor (CIO)",
         "Second pilot team ($380,000)"],
        ["Reno cannot absorb peak volume", "High", "Medium", "Marta Villanueva (COO)",
         "Divert 6,000 pallets to Phoenix"],
        ["Diesel price rises above $4.50 per gallon", "Medium", "Medium", "Tom Ashby (CFO)",
         "Fuel surcharge clause covers 80% of contracts"],
        ["Loss of a top-ten customer", "Low", "High", "Elena Brooks (CCO)",
         "Quarterly executive reviews with each account"],
    ], "Table 3. Top risks and owners.")
    doc.add_paragraph(
        "Diesel averaged $3.92 per gallon in the quarter. Our fuel surcharge clause covers 80% of "
        "contracted volume, so a sustained price above $4.50 would mainly hurt the remaining 20% and "
        "our spot business.")

    # ------------------------------------------------------------------ 8
    doc.add_heading("8. Outlook and Decisions Requested", level=1)
    doc.add_paragraph(
        "We are raising full-year revenue guidance to a range of $705 million to $715 million, from "
        "$690 million to $705 million. Operating margin guidance for the year moves up to 8.2%, from "
        "7.8%. The fourth quarter includes peak season, when volume typically runs 18% above the "
        "third quarter.")
    doc.add_paragraph("The leadership team is asked to decide the following on October 22:")
    bullets(doc, [
        "Approve $380,000 for a second Northstar Phase 2 pilot team.",
        "Endorse the fixed-route pilot for 120 Midwest drivers starting November 3.",
        "Agree to bring the $9.2 million Reno expansion to the capital committee in December.",
        "Confirm that the e-commerce two-hour delivery window becomes a 2027 commitment.",
    ])
    doc.add_paragraph("The next quarterly review is scheduled for January 28, 2027.")

    # Repeated decorative logo at the end, the way real templates do.
    doc.add_picture(tiny_logo(), width=Inches(1.1))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "Meridian_Q3_FY2026_Business_Review.docx"
    doc.save(out)
    return out


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
