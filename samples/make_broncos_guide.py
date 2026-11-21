"""Generate the dedicated, license-safe Denver Broncos game-day staff & stadium wayfinding guide (.docx).

100% original text + 4 custom matplotlib architectural/wayfinding/history graphics.
Zero trademarked logos or copyrighted photography.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
from docx import Document
from docx.shared import Inches

OUT = Path(__file__).resolve().parent / "out"
CHARTS = OUT / "_charts_broncos"
OUT.mkdir(parents=True, exist_ok=True)
CHARTS.mkdir(parents=True, exist_ok=True)

NAVY = "#0D2240"
ORANGE = "#FB4F14"
STEEL = "#3B6EA5"
MINT = "#10B981"
GOLD = "#F59E0B"
SLATE = "#475569"
BG = "#F8FAFC"


def _add_table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    tbl = doc.add_table(rows=1 + len(rows), cols=len(headers))
    tbl.style = "Light Grid Accent 1"
    for c, h in enumerate(headers):
        tbl.rows[0].cells[c].text = h
    for r_idx, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            tbl.rows[r_idx].cells[c].text = val


def _chart_perimeter_map() -> Path:
    """Exhibit 1: Clean bird's-eye stadium wayfinding & perimeter map."""
    fig, ax = plt.subplots(figsize=(8.4, 5.4), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 68)
    ax.axis("off")

    # Outer bowl (horseshoe oval)
    bowl = patches.FancyBboxPatch((27, 15), 46, 38, boxstyle="round,pad=2,rounding_size=14",
                                  linewidth=3, edgecolor=NAVY, facecolor="#E2E8F0")
    ax.add_patch(bowl)
    # Field inside
    field = patches.Rectangle((38, 22), 24, 24, linewidth=2, edgecolor="white", facecolor="#15803D")
    ax.add_patch(field)
    for y in (26, 30, 34, 38, 42):
        ax.plot([38, 62], [y, y], color="white", alpha=0.55, lw=1)
    ax.text(50, 34, "50-YD LINE\nFIELD", ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")

    # 4-Corner ADA Ramps
    for (rx, ry, lbl) in [(29, 50, "NW Ramp"), (71, 50, "NE Ramp"), (29, 18, "SW Ramp"), (71, 18, "SE Ramp")]:
        ax.scatter([rx], [ry], s=110, color=STEEL, zorder=5)
        ax.text(rx, ry + (2.7 if ry > 34 else -3.0), lbl, ha="center", va="center",
                fontsize=7.5, fontweight="bold", color=STEEL)

    # 12 Elevators at East & West 50-yard lines
    for (ex, side) in [(27.2, "West 50-Yd\nElevators"), (72.8, "East 50-Yd\nElevators")]:
        ax.scatter([ex], [34], s=140, marker="s", color=ORANGE, zorder=6)
        ax.text(ex + (-2.5 if ex < 50 else 2.5), 34, side,
                ha="right" if ex < 50 else "left", va="center", fontsize=8, fontweight="bold", color=ORANGE)

    # Perimeter landmarks (boxes outside bowl)
    def callout(x, y, text, color, ha="center"):
        ax.text(x, y, text, ha=ha, va="center", fontsize=8.2, fontweight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.42", facecolor=color, edgecolor="none"))

    callout(13, 57, "RTD Light Rail\n(E & W Lines · NW)", NAVY)
    callout(84, 57, "Gate 5 (NE)\nRideshare DROP-OFF", ORANGE)
    callout(87, 25, "East Side (Gate 10)\nTeam Store &\nRing of Fame Plaza", NAVY)
    callout(87, 10, "Walk East -> Ball Arena\n5th & Walnut\nRideshare PICK-UP", "#B91C1C")
    callout(50, 7, "Gates 1 & 10 · South Stands\nGate 6 Lower Concourse: Sensory Room", SLATE)
    callout(12, 22, "Lot C (6h Early)\nLot G (ADA)\nWest / Federal Blvd", STEEL)

    # Compass
    ax.text(50, 62.5, "N", ha="center", va="center", fontsize=11, fontweight="bold", color=NAVY)
    ax.annotate("", xy=(50, 60.5), xytext=(50, 55.5), arrowprops=dict(arrowstyle="->", lw=2, color=NAVY))

    ax.set_title("Empower Field at Mile High — Staff Perimeter & Wayfinding Map",
                 fontsize=12.5, fontweight="bold", color=NAVY, pad=6)
    p = CHARTS / "perimeter_map.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_levels_directory() -> Path:
    """Exhibit 2: 5-Level vertical cross-section & key food/guest service sections."""
    fig, ax = plt.subplots(figsize=(8.8, 5.4), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 58)
    ax.axis("off")

    levels = [
        (46, "500 LEVEL", "Upper Deck · Secs 500–542", "#1E3A5F",
         "First Aid: Sec 534  ·  Nursing Pod: West Elevator Lobby",
         "Food: Cupbop (518) · Carrera's (509, 542) · Chillville (539)"),
        (35.5, "400 LEVEL", "United Globe Club", "#2B4C7E",
         "Club & Suite Gates open 3 hrs pre-kickoff (vs. 2 hrs general)",
         "Food & Transit: Mister Oso · Club Bars · 15 Escalators serve tier"),
        (25, "300 LEVEL", "Suites & Broncos Row", "#3B6EA5",
         "Services: Nursing Pod (322) · Reverse ATM @ Guest Relations",
         "Food: Bagel Deli (306, 327, 335) · Aloha Cones Poke (327)"),
        (14.5, "200 LEVEL", "Lower Tier & South Stands", "#0F766E",
         "Historic South Stands · Club/Suite Sensory Room near Suite 233",
         "Value Food: $5 Mile High Favs stand at Section 230"),
        (4, "100 LEVEL", "Main Concourse · 100–135", ORANGE,
         "First Aid: Sec 122 · Reverse ATMs: 105, 114, 122, 133 · Sensory: Gate 6",
         "Food: Atwater (100) · Oso (103) · Mtn Village (110–117) · Blue Pan/Tag (127)"),
    ]
    for y, code, sub, color, line1, line2 in levels:
        left = patches.FancyBboxPatch((1.5, y), 25, 8.4, boxstyle="round,pad=0.3,rounding_size=1.2",
                                      facecolor=color, edgecolor="none")
        right = patches.FancyBboxPatch((28, y), 70.5, 8.4, boxstyle="round,pad=0.3,rounding_size=1.2",
                                       facecolor="white", edgecolor=color, linewidth=1.7)
        ax.add_patch(left)
        ax.add_patch(right)
        ax.text(14, y + 5.6, code, ha="center", va="center", color="white", fontsize=9.8, fontweight="bold")
        ax.text(14, y + 2.5, sub, ha="center", va="center", color="#E2E8F0", fontsize=7.5)
        ax.text(29.8, y + 5.7, line1, ha="left", va="center", color=NAVY, fontsize=8.0, fontweight="bold")
        ax.text(29.8, y + 2.5, line2, ha="left", va="center", color=SLATE, fontsize=7.8)

    ax.set_title("Vertical Stadium Directory: Levels 100–500 Food, First Aid & Services",
                 fontsize=12, fontweight="bold", color=NAVY, pad=6)
    p = CHARTS / "levels_directory.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_gameday_timeline() -> Path:
    """Exhibit 3: Game-day operations opening countdown timeline."""
    milestones = [
        ("Lot C\n(Early Tailgate)", 6.0, ORANGE),
        ("Standard\nParking Lots", 4.5, STEEL),
        ("Club & Suite\nGates", 3.0, MINT),
        ("General\nPublic Gates", 2.0, NAVY),
    ]
    fig, ax = plt.subplots(figsize=(7.6, 3.6), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    labels = [m[0] for m in milestones]
    hours = [m[1] for m in milestones]
    colors = [m[2] for m in milestones]
    y = np.arange(len(labels))

    bars = ax.barh(y, hours, height=0.55, color=colors)
    ax.set_yticks(y, labels, fontsize=9.5, fontweight="bold", color=NAVY)
    ax.invert_yaxis()
    ax.set_xlabel("Hours Before Kickoff", fontsize=9.5, fontweight="bold", color=NAVY)
    ax.set_xlim(0, 7.3)
    ax.set_title("Game-Day Opening Schedule (Hours Prior to Kickoff)", fontsize=12, fontweight="bold", color=NAVY)
    for bar, h in zip(bars, hours):
        ax.text(h + 0.14, bar.get_y() + bar.get_height() / 2, f"{h:g} hrs pre-kickoff",
                va="center", fontsize=9, fontweight="bold", color=NAVY)
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    ax.spines[["top", "right"]].set_visible(False)
    p = CHARTS / "gameday_timeline.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_super_bowls() -> Path:
    """Exhibit 4: Denver's three Super Bowl victories."""
    games = ["Super Bowl XXXII\n(vs. Green Bay, 1998)", "Super Bowl XXXIII\n(vs. Atlanta, 1999)", "Super Bowl 50\n(vs. Carolina, 2016)"]
    denver = [31, 34, 24]
    opp = [24, 19, 10]
    x = np.arange(len(games))
    w = 0.34

    fig, ax = plt.subplots(figsize=(7.6, 3.9), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    b1 = ax.bar(x - w / 2, denver, w, label="Denver Points", color=ORANGE)
    b2 = ax.bar(x + w / 2, opp, w, label="Opponent Points", color=NAVY)
    for bar in b1:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.8, f"{int(h)}", ha="center", fontsize=9.5, fontweight="bold", color=ORANGE)
    for bar in b2:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.8, f"{int(h)}", ha="center", fontsize=9.5, fontweight="bold", color=NAVY)

    ax.set_xticks(x, games, fontsize=9)
    ax.set_ylabel("Points Scored", fontsize=9.5, fontweight="bold", color=NAVY)
    ax.set_ylim(0, 42)
    ax.set_title("Denver's Three Super Bowl Championships", fontsize=12, fontweight="bold", color=NAVY)
    ax.legend(frameon=False, loc="upper right")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.spines[["top", "right"]].set_visible(False)
    p = CHARTS / "denver_super_bowls.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def build_broncos_guide() -> Path:
    doc = Document()
    doc.core_properties.title = "Game-Day Staff Orientation: Empower Field at Mile High & Denver Football"
    doc.core_properties.author = "Stadium Guest Operations Training"

    doc.add_heading("Game-Day Staff Orientation: Empower Field at Mile High", level=0)
    doc.add_paragraph(
        "Unofficial Stadium Wayfinding, Guest Services & Franchise History Handbook. "
        "This training guide prepares new ushers, gate attendants, and guest-relations staff to direct 76,125 fans "
        "smoothly from parking and light rail to their seats, food stands, medical aid, and post-game rideshares — "
        "while knowing the Mile High traditions and Denver championship history fans love to talk about."
    )

    # ---- Section 1: Arrival, Parking, Transit & Rideshare ----
    doc.add_heading("1. Arrival, Parking Lots, Light Rail & Rideshare Wayfinding", level=1)
    doc.add_paragraph(
        "Most stadium parking lots open 4.5 hours before kickoff, giving fans time to park and tailgate cleanly "
        "(one permit covers one vehicle space only). Early tailgaters looking for first-come, first-served spots "
        "should be directed to Lot C (the Ford F-150 Lot), which opens 6 hours before kickoff and typically fills "
        "within an hour. Designated ADA accessible parking spaces are located in Lot G and select stadium lots. "
        "Every parking lot at Empower Field is 100 percent cashless — credit cards, debit cards, and digital wallet "
        "passes only."
    )
    doc.add_paragraph(
        "For fans skipping stadium parking, the RTD Light Rail Empower Field at Mile High Station sits immediately "
        "west and northwest of the stadium and is served by the E and W Lines. Staff must remind drivers that the "
        "stadium light rail station has no public RTD parking lot of its own, so transit riders must park at an "
        "outlying Park-n-Ride station along their line. The Decatur-Federal Station to the west also offers walkable "
        "access to the stadium's western gates."
    )
    doc.add_paragraph(
        "Rideshare (Uber and Lyft) has a critical pre-game versus post-game difference that every staff member must "
        "memorize. Before the game, the designated rideshare drop-off zone is on the northeast side of the stadium "
        "near Gate 5 on Mile High Stadium Circle. After the game, rideshare pick-up is NOT permitted along the "
        "stadium perimeter because of outbound traffic closures. Instead, direct departing rideshare guests east "
        "across I-25 to the Ball Arena / 4Runner Lot near 5th and Walnut Street."
    )
    doc.add_picture(str(_chart_gameday_timeline()), width=Inches(5.8))
    doc.add_paragraph("Figure 1: Game-day opening schedule in hours prior to kickoff for lots and stadium gates.")

    # ---- Section 2: Getting Up, Down & Around the Stadium ----
    doc.add_heading("2. Navigating Gates, Levels, Elevators, Ramps & Landmarks", level=1)
    doc.add_paragraph(
        "General public gates open 2 hours prior to kickoff, while guests with United Globe Club or Suite Level "
        "tickets may enter 3 hours prior to kickoff. Ten numbered perimeter gates ring the facility: Gates 1 and 10 "
        "anchor the South Stands end, Gates 2 and 4 serve the western and southwestern approaches, Gate 5 sits on "
        "the northeast side by rideshare drop-off, Gate 6 on the Lower Concourse provides direct access to the "
        "all-ticketholder Sensory Room, and Gates 7 and 9 serve the eastern and northeastern sides. Two iconic "
        "meeting landmarks — the Ring of Fame Plaza and the main Team Store — sit on the east side near Gate 10 "
        "facing I-25."
    )
    doc.add_paragraph(
        "Inside the horseshoe bowl, seating rises across five numbered tiers: the 100 Level (Lower Bowl, Sections "
        "100 to 135, where main concourse tunnels enter around Row 20), the 200 Level (lower-tier seating including "
        "the famous South Stands), the 300 Level (Suites and Broncos Row), the 400 Level (United Globe Club), and "
        "the 500 Level (Upper Deck, Sections 500 to 542, known for panoramic Rocky Mountain and skyline views)."
    )
    doc.add_paragraph(
        "Vertical circulation is built around three clear numbers — 12, 15, and 4. Twelve public ADA-compliant "
        "elevators are clustered at the 50-yard line on both the East and West sides, connecting every single "
        "seating, club, and suite level. Fifteen escalators operate inside the ramp quadrants and serve the club "
        "and suite tiers. Continuous ADA-compliant pedestrian ramps sit in all four corners of the stadium, "
        "providing a steady walking route all the way up to the 500 Level."
    )
    doc.add_picture(str(_chart_perimeter_map()), width=Inches(6.2))
    doc.add_paragraph("Figure 2: Bird's-eye perimeter map showing gates, four-corner ramps, 50-yard-line elevators, RTD Light Rail, and rideshare zones.")

    # ---- Section 3: Food, Concessions & Cashless Kiosks ----
    doc.add_heading("3. Food & Concessions Guide — Where to Send Hungry Fans", level=1)
    doc.add_paragraph(
        "Empower Field at Mile High is a completely cashless venue across parking, retail, and every food stand. "
        "When a guest only has paper cash, direct them to a fee-free Reverse ATM (cash-to-card kiosk) that converts "
        "cash into a prepaid debit card usable anywhere inside or outside the stadium. Level 1 Reverse ATMs stand "
        "near Sections 105, 114, 122, and 133, and additional kiosks sit next to Guest Relations Centers on Levels "
        "3 and 5."
    )
    doc.add_paragraph(
        "Families looking for budget-friendly snacks should be pointed to 'Mile High Favs' value stands, which sell "
        "$5 stadium classics including hot dogs, popcorn, cheese nachos, Bavarian pretzels, soda, and bottled water. "
        "Mile High Favs locations appear on every concourse — including Sections 104, 107, 114, 123, 128, and 133 "
        "on Level 100; Section 230 on Level 200; Sections 314, 318, 328, and 332 on Level 300; and seven stands "
        "across Level 500."
    )
    _add_table(
        doc,
        ["Stand / Neighborhood", "Level & Section(s)", "Signature Specialty"],
        [
            ["Atwater Grill", "Level 100 · Sec 100", "Hall of Famer & Safety Blitz specialty burgers"],
            ["Mister Oso", "Level 100 · Sec 103 (and Club)", "Smoky short-rib quesabirria tacos with consommé"],
            ["ARTA Tequila Bar", "Level 100 · Sec 106 (West 50-Yd)", "Craft margaritas, palomas, high-top TVs"],
            ["Mile High Mountain Village", "Level 100 · Secs 110–117 (NW)", "Colorado ski-lodge zone: Birdcall (110), Lucy's Burgers, Breckenridge Barrel Bar"],
            ["Tag Burger Bar & Blue Pan Pizza", "Level 100 · Sec 127", "Green Chili Cheeseburgers & Detroit-style square pizza"],
            ["The Beast in a Blanket", "Level 100 · Sec 128", "Two-foot hot dog in puff pastry with brisket burnt ends"],
            ["Bagel Deli & Aloha Cones", "Level 300 · Sec 327 (306/335)", "Mile High Madness sandwiches & fresh ahi poke bowls"],
            ["Cupbop & Carrera's", "Level 500 · Sec 518 / 509 & 542", "Korean B-Bop rice bowls & California-style burritos"],
            ["Chillville", "Level 500 · Sec 539", "Margarita soft serve & frozen treats"],
        ],
    )
    doc.add_picture(str(_chart_levels_directory()), width=Inches(6.2))
    doc.add_paragraph("Figure 3: Five-level vertical directory of signature food stands, First Aid centers, and guest services.")

    # ---- Section 4: Safety, Accessibility, Bags & Mile High Weather ----
    doc.add_heading("4. Clear Bag Policy, First Aid, Accessibility & Altitude Protocol", level=1)
    doc.add_paragraph(
        "Security gates enforce the stadium Clear Bag Policy strictly: guests may carry one clear plastic, vinyl, "
        "or PVC bag no larger than 12 by 6 by 12 inches, or a one-gallon clear plastic freezer bag, plus a small "
        "non-clear clutch or purse no larger than 4.5 by 6.5 inches. Regular backpacks, tinted plastic bags, camera "
        "cases, and diaper bags are not permitted, though medically necessary items are allowed after gate inspection."
    )
    doc.add_paragraph(
        "Medical staff operate two full First Aid Centers: on the Main Concourse across from Section 122, and on "
        "the Upper Concourse near Section 534. Both centers handle emergencies, altitude dizziness, and refrigerated "
        "storage for guest medications. For guests with sensory sensitivities, Empower Field provides two quiet "
        "Sensory Rooms: an all-ticketholder room on the Lower Concourse inside Gate 6, and a Club/Suite room on the "
        "Plaza Level adjacent to Suite 233. Free KultureCity sensory bags — stocked with noise-canceling headphones, "
        "fidget tools, and weighted lap pads — can be checked out at any Guest Relations booth. Private nursing and "
        "lactation pods are located at Section 122 (inside Gate 6), Section 322, and the Level 500 West elevator "
        "lobby, with access codes issued by Guest Relations."
    )
    doc.add_paragraph(
        "Empower Field sits at an exact elevation of 5,280 feet — one full mile above sea level. Visiting fans from "
        "sea level often underestimate Denver's dry, thin air, so staff regularly remind guests to hydrate. Thin air "
        "also affects the game itself: kicked footballs travel roughly 5 to 10 percent farther in Denver, helping "
        "fuel Matt Prater's then-record 64-yard field goal in December 2013 and Jason Elam's 63-yard kick in 1998. "
        "During summer or early-fall thunderstorms, if lightning strikes within 8 miles of the stadium, fans shelter "
        "in the covered concourses for a mandatory 30-minute wait that resets with each new nearby strike."
    )

    # ---- Section 5: Denver Football Traditions & Championship History ----
    doc.add_heading("5. Mile High Traditions, Legends & Championship History", level=1)
    doc.add_paragraph(
        "Knowing Denver's proud history helps game-day staff connect warmly with fans. Empower Field opened in 2001 "
        "to replace the beloved original Mile High Stadium (famous for its movable East Stands and roaring South "
        "Stands), and its official seating capacity of 76,125 deliberately honors the number 76 and Denver's 1976 "
        "civic roots. After every Denver touchdown, Thunder — the team's live Arabian gelding mascot — gallops "
        "across the field, while every visiting incomplete pass triggers the stadium-wide 'In-Com-Plete!' chant."
    )
    doc.add_paragraph(
        "Founded as a 1960 American Football League charter franchise (whose players famously burned their original "
        "vertical-striped brown-and-yellow socks in a 1962 bonfire), Denver broke through in 1977 behind the "
        "'Orange Crush' 3-4 defense led by Randy Gradishar, Tom Jackson, Lyle Alzado, and Louis Wright to reach "
        "its first Super Bowl. In the 1986 AFC Championship at Cleveland, quarterback John Elway engineered 'The "
        "Drive' — a 98-yard touchdown march in five minutes and two seconds — cementing his status as a franchise "
        "icon across all 16 of his seasons in Denver."
    )
    doc.add_paragraph(
        "Denver has won three Super Bowl championships. In Super Bowl XXXII (January 1998), running back Terrell "
        "Davis earned MVP honors and John Elway's famous eight-yard 'Helicopter' dive on third-and-six powered a "
        "31–24 upset of Green Bay, snapping a 13-year AFC title drought. One year later, after Terrell Davis rushed "
        "for 2,008 yards to win the 1998 NFL MVP award, Elway earned Super Bowl XXXIII MVP in a 34–19 repeat over "
        "Atlanta. After Peyton Manning's 2013 Denver offense set NFL single-season records with 55 touchdown passes "
        "and 606 team points, the 2015 'No Fly Zone' secondary (Aqib Talib, Chris Harris Jr., T.J. Ward, and Darian "
        "Stewart) and pass-rushing duo Von Miller and DeMarcus Ware carried Denver to Super Bowl 50, beating "
        "Carolina 24–10 as Von Miller captured MVP with 2.5 sacks and two forced fumbles."
    )
    _add_table(
        doc,
        ["Super Bowl", "Date", "Opponent", "Score", "MVP & Signature Moment"],
        [
            ["Super Bowl XXXII", "Jan 1998", "Green Bay", "31–24", "Terrell Davis · Elway's 8-yd Helicopter dive"],
            ["Super Bowl XXXIII", "Jan 1999", "Atlanta", "34–19", "John Elway · Back-to-back titles, 336 pass yds"],
            ["Super Bowl 50", "Feb 2016", "Carolina", "24–10", "Von Miller · 2.5 sacks, 2 forced fumbles, No Fly Zone"],
        ],
    )
    doc.add_picture(str(_chart_super_bowls()), width=Inches(5.8))
    doc.add_paragraph("Figure 4: Points scored by Denver versus opponents across Denver's three Super Bowl victories.")

    out_path = OUT / "GameDay_Staff_Guide_Denver_Broncos.docx"
    doc.save(str(out_path))
    return out_path


if __name__ == "__main__":
    p = build_broncos_guide()
    print(f"Generated: {p} ({p.stat().st_size:,} bytes)")
