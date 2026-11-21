"""Generate two extra showcase Word reports for testers to try alongside the business report:

  1. samples/out/Lesson_Review_Space_Race_Apollo11.docx
     (Classroom / teacher lesson recap from "yesterday's class" — STEM + history)
  2. samples/out/GameDay_Staff_Guide_Denver_Tampa_Football.docx
     (Unofficial game-day staff orientation covering Denver Broncos & Tampa Bay Buccaneers
      stadium operations, landmarks, and football history — factual text + original charts,
      no trademarked logos.)

Run:
    backend/.venv/bin/python samples/make_demo_reports.py
"""
from __future__ import annotations

import io
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Inches, Pt, RGBColor

OUT_DIR = Path(__file__).resolve().parent / "out"
NAVY, ORANGE, RED, TEAL, AMBER, SLATE = "#0F2244", "#FB4F14", "#D50A0A", "#1F9E89", "#E8A33D", "#64748B"


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def _add_table(doc: Document, headers: list[str], rows: list[list[str]]) -> None:
    tbl = doc.add_table(rows=1 + len(rows), cols=len(headers))
    tbl.style = "Table Grid"
    for c, h in enumerate(headers):
        cell = tbl.rows[0].cells[c]
        cell.text = h
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for r_idx, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            tbl.rows[r_idx].cells[c].text = val
    doc.add_paragraph("")


def _add_bullets(doc: Document, items: list[str]) -> None:
    for item in items:
        doc.add_paragraph(item, style="List Bullet")


# =============================================================================
# 1. TEACHER LESSON REVIEW: THE SPACE RACE & APOLLO 11
# =============================================================================

def chart_space_milestones_timeline() -> io.BytesIO:
    missions = ["Sputnik 1\n(1957)", "Vostok 1\n(1961)", "Freedom 7\n(1961)", "Gemini 4\n(1965)", "Apollo 8\n(1968)", "Apollo 11\n(1969)"]
    apogee_km = [939, 327, 187, 296, 377349, 384400]
    # Plot log-scaled peak altitude / distance reached (km) so LEO vs lunar distance is unmistakable
    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    colors = [SLATE, SLATE, TEAL, TEAL, AMBER, ORANGE]
    bars = ax.bar(missions, apogee_km, color=colors)
    ax.set_yscale("log")
    ax.set_ylim(100, 1_500_000)
    for b, v in zip(bars, apogee_km):
        label = f"{v:,} km"
        ax.text(b.get_x() + b.get_width() / 2, v * 1.25, label, ha="center", fontsize=8.5, fontweight="bold", color=NAVY)
    ax.set_ylabel("Peak distance from Earth (km, log scale)")
    ax.set_title("From Low Earth Orbit to the Moon: Peak Mission Distance (1957–1969)", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    return _png(fig)


def chart_apollo_budget_share() -> io.BytesIO:
    years = ["1961", "1962", "1963", "1964", "1965", "1966", "1967", "1968", "1969"]
    pct = [0.8, 1.2, 2.3, 3.5, 4.3, 4.4, 3.4, 2.7, 2.3]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.plot(years, pct, marker="o", color=NAVY, linewidth=2.4)
    ax.fill_between(years, pct, alpha=0.12, color=TEAL)
    for y, v in zip(years, pct):
        ax.text(y, v + 0.18, f"{v:.1f}%", ha="center", fontsize=8.5, color=NAVY, fontweight="bold")
    ax.set_ylim(0, 5.3)
    ax.set_ylabel("NASA share of U.S. federal budget (%)")
    ax.set_title("NASA Share of the U.S. Federal Budget During the Space Race", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    return _png(fig)


def build_space_lesson() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.core_properties.title = "Lesson Review: The Space Race & Apollo 11"

    title = doc.add_paragraph("Lesson Review: The Space Race & Apollo 11")
    title.style = "Title"
    sub = doc.add_paragraph("Class Recap Handout — Yesterday's Unit 4 Lecture (History & Applied Science)")
    sub.style = "Subtitle"

    doc.add_heading("1. Yesterday's Big Question & Cold War Context", level=1)
    doc.add_paragraph(
        "In yesterday's class, we examined how the Space Race grew directly out of Cold War competition between "
        "the United States and the Soviet Union. On October 4, 1957, the Soviet Union stunned the world by launching "
        "Sputnik 1, an 83.6-kilogram metal sphere that orbited Earth every 96 minutes and broadcast simple radio beeps. "
        "In response, the U.S. Congress passed the National Aeronautics and Space Act of 1958, establishing NASA to "
        "coordinate civilian spaceflight and fund science education across American schools."
    )
    doc.add_paragraph(
        "The Soviet Union claimed several early firsts: Yuri Gagarin became the first human in space aboard Vostok 1 "
        "on April 12, 1961, completing one orbit in 108 minutes, and Valentina Tereshkova became the first woman in "
        "space in June 1963. Less than a month after Gagarin's flight, Alan Shepard flew a 15-minute suborbital hop "
        "in Freedom 7, and President John F. Kennedy addressed Congress on May 25, 1961, setting the national goal "
        "of landing a human on the Moon and returning them safely to Earth before the decade was out."
    )

    doc.add_heading("2. Stepping Stones: Mercury and Gemini", level=1)
    doc.add_paragraph(
        "Before NASA could attempt a lunar landing, engineers had to solve four survival and navigation problems: "
        "launching humans into orbit, surviving multi-day weightlessness, performing spacewalks (extravehicular "
        "activity), and rendezvousing and docking two spacecraft traveling at 28,000 kilometers per hour. Project "
        "Mercury proved humans could function in orbit—John Glenn orbited Earth three times in Friendship 7 in "
        "February 1962—while Project Gemini served as the two-astronaut proving ground between 1965 and 1966."
    )
    _add_table(doc, ["Program", "Years", "Crew Size", "Core Engineering Goal", "Notable Milestone"], [
        ["Project Mercury", "1958–1963", "1 astronaut", "Prove humans can survive launch and orbit", "John Glenn orbits Earth 3 times (1962)"],
        ["Project Gemini", "1961–1966", "2 astronauts", "Master spacewalks, long duration, and orbital docking", "Ed White's first U.S. spacewalk (Gemini 4, 1965); Gemini 8 first docking (1966)"],
        ["Project Apollo", "1961–1972", "3 astronauts", "Land humans on the Moon and return safely", "Apollo 11 lunar landing on July 20, 1969"],
    ])
    doc.add_picture(chart_space_milestones_timeline(), width=Inches(6.0))
    doc.add_paragraph("Figure 1: Peak distance from Earth across six landmark missions, showing the leap from low Earth orbit to lunar distance (384,400 km).")

    doc.add_heading("3. The Saturn V Rocket & Lunar Orbit Rendezvous", level=1)
    doc.add_paragraph(
        "A key debate we covered yesterday was how to physically get to the Moon and back. Early proposals favored "
        "Direct Ascent (a single colossal rocket flying straight to the Moon) or Earth Orbit Rendezvous (assembling "
        "a ship in Earth orbit). NASA engineer John Houbolt championed a third architecture—Lunar Orbit Rendezvous "
        "(LOR)—in which a mother ship stayed in lunar orbit while a lightweight, specialized Lunar Module descended "
        "to the surface. LOR saved immense mass and made the 1969 deadline achievable."
    )
    doc.add_paragraph(
        "To launch the Apollo stack, Wernher von Braun's team at Marshall Space Flight Center built the Saturn V, "
        "standing 110.6 meters (363 feet) tall and generating 34.5 meganewtons (7.6 million pounds) of thrust at "
        "liftoff from five F-1 engines burning RP-1 kerosene and liquid oxygen. The Saturn V burned roughly 20 tons "
        "of propellant per second during its first two and a half minutes of flight and remains the only launch "
        "vehicle to have carried humans beyond low Earth orbit."
    )

    doc.add_heading("4. Apollo 11: Crew, Descent, and the 1202 Alarm", level=1)
    doc.add_paragraph(
        "Apollo 11 launched from Kennedy Space Center on July 16, 1969, carrying Commander Neil Armstrong, Lunar "
        "Module Pilot Buzz Aldrin, and Command Module Pilot Michael Collins. While Collins remained in lunar orbit "
        "aboard Columbia (the Command Module), Armstrong and Aldrin undocked in the Lunar Module Eagle on July 20, 1969, "
        "aiming for the flat volcanic plain of the Sea of Tranquility."
    )
    doc.add_paragraph(
        "During the final minutes of powered descent, the Apollo Guidance Computer triggered 1202 and 1201 program "
        "alarms because a rendezvous radar switch had been left on, flooding the computer with extra processing tasks. "
        "Thanks to priority-scheduling software led by Margaret Hamilton—and quick verification by 26-year-old guidance "
        "officer Steve Bales in Mission Control—the computer shed low-priority tasks and kept flying. Spotting a "
        "boulder-strewn crater at the automatic landing site, Armstrong took semi-manual control and touched down "
        "with roughly 25 seconds of landing fuel remaining."
    )
    _add_bullets(doc, [
        "Touchdown occurred at 20:17 UTC on July 20, 1969; Armstrong stepped onto the lunar surface about six and a half hours later.",
        "Armstrong and Aldrin spent 2 hours and 31 minutes walking on the Moon and collected 21.55 kilograms (47.5 pounds) of lunar rock and soil.",
        "An estimated 650 million people worldwide watched the televised moonwalk—roughly one-fifth of the global population at the time.",
        "The crew splashed down safely in the Pacific Ocean on July 24, 1969, and spent 21 days in biological quarantine.",
    ])

    doc.add_heading("5. Cost, People, and Everyday Spin-Offs", level=1)
    doc.add_paragraph(
        "At its peak in 1966, NASA accounted for 4.4% of the entire U.S. federal budget—a level never reached before "
        "or since—and Project Apollo employed more than 400,000 engineers, technicians, seamstresses, and factory "
        "workers across over 20,000 contractors and universities. Remember from yesterday's discussion: the Playtex "
        "division that made everyday apparel actually hand-sewed the 21-layer Apollo spacesuits to sub-millimeter tolerances."
    )
    doc.add_picture(chart_apollo_budget_share(), width=Inches(6.0))
    doc.add_paragraph("Figure 2: NASA's share of the U.S. federal budget peaked at 4.4% in 1966 before declining after the landing architecture matured.")
    doc.add_paragraph(
        "Beyond geopolitics, Apollo accelerated integrated circuits (miniature computer chips), digital fly-by-wire "
        "aviation controls, freeze-dried food preservation, fire-resistant textiles, and medical telemetry sensors. "
        "In total, six Apollo missions (Apollo 11, 12, 14, 15, 16, and 17) landed 12 astronauts on the Moon between "
        "1969 and 1972, returning 382 kilograms of lunar samples that transformed planetary science."
    )

    out = OUT_DIR / "Lesson_Review_Space_Race_Apollo11.docx"
    doc.save(str(out))
    return out


# =============================================================================
# 2. SPORTS STADIUM & TEAM HISTORY ORIENTATION (DENVER & TAMPA BAY)
# =============================================================================

def chart_stadium_comparison() -> io.BytesIO:
    metrics = ["Seating Capacity\n(thousands)", "Elevation\n(hundreds of ft)", "Super Bowl\nChampionships"]
    denver = [76.1, 52.8, 3.0]
    tampa = [69.2, 0.4, 2.0]
    fig, ax = plt.subplots(figsize=(7.4, 3.7))
    x = range(len(metrics))
    b1 = ax.bar([i - 0.19 for i in x], denver, 0.36, label="Denver (Mile High)", color=ORANGE)
    b2 = ax.bar([i + 0.19 for i in x], tampa, 0.36, label="Tampa Bay (Raymond James)", color=RED)
    for b, v in zip(b1, denver):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.4, f"{v:g}", ha="center", fontsize=9, fontweight="bold", color=NAVY)
    for b, v in zip(b2, tampa):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.4, f"{v:g}", ha="center", fontsize=9, fontweight="bold", color=NAVY)
    ax.set_xticks(list(x), metrics)
    ax.set_ylim(0, 88)
    ax.set_title("Denver vs. Tampa Bay: Stadium & Championship Snapshot", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    return _png(fig)


def chart_super_bowl_margins() -> io.BytesIO:
    games = ["SB XXXII\nDenver (1998)", "SB XXXIII\nDenver (1999)", "SB XXXVII\nTampa (2003)", "SB 50\nDenver (2016)", "SB LV\nTampa (2021)"]
    winner_pts = [31, 34, 48, 24, 31]
    opp_pts = [24, 19, 21, 10, 9]
    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    x = range(len(games))
    b1 = ax.bar([i - 0.18 for i in x], winner_pts, 0.35, label="Winning Team Score", color=NAVY)
    b2 = ax.bar([i + 0.18 for i in x], opp_pts, 0.35, label="Opponent Score", color=SLATE)
    for b, v in zip(b1, winner_pts):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.8, str(v), ha="center", fontsize=9, fontweight="bold", color=NAVY)
    for b, v in zip(b2, opp_pts):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.8, str(v), ha="center", fontsize=9, color=SLATE)
    ax.set_xticks(list(x), games, fontsize=8.5)
    ax.set_ylim(0, 55)
    ax.set_ylabel("Points scored")
    ax.set_title("All Five Super Bowl Victories: Denver & Tampa Bay Final Scores", fontweight="bold", color=NAVY)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    return _png(fig)


def build_sports_handbook() -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.core_properties.title = "Game-Day Staff Guide: Mile High & Tampa Bay Football History"

    title = doc.add_paragraph("Game-Day Staff Guide: Mile High & Tampa Bay Football")
    title.style = "Title"
    sub = doc.add_paragraph("Unofficial Orientation Packet — Stadium Operations, Landmarks & Franchise History")
    sub.style = "Subtitle"

    doc.add_heading("1. Two Iconic Stadiums Every Staff Member Should Know", level=1)
    doc.add_paragraph(
        "Welcome to game-day operations! Fans love asking staff about stadium landmarks and history, so this "
        "handbook covers two of pro football's most recognizable home venues: Empower Field at Mile High in Denver, "
        "Colorado, and Raymond James Stadium in Tampa, Florida. Opened in 2001 to replace the beloved original Mile "
        "High Stadium, Denver's venue sits at an elevation of 5,280 feet—exactly one mile above sea level—with an "
        "official seating capacity of 76,125 (chosen deliberately to honor the team's 1976 roots and the iconic "
        "number 76). Its steel-tread upper deck was engineered to preserve the thunderous stomping noise of the old "
        "stadium."
    )
    doc.add_paragraph(
        "Nearly at sea level (about 40 feet elevation), Raymond James Stadium opened in 1998 and seats roughly "
        "69,200 fans for regular football games, expanding past 75,000 for Super Bowls. Its unmistakable centerpiece "
        "in the north end zone—Buccaneer Cove—is a 103-foot-long, 43-ton steel-and-concrete replica pirate ship "
        "equipped with eight working cannons. Whenever Tampa Bay enters the opponent's 20-yard red zone, a cannon "
        "fires once; after every score, the cannons fire once per point scored on the play."
    )
    doc.add_picture(chart_stadium_comparison(), width=Inches(6.0))
    doc.add_paragraph("Figure 1: Side-by-side snapshot comparing Denver's Mile High altitude and seating capacity against Tampa Bay's Raymond James Stadium.")

    doc.add_heading("2. Championship Eras & Super Bowl Milestones", level=1)
    doc.add_paragraph(
        "Between them, Denver and Tampa Bay own five Lombardi Trophies, each with a memorable storyline fans ask "
        "about on stadium tours. Denver broke a 13-year AFC Super Bowl drought by winning back-to-back titles in "
        "Super Bowl XXXII (defeating Green Bay 31–24 in January 1998) and Super Bowl XXXIII (beating Atlanta 34–19 "
        "in January 1999). Seventeen years later, Denver captured Super Bowl 50 with a 24–10 victory over Carolina, "
        "sending Peyton Manning into retirement as a champion."
    )
    doc.add_paragraph(
        "Tampa Bay is undefeated in its two Super Bowl appearances. In Super Bowl XXXVII (January 2003), Tampa Bay "
        "overwhelmed Oakland 48–21 behind five interceptions, three of which were returned for touchdowns. Eighteen "
        "years later in Super Bowl LV (February 2021), Tampa Bay beat Kansas City 31–9—making history as the very "
        "first team ever to play and win a Super Bowl in its own home stadium, Raymond James Stadium."
    )
    _add_table(doc, ["Super Bowl", "Season / Date", "Champion", "Opponent & Score", "Super Bowl MVP"], [
        ["Super Bowl XXXII", "Jan 1998", "Denver Broncos", "Green Bay Packers (31–24)", "Terrell Davis (RB)"],
        ["Super Bowl XXXIII", "Jan 1999", "Denver Broncos", "Atlanta Falcons (34–19)", "John Elway (QB)"],
        ["Super Bowl XXXVII", "Jan 2003", "Tampa Bay Buccaneers", "Oakland Raiders (48–21)", "Dexter Jackson (S)"],
        ["Super Bowl 50", "Feb 2016", "Denver Broncos", "Carolina Panthers (24–10)", "Von Miller (LB)"],
        ["Super Bowl LV", "Feb 2021", "Tampa Bay Buccaneers", "Kansas City Chiefs (31–9)", "Tom Brady (QB)"],
    ])
    doc.add_picture(chart_super_bowl_margins(), width=Inches(6.0))
    doc.add_paragraph("Figure 2: Final scores across all five Denver and Tampa Bay Super Bowl championships (1998–2021).")

    doc.add_heading("3. Quarterbacks, Playmakers & Offensive Records", level=1)
    doc.add_paragraph(
        "Denver's offensive legacy starts with Hall of Fame quarterback John Elway, who spent all 16 seasons (1983–1998) "
        "in Denver, engineered the famous 98-yard 'The Drive' in the 1986 AFC Championship Game in Cleveland, and "
        "retired after winning back-to-back Super Bowls. Beside him stood running back Terrell Davis, who rushed for "
        "2,008 yards in 1998 to win league MVP and still holds the highest career postseason rushing average in NFL "
        "history (142.5 yards per game). Later, Peyton Manning led Denver's 2013 offense to single-season NFL records "
        "with 55 touchdown passes, 5,477 passing yards, and 606 team points."
    )
    doc.add_paragraph(
        "In Tampa Bay, wide receiver Mike Evans became the cornerstone of modern franchise history by opening his "
        "career with 11 consecutive 1,000-yard receiving seasons (2014–2024), tying Jerry Rice's all-time record for "
        "consecutive 1,000-yard years. Long before Tom Brady arrived in 2020 to throw 40 regular-season touchdowns "
        "and capture his seventh Super Bowl ring, fullback Mike Alstott—nicknamed the 'A-Train'—earned six Pro Bowl "
        "selections and became a Tampa Bay fan favorite for bulldozing short-yardage defenses."
    )

    doc.add_heading("4. Defensive Identities: Orange Crush, No Fly Zone & Tampa 2", level=1)
    doc.add_paragraph(
        "Both franchises built championship identities around iconic defenses. Denver's 1977 'Orange Crush' 3–4 "
        "defense—led by Randy Gradishar, Tom Jackson, and Lyle Alzado—carried the team to its first Super Bowl "
        "appearance. Nearly four decades later, the 2015 'No Fly Zone' secondary (Aqib Talib, Chris Harris Jr., "
        "T.J. Ward, and Darian Stewart) paired with pass rushers Von Miller and DeMarcus Ware to dominate Super Bowl "
        "50, where Miller posted 2.5 sacks and two forced fumbles."
    )
    doc.add_paragraph(
        "Tampa Bay's 2002 defense perfected the 'Tampa 2' scheme designed by Tony Dungy and defensive coordinator "
        "Monte Kiffin, in which the middle linebacker drops deep down the seam while a relentless four-man front "
        "pressures the quarterback. Four defenders from that 2002 juggernaut are now in the Pro Football Hall of "
        "Fame: defensive tackle Warren Sapp, linebacker Derrick Brooks (the 2002 NFL Defensive Player of the Year), "
        "safety John Lynch, and cornerback Ronde Barber, whose 92-yard interception return closed Veterans Stadium "
        "in the NFC Championship Game."
    )

    doc.add_heading("5. Game-Day Traditions, Weather & Guest Operations", level=1)
    doc.add_paragraph(
        "Guest-services teams at both stadiums prepare for dramatically different environments. In Denver, the thin "
        "mile-high air reduces oxygen density by roughly 17% compared to sea level, causing kicks to travel slightly "
        "farther and making hydration critical for fans visiting from lower elevations. Denver has maintained one "
        "of the longest consecutive home sellout streaks in American sports—dating back to 1970—and greets fans "
        "with Thunder, a purebred white Arabian gelding who gallops across the field after every home touchdown, "
        "alongside the crowd's booming 'In-Com-Plete!' chant after opposing pass drops."
    )
    doc.add_paragraph(
        "In Tampa Bay, game-day crews train extensively on Florida lightning and heat protocols: whenever lightning "
        "strikes within 8 miles of Raymond James Stadium, play pauses and fans move to covered concourses for at "
        "least 30 minutes after the last strike. During the 1997 rebrand, Tampa Bay famously traded its original "
        "1976 'Creamsicle' Florida orange jerseys (featuring mascot Bucco Bruce) for pewter and red—though the "
        "beloved Creamsicle throwbacks still return for special heritage games every season."
    )
    _add_bullets(doc, [
        "Denver's Ring of Fame Plaza outside the south entrance honors franchise greats with bronze pillars and plaques.",
        "Tampa Bay's Ring of Honor wraps the upper stadium fascia and inducted Lee Roy Selmon—the franchise's very first draft pick in 1976—as its inaugural member.",
        "Visiting fans at Raymond James Stadium can tour Buccaneer Cove on the north concourse before kickoff.",
        "Both franchises have won Super Bowls in multiple decades, and John Lynch uniquely holds honors with both Denver and Tampa Bay after starring in Tampa and finishing his playing career with four Pro Bowl seasons in Denver.",
    ])

    out = OUT_DIR / "GameDay_Staff_Guide_Denver_Tampa_Football.docx"
    doc.save(str(out))
    return out


if __name__ == "__main__":
    p1 = build_space_lesson()
    p2 = build_sports_handbook()
    print("Built:", p1, p1.stat().st_size, "bytes")
    print("Built:", p2, p2.stat().st_size, "bytes")
