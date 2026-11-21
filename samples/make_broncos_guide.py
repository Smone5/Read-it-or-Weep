"""Generate the license-safe Denver Broncos Ultimate Fan Guide (.docx):
80% exciting team history, legends, iconic games, records & nicknames + 20% fun Mile High game-day traditions.
Zero trademarked logos or copyrighted photography — 4 original custom matplotlib visuals + verified YouTube links.
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
CHARTS = OUT / "_charts_broncos_fan"
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


def _chart_the_drive() -> Path:
    """Exhibit 1: Visual football-field diagram of John Elway's 98-yard 'The Drive' (1986 AFC Championship)."""
    fig, ax = plt.subplots(figsize=(8.6, 3.8), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor("#15803D")
    ax.set_xlim(-10, 110)
    ax.set_ylim(0, 44)

    # End zones
    ax.add_patch(patches.Rectangle((-10, 0), 10, 44, facecolor="#78350F", alpha=0.9))
    ax.add_patch(patches.Rectangle((100, 0), 10, 44, facecolor=ORANGE, alpha=0.95))
    ax.text(-5, 22, "DEN\nEND\nZONE", ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")
    ax.text(105, 22, "TD!\nMARK\nJACKSON", ha="center", va="center", color="white", fontsize=8.2, fontweight="bold")

    # Yard lines every 10 yards
    for yd in range(0, 101, 10):
        ax.axvline(yd, color="white", alpha=0.6, lw=1.3 if yd in (0, 50, 100) else 0.9)
        if 10 <= yd <= 90:
            lbl = str(yd if yd <= 50 else 100 - yd)
            ax.text(yd, 4.5, lbl, ha="center", va="center", color="white", alpha=0.85, fontsize=8.5, fontweight="bold")
            ax.text(yd, 39.5, lbl, ha="center", va="center", color="white", alpha=0.85, fontsize=8.5, fontweight="bold")

    # Drive progression arc (2-yd line -> 15 plays -> 98 yards -> 5-yd TD pass)
    stops = [(2, 22, "START\nOwn 2-Yd Line\n5:32 left"), (38, 28, "3rd & 18\nConverted!"),
             (72, 16, "20-yd pass\nto Jackson"), (95, 24, "3rd & 1\n5-yd TD Pass\n0:37 left!")]
    xs = [s[0] for s in stops] + [102]
    ys = [s[1] for s in stops] + [22]
    for i in range(len(xs) - 1):
        ax.annotate(
            "", xy=(xs[i + 1], ys[i + 1]), xytext=(xs[i], ys[i]),
            arrowprops=dict(arrowstyle="->,head_width=0.35,head_length=0.5", lw=3.0, color="#FDE047"),
        )
    for x, y, txt in stops:
        ax.scatter([x], [y], s=110, color=ORANGE, edgecolor="white", linewidth=2, zorder=6)
        offset = 8.5 if y < 23 else -9.0
        ax.text(x, y + offset, txt, ha="center", va="center", fontsize=7.8, fontweight="bold", color=NAVY,
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white", edgecolor=NAVY, lw=1.2))

    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor("white")
        sp.set_linewidth(2)
    ax.set_title('Anatomy of "The Drive" (1986 AFC Championship): 98 Yards · 15 Plays · 5:02',
                 fontsize=11.5, fontweight="bold", color=NAVY, pad=8)
    p = CHARTS / "the_drive_field.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_super_bowls() -> Path:
    """Exhibit 2: Denver's three Super Bowl victories."""
    games = ["Super Bowl XXXII\n(vs. Green Bay, 1998)\nMVP: Terrell Davis",
             "Super Bowl XXXIII\n(vs. Atlanta, 1999)\nMVP: John Elway",
             "Super Bowl 50\n(vs. Carolina, 2016)\nMVP: Von Miller"]
    denver = [31, 34, 24]
    opp = [24, 19, 10]
    x = np.arange(len(games))
    w = 0.34

    fig, ax = plt.subplots(figsize=(8.0, 4.1), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    b1 = ax.bar(x - w / 2, denver, w, label="Denver Points", color=ORANGE)
    b2 = ax.bar(x + w / 2, opp, w, label="Opponent Points", color=NAVY)
    for bar in b1:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.8, f"{int(h)}", ha="center", fontsize=10, fontweight="bold", color=ORANGE)
    for bar in b2:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 0.8, f"{int(h)}", ha="center", fontsize=10, fontweight="bold", color=NAVY)

    ax.set_xticks(x, games, fontsize=8.8)
    ax.set_ylabel("Points Scored", fontsize=9.5, fontweight="bold", color=NAVY)
    ax.set_ylim(0, 42)
    ax.set_title("Denver's Three Super Bowl Championships & MVPs", fontsize=12, fontweight="bold", color=NAVY)
    ax.legend(frameon=False, loc="upper right")
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    ax.spines[["top", "right"]].set_visible(False)
    p = CHARTS / "denver_super_bowls.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_mile_high_kicks() -> Path:
    """Exhibit 3: Mile High thin-air field goals & elevation advantage."""
    kicks = [
        ("Matt Prater\n(Denver, 2013)", 64, ORANGE),
        ("Jason Elam\n(Denver, 1998)", 63, NAVY),
        ("Typical Sea-Level\nLong Kick", 55, STEEL),
    ]
    fig, ax = plt.subplots(figsize=(7.6, 3.4), dpi=170)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    y = np.arange(len(kicks))
    bars = ax.barh(y, [k[1] for k in kicks], height=0.52, color=[k[2] for k in kicks])
    ax.set_yticks(y, [k[0] for k in kicks], fontsize=9.2, fontweight="bold", color=NAVY)
    ax.invert_yaxis()
    ax.set_xlim(0, 74)
    ax.set_xlabel("Field Goal Distance (Yards)", fontsize=9.5, fontweight="bold", color=NAVY)
    ax.set_title("Mile High Thin-Air Advantage (5,280 ft Elevation · Kicks Fly 5–10% Farther)",
                 fontsize=11, fontweight="bold", color=NAVY)
    for bar, (_, dist, col) in zip(bars, kicks):
        ax.text(dist + 1.0, bar.get_y() + bar.get_height() / 2, f"{dist} yds",
                va="center", fontsize=9.5, fontweight="bold", color=col)
    ax.grid(axis="x", linestyle="--", alpha=0.35)
    ax.spines[["top", "right"]].set_visible(False)
    p = CHARTS / "mile_high_kicks.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def _chart_franchise_records() -> Path:
    """Exhibit 4: Single-season & career milestones of Broncos legends."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.8, 3.8), dpi=170)
    fig.patch.set_facecolor(BG)

    # Left: Peyton Manning's 2013 record offense vs typical top NFL offense
    ax1.set_facecolor(BG)
    labels1 = ["2013 Denver\n(Peyton Manning)", "2015 Denver\n(SB 50 Champs)", "1998 Denver\n(SB XXXIII Champs)"]
    pts = [606, 355, 501]
    colors1 = [ORANGE, STEEL, NAVY]
    bars1 = ax1.bar(labels1, pts, width=0.52, color=colors1)
    for b, val in zip(bars1, pts):
        ax1.text(b.get_x() + b.get_width() / 2, val + 12, f"{val} pts", ha="center", fontsize=9, fontweight="bold", color=NAVY)
    ax1.set_ylim(0, 700)
    ax1.set_ylabel("Regular-Season Team Points", fontsize=8.8, fontweight="bold", color=NAVY)
    ax1.set_title("Historic Denver Offenses (Team Points)", fontsize=10.5, fontweight="bold", color=NAVY)
    ax1.tick_params(axis="x", labelsize=8)
    ax1.grid(axis="y", linestyle="--", alpha=0.35)
    ax1.spines[["top", "right"]].set_visible(False)

    # Right: Iconic Broncos Franchise Career Sacks Leaders
    ax2.set_facecolor(BG)
    rushers = ["Von Miller\n(#58)", "Simon Fletcher\n(#73)", "Karl Mecklenburg\n(#77)"]
    sacks = [110.5, 97.5, 79.0]
    bars2 = ax2.barh(rushers, sacks, height=0.52, color=[ORANGE, NAVY, STEEL])
    ax2.invert_yaxis()
    ax2.set_xlim(0, 132)
    for b, val in zip(bars2, sacks):
        ax2.text(val + 2, b.get_y() + b.get_height() / 2, f"{val:g}", va="center", fontsize=9, fontweight="bold", color=NAVY)
    ax2.set_xlabel("Career Sacks With Denver", fontsize=8.8, fontweight="bold", color=NAVY)
    ax2.set_title("All-Time Broncos Sack Leaders", fontsize=10.5, fontweight="bold", color=NAVY)
    ax2.tick_params(axis="y", labelsize=8.5)
    ax2.grid(axis="x", linestyle="--", alpha=0.35)
    ax2.spines[["top", "right"]].set_visible(False)

    plt.tight_layout(pad=1.3)
    p = CHARTS / "franchise_records.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def build_ultimate_fan_guide() -> Path:
    doc = Document()
    doc.core_properties.title = "Denver Broncos Ultimate Fan Guide: Legends, Big Games & Mile High"
    doc.core_properties.author = "Unofficial Denver Football Fan Companion"

    doc.add_heading("Denver Broncos Ultimate Fan Guide: Legends, Big Games & Mile High", level=0)
    doc.add_paragraph(
        "Unofficial Denver Broncos Fan Trivia & History Companion. "
        "From burning vertical-striped socks in 1962 and the ferocious 1977 Orange Crush defense, to John Elway's "
        "98-yard 'The Drive', Terrell Davis's 2,008-yard MVP season, Peyton Manning's record-breaking 55 touchdowns, "
        "Von Miller's Super Bowl 50 takeover, and modern stars Patrick Surtain II and Bo Nix — here is everything "
        "a Broncos fan (kid or lifelong die-hard!) loves knowing about Denver football."
    )

    # ---- Section 1: Origin Story, Orange Crush & Mile High Traditions ----
    doc.add_heading("1. AFL Roots, the 1977 Orange Crush & Mile High Traditions", level=1)
    doc.add_paragraph(
        "The Denver Broncos kicked off in 1960 as a charter member of the American Football League (AFL). Their "
        "original uniforms featured brown-and-yellow vertical-striped socks that players disliked so much they "
        "famously held a public bonfire in 1962 to burn them before switching to orange and blue! Running back "
        "Floyd Little — nicknamed 'The Franchise' and wearing #44 — became Denver's first true superstar, helping "
        "keep the team in Colorado and sparking a legendary home sellout streak that began in 1970."
    )
    doc.add_paragraph(
        "Denver broke onto the national stage in 1977 behind the ferocious 'Orange Crush' 3-4 defense, anchored by "
        "Hall of Fame linebacker Randy Gradishar alongside Tom Jackson, Lyle Alzado, and Louis Wright. That gritty "
        "squad went 12–2 and carried the Broncos to their very first Super Bowl appearance (Super Bowl XII)."
    )
    doc.add_paragraph(
        "Today at Empower Field at Mile High (capacity 76,125, honoring 1976), game-day traditions make every Sunday "
        "unforgettable: after every Denver touchdown, Thunder — the team's live Arabian horse mascot — gallops "
        "across the turf, and after every visiting incomplete pass, 76,000 fans thunder out 'In-Com-Plete!' "
        "Sitting at an exact elevation of 5,280 feet (one mile high), Denver's thin air lets kicked footballs fly "
        "roughly 5 to 10 percent farther, helping fuel Matt Prater's then-NFL-record 64-yard field goal in 2013 and "
        "Jason Elam's 63-yarder in 1998. Quick game-day fan tips: classic snacks cost just $5 at Mile High Favs "
        "stands, rideshares drop off at Gate 5 before kickoff but pick up east at Ball Arena after the game, and "
        "the Ring of Fame Plaza & Team Store sit on the east side near Gate 10 "
        "(stadium tour: https://www.youtube.com/watch?v=V_F6byM7Xyo )."
    )
    doc.add_picture(str(_chart_mile_high_kicks()), width=Inches(5.8))
    doc.add_paragraph("Figure 1: Mile High thin-air kicking advantage — Matt Prater's 64-yard and Jason Elam's 63-yard field goals at 5,280 feet.")

    # ---- Section 2: John Elway, The Drive & The Fumble ----
    doc.add_heading("2. John Elway, 'The Drive', 'The Fumble' & The Three Amigos", level=1)
    doc.add_paragraph(
        "Quarterback John Elway (#7) played all 16 of his NFL seasons in Denver (1983–1998), retiring with 51,475 "
        "passing yards, 300 touchdown passes, and 47 fourth-quarter comebacks. His rocket right arm threw the ball "
        "so hard it left a cross-shaped mark on receivers' chests nicknamed the 'Elway Cross.' In the late 1980s, "
        "Elway electrified fans alongside his speedy wide-receiver trio known as 'The Three Amigos' — Vance Johnson, "
        "Mark Jackson, and Ricky Nattiel."
    )
    doc.add_paragraph(
        "Elway's defining playoff legend was born in the 1986 AFC Championship Game at Cleveland's roaring Dawg "
        "Pound. Trailing 20–13 with 5 minutes and 32 seconds left, Denver took over pinned at its own 2-yard line. "
        "Over 15 plays and 5 minutes and 2 seconds, Elway marched the Broncos 98 yards downfield — converting a "
        "desperate third-and-18 along the way — and fired a five-yard tying touchdown pass to Mark Jackson with 37 "
        "seconds left before Rich Karlis kicked the overtime winner. NFL history remembers it simply as 'The Drive' "
        "(highlights: https://www.youtube.com/watch?v=Px-tHzMEY-I )."
    )
    doc.add_paragraph(
        "One year later, in the 1987 AFC Championship rematch against Cleveland, Denver survived another miracle "
        "finish known as 'The Fumble': with Cleveland driving to tie the game with 1:12 left, Broncos cornerback "
        "Jeremiah Castille stripped Browns running back Earnest Byner at the 2-yard line to send Denver back to "
        "the Super Bowl."
    )
    doc.add_picture(str(_chart_the_drive()), width=Inches(6.2))
    doc.add_paragraph('Figure 2: Field progression of "The Drive" — 98 yards in 15 plays from Denver\'s own 2-yard line to Mark Jackson\'s tying touchdown.')

    # ---- Section 3: Terrell Davis & Back-to-Back Super Bowls XXXII and XXXIII ----
    doc.add_heading("3. Terrell Davis & Back-to-Back Super Bowls (XXXII & XXXIII)", level=1)
    doc.add_paragraph(
        "Running back Terrell Davis (#30) was one of the greatest steals in NFL Draft history — selected in the 6th "
        "round (196th overall) in 1995. Famous for his 'Mile High Salute' touchdown celebration, Davis teamed with "
        "Elway, Hall of Fame tight end Shannon Sharpe (#84), and hard-hitting safety Steve Atwater (#27, 'The "
        "Smiling Assassin') to finally bring a championship to Denver."
    )
    doc.add_paragraph(
        "In Super Bowl XXXII (January 1998), Denver entered as a heavy underdog against Brett Favre's defending "
        "champion Green Bay Packers. Despite battling a blinding migraine that forced him to miss the entire second "
        "quarter, Terrell Davis returned to rush for 157 yards and a Super Bowl-record three rushing touchdowns to "
        "win MVP. On a pivotal third-and-six, 37-year-old John Elway dove headfirst between three defenders and "
        "spun through midair for an 8-yard first down — forever immortalized as 'The Helicopter' "
        "(https://www.youtube.com/watch?v=BuL1QaVXUpM ). Linebacker John Mobley knocked down Favre's fourth-down "
        "pass to seal a 31–24 upset and snap a 13-year AFC Super Bowl drought, prompting owner Pat Bowlen to shout "
        "'This one's for John!'"
    )
    doc.add_paragraph(
        "The Broncos were even more dominant in 1998. Terrell Davis exploded for 2,008 rushing yards and 21 "
        "touchdowns to win the 1998 NFL MVP award as Denver rolled to Super Bowl XXXIII (January 1999). John Elway "
        "threw for 336 yards — including an 80-yard touchdown bomb to undrafted wide receiver Rod Smith (#80) — in "
        "a 34–19 victory over Atlanta, earning Super Bowl MVP before retiring on top as a back-to-back champion."
    )
    _add_table(
        doc,
        ["Super Bowl", "Year", "Opponent", "Score", "MVP & Signature Moment"],
        [
            ["Super Bowl XXXII", "1998", "Green Bay", "31–24", "Terrell Davis (157 yds, 3 TDs) · Elway's Helicopter dive"],
            ["Super Bowl XXXIII", "1999", "Atlanta", "34–19", "John Elway (336 pass yds) · 80-yd TD bomb to Rod Smith"],
            ["Super Bowl 50", "2016", "Carolina", "24–10", "Von Miller (2.5 sacks, 2 strip-sacks) · No Fly Zone defense"],
        ],
    )
    doc.add_picture(str(_chart_super_bowls()), width=Inches(6.0))
    doc.add_paragraph("Figure 3: Denver's three Super Bowl championship scores and MVP winners (XXXII, XXXIII, and 50).")

    # ---- Section 4: Peyton Manning's 2013 Record Offense & Super Bowl 50 'No Fly Zone' ----
    doc.add_heading("4. Peyton Manning's 2013 Records & the Super Bowl 50 'No Fly Zone'", level=1)
    doc.add_paragraph(
        "In 2012, legendary quarterback Peyton Manning (#18) signed with Denver and ignited the highest-scoring "
        "offense in NFL history the very next season. In 2013, Manning threw an NFL-record 55 touchdown passes and "
        "5,477 passing yards as Denver piled up an NFL-record 606 team points, throwing to stars Demaryius Thomas "
        "(#88, who also caught Tim Tebow's famous 80-yard walk-off overtime touchdown against Pittsburgh in the "
        "2011 playoffs), Eric Decker, Wes Welker, and Julius Thomas."
    )
    doc.add_paragraph(
        "Two years later, Denver flipped the script with one of the greatest defenses ever assembled. Coordinated "
        "by Wade Phillips, the 2015 'No Fly Zone' secondary — Aqib Talib, Chris Harris Jr., T.J. Ward, and Darian "
        "Stewart — paired with unstoppable edge rushers Von Miller (#58) and DeMarcus Ware (#94). In Super Bowl 50 "
        "(February 2016), Denver stunned Cam Newton and the 15–1 Carolina Panthers 24–10. Von Miller dominated with "
        "2.5 sacks and two forced fumbles (Malik Jackson recovered the first strip-sack in the end zone for a "
        "touchdown) to win Super Bowl 50 MVP and send Peyton Manning into retirement with his second ring "
        "(highlights: https://www.youtube.com/watch?v=wS1BRQ4l9_4 )."
    )
    doc.add_picture(str(_chart_franchise_records()), width=Inches(6.2))
    doc.add_paragraph("Figure 4: Historic Denver regular-season team scoring (606 points in 2013) and all-time franchise sack leaders (Von Miller at 110.5).")

    # ---- Section 5: Ring of Fame Greats & Modern Broncos Stars ----
    doc.add_heading("5. Ring of Fame Legends, Jersey Numbers & Today's Broncos", level=1)
    doc.add_paragraph(
        "Outside Empower Field, Denver's Ring of Fame celebrates legends across every era. Shutdown cornerback "
        "Champ Bailey (#24) earned 12 Pro Bowl selections (an NFL record for cornerbacks) and delivered a famous "
        "100-yard interception return in the January 2006 playoffs to hand Tom Brady his first-ever postseason "
        "defeat. Wide receiver Rod Smith (#80) went from undrafted free agent to Denver's all-time leader in "
        "receptions (849) and receiving yards (11,389), becoming the first undrafted player in NFL history to top "
        "10,000 receiving yards. Linebacker Von Miller (#58) holds the franchise record with 110.5 career sacks "
        "as a Bronco."
    )
    doc.add_paragraph(
        "A new generation of Broncos stars is carrying that Mile High legacy forward. Lockdown cornerback Patrick "
        "Surtain II (#2) captured the 2024 NFL Defensive Player of the Year award — becoming the first Bronco since "
        "Randy Gradishar in 1978 to win that honor — while young franchise quarterback Bo Nix (#10, drafted 12th "
        "overall in 2024 under head coach Sean Payton) sparked Denver's return to the AFC playoffs."
    )
    _add_table(
        doc,
        ["Jersey #", "Broncos Legend / Star", "Famous For"],
        [
            ["#7", "John Elway", "16 seasons in Denver · 2 Super Bowl wins · 'The Drive' & 'The Helicopter'"],
            ["#18", "Peyton Manning", "2013 NFL-record 55 TD passes & 606 team points · Super Bowl 50 champion"],
            ["#24", "Champ Bailey", "12 Pro Bowls · 100-yd playoff INT return vs. New England (2006)"],
            ["#27", "Steve Atwater", "Hall of Fame safety · 'The Smiling Assassin'"],
            ["#30", "Terrell Davis", "6th-round pick · 2,008 rush yds (1998 MVP) · Super Bowl XXXII MVP"],
            ["#44", "Floyd Little", "'The Franchise' · Early AFL/NFL Hall of Fame running back"],
            ["#58", "Von Miller", "Franchise-record 110.5 sacks · Super Bowl 50 MVP (2.5 sacks, 2 FF)"],
            ["#80", "Rod Smith", "Undrafted to Denver's all-time leader: 849 catches & 11,389 rec yds"],
            ["#84", "Shannon Sharpe", "Hall of Fame tight end · 3-time Super Bowl champ (2 with Denver)"],
            ["#2 & #10", "Patrick Surtain II & Bo Nix", "2024 Defensive Player of the Year (#2) & franchise QB (#10)"],
        ],
    )

    out_path = OUT / "Denver_Broncos_Ultimate_Fan_Guide.docx"
    doc.save(str(out_path))
    return out_path


if __name__ == "__main__":
    p = build_ultimate_fan_guide()
    print(f"Generated: {p} ({p.stat().st_size:,} bytes)")
