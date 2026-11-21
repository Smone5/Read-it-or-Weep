"""Generate the license-safe, fan-oriented Empower Field at Mile High Game-Day Fan Guide (.docx).

Written directly for fans attending a Denver Broncos game (kids, families, first-timers, and die-hards).
100% original text + 5 custom matplotlib wayfinding/food/policy/history graphics + verified YouTube links.
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
    """Exhibit 1: Clean bird's-eye fan wayfinding & perimeter map."""
    fig, ax = plt.subplots(figsize=(8.4, 5.4), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 68)
    ax.axis("off")

    bowl = patches.FancyBboxPatch((27, 15), 46, 38, boxstyle="round,pad=2,rounding_size=14",
                                  linewidth=3, edgecolor=NAVY, facecolor="#E2E8F0")
    ax.add_patch(bowl)
    field = patches.Rectangle((38, 22), 24, 24, linewidth=2, edgecolor="white", facecolor="#15803D")
    ax.add_patch(field)
    for y in (26, 30, 34, 38, 42):
        ax.plot([38, 62], [y, y], color="white", alpha=0.55, lw=1)
    ax.text(50, 34, "50-YD LINE\nFIELD", ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")

    for (rx, ry, lbl) in [(29, 50, "NW Ramp"), (71, 50, "NE Ramp"), (29, 18, "SW Ramp"), (71, 18, "SE Ramp")]:
        ax.scatter([rx], [ry], s=110, color=STEEL, zorder=5)
        ax.text(rx, ry + (2.7 if ry > 34 else -3.0), lbl, ha="center", va="center",
                fontsize=7.5, fontweight="bold", color=STEEL)

    for (ex, side) in [(27.2, "West 50-Yd\nElevators"), (72.8, "East 50-Yd\nElevators")]:
        ax.scatter([ex], [34], s=140, marker="s", color=ORANGE, zorder=6)
        ax.text(ex + (-2.5 if ex < 50 else 2.5), 34, side,
                ha="right" if ex < 50 else "left", va="center", fontsize=8, fontweight="bold", color=ORANGE)

    def callout(x, y, text, color, ha="center"):
        ax.text(x, y, text, ha=ha, va="center", fontsize=8.2, fontweight="bold", color="white",
                bbox=dict(boxstyle="round,pad=0.42", facecolor=color, edgecolor="none"))

    callout(13, 57, "RTD Light Rail\n(E & W Lines · NW)", NAVY)
    callout(84, 57, "Gate 5 (NE)\nRideshare DROP-OFF", ORANGE)
    callout(87, 25, "East Side (Gate 10)\nTeam Store &\nRing of Fame Plaza", NAVY)
    callout(87, 10, "Walk East -> Ball Arena\n5th & Walnut\nRideshare PICK-UP", "#B91C1C")
    callout(50, 7, "Gates 1 & 10 · South Stands\nGate 6 Lower Concourse: Sensory Room", SLATE)
    callout(12, 22, "Lot C (6h Early)\nLot G (ADA)\nWest / Federal Blvd", STEEL)

    ax.text(50, 62.5, "N", ha="center", va="center", fontsize=11, fontweight="bold", color=NAVY)
    ax.annotate("", xy=(50, 60.5), xytext=(50, 55.5), arrowprops=dict(arrowstyle="->", lw=2, color=NAVY))

    ax.set_title("Empower Field at Mile High — Fan Perimeter & Wayfinding Map",
                 fontsize=12.5, fontweight="bold", color=NAVY, pad=6)
    p = CHARTS / "perimeter_map.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_levels_directory() -> Path:
    """Exhibit 2: 5-Level vertical fan directory of food & services."""
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
         "Famous South Stands · Club/Suite Sensory Room near Suite 233",
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

    ax.set_title("Level-by-Level Fan Guide: Where to Find Food, First Aid & Services",
                 fontsize=12, fontweight="bold", color=NAVY, pad=6)
    p = CHARTS / "levels_directory.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_gameday_timeline() -> Path:
    """Exhibit 3: Game-day countdown — when lots and gates open."""
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
    ax.set_title("Game-Day Countdown: When Parking Lots & Gates Open", fontsize=12, fontweight="bold", color=NAVY)
    for bar, h in zip(bars, hours):
        ax.text(h + 0.14, bar.get_y() + bar.get_height() / 2, f"{h:g} hrs pre-kickoff",
                va="center", fontsize=9, fontweight="bold", color=NAVY)
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    ax.spines[["top", "right"]].set_visible(False)
    p = CHARTS / "gameday_timeline.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_policies_and_altitude() -> Path:
    """Exhibit 4: Visual guide to Clear Bag sizes, 5,280 ft altitude & 8-mile lightning rule."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.8, 4.3), dpi=170)
    fig.patch.set_facecolor(BG)

    ax1.set_facecolor(BG)
    ax1.set_xlim(0, 100)
    ax1.set_ylim(0, 82)
    ax1.axis("off")
    ax1.set_title("Clear Bag Policy at the Gates", fontsize=11, fontweight="bold", color=NAVY, pad=6)

    tote = patches.FancyBboxPatch((8, 34), 38, 36, boxstyle="round,pad=0.6,rounding_size=3",
                                  facecolor="#DBEAFE", edgecolor=STEEL, linewidth=2.4)
    ax1.add_patch(tote)
    ax1.text(27, 56, "CLEAR BAG", ha="center", va="center", fontsize=9.5, fontweight="bold", color=NAVY)
    ax1.text(27, 47, 'Max 12" × 6" × 12"', ha="center", va="center", fontsize=8.5, fontweight="bold", color=STEEL)
    ax1.text(27, 39, "(or 1-gal freezer bag)", ha="center", va="center", fontsize=7.5, color=SLATE)
    ax1.text(27, 27, "ALLOWED", ha="center", va="center", fontsize=8.5, fontweight="bold", color="white",
             bbox=dict(boxstyle="round,pad=0.35", facecolor=MINT, edgecolor="none"))

    clutch = patches.FancyBboxPatch((56, 42), 34, 22, boxstyle="round,pad=0.6,rounding_size=3",
                                    facecolor="#FEF3C7", edgecolor=GOLD, linewidth=2.2)
    ax1.add_patch(clutch)
    ax1.text(73, 55, "SMALL CLUTCH", ha="center", va="center", fontsize=8.5, fontweight="bold", color=NAVY)
    ax1.text(73, 47, 'Max 4.5" × 6.5"', ha="center", va="center", fontsize=8.2, fontweight="bold", color="#B45309")
    ax1.text(73, 27, "ALLOWED", ha="center", va="center", fontsize=8.5, fontweight="bold", color="white",
             bbox=dict(boxstyle="round,pad=0.35", facecolor=MINT, edgecolor="none"))

    ax1.text(50, 10, "NO regular backpacks, tinted bags, camera or diaper bags\n(Medically necessary items allowed after gate inspection)",
             ha="center", va="center", fontsize=7.6, fontweight="bold", color="#991B1B",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#FEE2E2", edgecolor="#DC2626", linewidth=1.3))

    ax2.set_facecolor(BG)
    ax2.set_xlim(0, 100)
    ax2.set_ylim(0, 82)
    ax2.axis("off")
    ax2.set_title("Mile High Altitude & Weather Tips", fontsize=11, fontweight="bold", color=NAVY, pad=6)

    cards = [
        (58, "5,280 FEET ELEVATION", "1 full mile high · drink plenty of water\nKicks fly 5–10% farther (64-yd Prater FG!)", ORANGE),
        (32, "8-MILE LIGHTNING RULE", "Strike within 8 miles -> shelter in concourses\nMandatory 30-min wait resets on each new strike", NAVY),
        (6,  "CARE & SENSORY ROOMS", "First Aid: Sec 122 & Sec 534\nSensory Room: Gate 6 (all fans) & Suite 233", "#0F766E"),
    ]
    for y, head, body, col in cards:
        box = patches.FancyBboxPatch((4, y), 92, 20, boxstyle="round,pad=0.4,rounding_size=2.5",
                                     facecolor="white", edgecolor=col, linewidth=2.0)
        ax2.add_patch(box)
        ax2.text(9, y + 14.5, head, ha="left", va="center", fontsize=8.8, fontweight="bold", color=col)
        ax2.text(9, y + 6.5, body, ha="left", va="center", fontsize=7.6, color=NAVY)

    plt.tight_layout(pad=1.2)
    p = CHARTS / "policies_and_altitude.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_super_bowls() -> Path:
    """Exhibit 5: Denver's three Super Bowl victories."""
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


def build_fan_guide() -> Path:
    doc = Document()
    doc.core_properties.title = "Game-Day Fan Guide: Visiting Empower Field at Mile High"
    doc.core_properties.author = "Unofficial Mile High Fan Companion"

    doc.add_heading("Game-Day Fan Guide: Visiting Empower Field at Mile High", level=0)
    doc.add_paragraph(
        "Unofficial Fan Survival, Wayfinding & Broncos History Guide. "
        "Heading to a Denver Broncos game for the first time — or your fiftieth? "
        "Here is everything a fan needs to breeze through parking, light rail, rideshare, stadium levels, "
        "the best food stands, clear-bag rules, and Mile High traditions before kickoff!"
    )

    # ---- Section 1: Getting There — Parking, Light Rail & Rideshare ----
    doc.add_heading("1. Getting to the Game: Parking, Light Rail & Rideshare", level=1)
    doc.add_paragraph(
        "If you are driving to Empower Field at Mile High, most stadium parking lots open 4.5 hours before kickoff, "
        "giving fans plenty of time to tailgate (one permit covers one vehicle space). Want to start tailgating even "
        "earlier? Head to Lot C (the Ford F-150 Lot), which opens 6 hours before kickoff with first-come, first-served "
        "spots that usually fill within an hour. Accessible ADA parking is available in Lot G and select stadium lots. "
        "Leave cash at home — every parking lot is 100 percent cashless and takes credit cards, debit cards, or "
        "mobile wallet passes only."
    )
    doc.add_paragraph(
        "Want to skip game-day traffic? Hop on the RTD Light Rail E or W Line and ride straight to the Empower Field "
        "at Mile High Station just northwest of the stadium. One catch to remember: the stadium rail station has no "
        "public RTD parking lot of its own, so park at an outlying Park-n-Ride along your line before boarding. The "
        "Decatur-Federal Station to the west is also an easy walk to the west gates."
    )
    doc.add_paragraph(
        "Taking an Uber or Lyft? Know the big before-and-after-game rule so you don't get stuck wandering! Before "
        "kickoff, rideshares drop fans off on the northeast side near Gate 5 on Mile High Stadium Circle. After the "
        "final whistle, outbound road closures block rideshare pick-up around the stadium itself — instead, walk east "
        "across I-25 to the Ball Arena / 4Runner Lot near 5th and Walnut Street to catch your ride home."
    )
    doc.add_picture(str(_chart_gameday_timeline()), width=Inches(5.8))
    doc.add_paragraph("Figure 1: When parking lots and stadium gates open before kickoff.")

    # ---- Section 2: Getting Around Inside the Stadium ----
    doc.add_heading("2. Finding Your Gate, Your Level & Iconic Stadium Landmarks", level=1)
    doc.add_paragraph(
        "General public gates swing open 2 hours before kickoff, while fans with United Globe Club or Suite tickets "
        "can head inside 3 hours before kickoff. Ten numbered gates ring the stadium: Gates 1 and 10 anchor the loud, "
        "historic South Stands; Gates 2 and 4 serve the west side; Gate 5 sits on the northeast corner by rideshare "
        "drop-off; Gate 6 leads straight to the Lower Concourse Sensory Room; and Gates 7 and 9 open onto the east "
        "side. Looking for a photo op or jersey before the game? Walk to the east side near Gate 10 for the Ring of "
        "Fame Plaza and the main Broncos Team Store."
    )
    doc.add_paragraph(
        "Inside the horseshoe bowl, seats rise across five levels: the 100 Level (Lower Bowl, Sections 100 to 135, "
        "where concourse tunnels enter around Row 20), the 200 Level (lower-tier seating including the South Stands), "
        "the 300 Level (Suites and Broncos Row), the 400 Level (United Globe Club), and the 500 Level (Upper Deck, "
        "Sections 500 to 542, famous for sweeping views of the Denver skyline and Rocky Mountains)."
    )
    doc.add_paragraph(
        "Getting up and down is easy once you know the 12-15-4 layout: 12 public ADA-compliant elevators sit right "
        "at the 50-yard line on both the East and West sides and reach every level; 15 escalators run inside the ramp "
        "quadrants and club/suite levels; and wide ADA-compliant pedestrian ramps curve up all four corners of the "
        "stadium all the way to the 500 Level. Take a quick virtual stadium walkthrough on YouTube: "
        "https://www.youtube.com/watch?v=V_F6byM7Xyo"
    )
    doc.add_picture(str(_chart_perimeter_map()), width=Inches(6.2))
    doc.add_paragraph("Figure 2: Fan perimeter map showing gates, four-corner ramps, 50-yard elevators, RTD Light Rail, and rideshare zones.")

    # ---- Section 3: Stadium Food & Drinks ----
    doc.add_heading("3. What to Eat at Mile High: $5 Classics & Local Favorites", level=1)
    doc.add_paragraph(
        "Every concession stand and bar at Empower Field is completely cashless. If you only brought paper cash, "
        "don't panic — step up to a fee-free Reverse ATM (cash-to-card kiosk) to swap your bills for a prepaid debit "
        "card you can spend anywhere in the stadium. On Level 100, Reverse ATMs sit near Sections 105, 114, 122, and "
        "133, and you'll find more right next to Guest Relations on Levels 300 and 500."
    )
    doc.add_paragraph(
        "Want great stadium food without blowing your budget? Look for 'Mile High Favs' value stands on every level, "
        "where hot dogs, popcorn, cheese nachos, Bavarian pretzels, soda, and bottled water are all just $5 each! "
        "Mile High Favs stands appear at Sections 104, 107, 114, 123, 128, and 133 on Level 100, Section 230 on Level "
        "200, four spots on Level 300, and seven stands up on Level 500. Preview the latest Mile High menu on 9NEWS: "
        "https://www.youtube.com/watch?v=X5FumD48dao"
    )
    _add_table(
        doc,
        ["Food Stand / Spot", "Level & Section(s)", "Must-Try Fan Favorite"],
        [
            ["Atwater Grill", "Level 100 · Sec 100", "Hall of Famer & Safety Blitz specialty burgers"],
            ["Mister Oso", "Level 100 · Sec 103 (and Club)", "Smoky short-rib quesabirria tacos with consommé"],
            ["ARTA Tequila Bar", "Level 100 · Sec 106 (West 50-Yd)", "Craft margaritas, palomas & game-day TVs"],
            ["Mile High Mountain Village", "Level 100 · Secs 110–117 (NW)", "Colorado ski-lodge zone: Birdcall chicken (110), Lucy's Burgers & Breckenridge Barrel Bar"],
            ["Tag Burger Bar & Blue Pan Pizza", "Level 100 · Sec 127", "Green Chili Cheeseburgers & Detroit-style square pizza"],
            ["The Beast in a Blanket", "Level 100 · Sec 128", "Two-foot hot dog in flaky puff pastry with brisket burnt ends"],
            ["Bagel Deli & Aloha Cones", "Level 300 · Sec 327 (306/335)", "Mile High Madness sandwiches & fresh ahi poke bowls"],
            ["Cupbop & Carrera's", "Level 500 · Sec 518 / 509 & 542", "Korean B-Bop rice bowls & California-style burritos"],
            ["Chillville", "Level 500 · Sec 539", "Margarita soft serve & frozen treats"],
        ],
    )
    doc.add_picture(str(_chart_levels_directory()), width=Inches(6.2))
    doc.add_paragraph("Figure 3: Level-by-level fan directory of food favorites, First Aid centers, and guest services.")

    # ---- Section 4: Clear Bags, Altitude & Comfort Tips ----
    doc.add_heading("4. Clear Bag Rules, Mile High Air & Family Comfort Tips", level=1)
    doc.add_paragraph(
        "Pack smart so you sail right through security! Empower Field enforces a strict Clear Bag Policy: you can "
        "bring one clear plastic, vinyl, or PVC bag no larger than 12 by 6 by 12 inches (or a one-gallon clear "
        "freezer bag), plus a small non-clear clutch or purse no larger than 4.5 by 6.5 inches. Regular backpacks, "
        "tinted plastic bags, camera cases, and diaper bags have to stay home, though medically necessary items are "
        "always welcome after gate inspection."
    )
    doc.add_paragraph(
        "Need a breather, medical help, or a quiet spot for kids? First Aid Centers sit on the Main Concourse across "
        "from Section 122 and on the Upper Concourse near Section 534 (and can even refrigerate medications). Fans "
        "who need a calm break from stadium noise can use the free Sensory Room on the Lower Concourse inside Gate 6 "
        "(open to every ticketholder) or the Club/Suite Sensory Room near Suite 233, and borrow free KultureCity "
        "sensory bags with noise-canceling headphones and fidget tools at any Guest Relations booth. Private nursing "
        "pods are available at Section 122 (inside Gate 6), Section 322, and the Level 500 West elevator lobby."
    )
    doc.add_paragraph(
        "Remember: Empower Field sits at an exact elevation of 5,280 feet — one full mile above sea level! Drink "
        "extra water if you are visiting from lower elevation. The thin Denver air also creates football magic: "
        "kicked footballs fly roughly 5 to 10 percent farther here, helping fuel Matt Prater's then-NFL-record "
        "64-yard field goal in December 2013 and Jason Elam's 63-yarder in 1998. If lightning strikes within 8 miles "
        "of the stadium, fans shelter in the covered concourses for a mandatory 30-minute wait that resets with each "
        "new nearby strike."
    )
    doc.add_picture(str(_chart_policies_and_altitude()), width=Inches(6.2))
    doc.add_paragraph("Figure 4: Fan visual guide to the Clear Bag Policy, 5,280-foot altitude boost, and 8-mile lightning rule.")

    # ---- Section 5: Broncos Traditions & Championship Moments ----
    doc.add_heading("5. Broncos Game-Day Traditions, Legends & Super Bowl Glory", level=1)
    doc.add_paragraph(
        "Empower Field opened in 2001 to succeed the beloved original Mile High Stadium, and its official seating "
        "capacity of 76,125 was deliberately chosen to honor the number 76 and Denver's 1976 roots. Watch the turf "
        "after every Denver touchdown — Thunder, the Broncos' live Arabian horse mascot, gallops across the field "
        "while the crowd erupts! And whenever the opposing quarterback throws an incomplete pass, join 76,000 fans "
        "shouting 'In-Com-Plete!' in unison."
    )
    doc.add_paragraph(
        "Born as a 1960 American Football League original (whose players famously held a 1962 bonfire to burn their "
        "vertical-striped brown-and-yellow socks!), Denver reached its first Super Bowl in 1977 behind the ferocious "
        "'Orange Crush' 3-4 defense led by Randy Gradishar, Tom Jackson, Lyle Alzado, and Louis Wright. In the 1986 "
        "AFC Championship at Cleveland, quarterback John Elway led 'The Drive' — a legendary 98-yard touchdown march "
        "in five minutes and two seconds (watch highlights: https://www.youtube.com/watch?v=Px-tHzMEY-I )."
    )
    doc.add_paragraph(
        "Denver has hoisted three Vince Lombardi Trophies as Super Bowl champions! In Super Bowl XXXII (January "
        "1998), running back Terrell Davis won MVP and John Elway launched into his unforgettable eight-yard "
        "'Helicopter' dive on third-and-six to upset Green Bay 31–24, snapping a 13-year AFC title drought "
        "(https://www.youtube.com/watch?v=BuL1QaVXUpM ). One year later, after Terrell Davis rushed for 2,008 yards "
        "to win 1998 NFL MVP, Elway won Super Bowl XXXIII MVP in a 34–19 repeat over Atlanta. Following Peyton "
        "Manning's record-shattering 2013 offense (55 touchdown passes and 606 team points), the 2015 'No Fly Zone' "
        "secondary (Aqib Talib, Chris Harris Jr., T.J. Ward, and Darian Stewart) plus pass rushers Von Miller and "
        "DeMarcus Ware dominated Carolina 24–10 in Super Bowl 50 as Von Miller took MVP with 2.5 sacks and two "
        "forced fumbles (https://www.youtube.com/watch?v=wS1BRQ4l9_4 )."
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
    doc.add_paragraph("Figure 5: Points scored by Denver versus opponents across Denver's three Super Bowl victories.")

    out_path = OUT / "GameDay_Fan_Guide_Empower_Field.docx"
    doc.save(str(out_path))
    return out_path


if __name__ == "__main__":
    p = build_fan_guide()
    print(f"Generated: {p} ({p.stat().st_size:,} bytes)")
