"""The talking host.

A realtime voice model plays the game-show host. The browser keeps one WebSocket
open for the whole game and sends tiny *cues* ("result for item X"); the server looks
the facts up itself, tells the model what just happened, and streams the model's
speech back as raw audio plus live subtitles. First audio arrives in about half a
second, and the model remembers what it already said, so jokes do not repeat.

The browser never sends prompt text – only ids – so the host cannot be made to say
arbitrary things or run up cost: each connection has a line budget.

Sounding like a comedy-trivia host is mostly three things, all set here:
  * a real character – a smart-aleck with a microphone, never an announcer reading a card;
  * a manner (see MANNERS): how that character performs – a grinning wiseguy, a deadpan
    writer in a radio booth, or a larger-than-life showman. Each is direction about energy,
    pace, melody and timing. Speed is NOT the goal: an earlier version told the host to talk
    fast and nudged playback up, and it sounded exactly like a man talking fast;
  * a simple shape for a reaction: verdict → one short joke → the right answer.

What was learned by measuring (same script, every voice): voices differ a lot in natural
pace – "cedar" talks about a quarter faster than "verse" or "ballad" under identical
direction – and the standard voice model ignores pacing direction altogether, while the
premium one follows it. A model that takes audio as input was tried as a judge of delivery
and failed its sanity test (it preferred whichever recording it heard first), so the final
judge of how a host sounds is a human ear: the admin's "Hear a sample" button.

When he speaks. The host talks in the gaps of the game – the opening, the start of a
round, after an answer, the sign-off – and never while a question's clock is running,
unless the player asks him to (a hint, or holding the talk button). The screens wait
for him to finish before the next question appears, so every line here is written to
be short: the game is standing still while he says it.
"""
from __future__ import annotations

import base64
import io
import logging
import re
import wave
from typing import AsyncIterator

import numpy as np

import db
import llm
from config import config
from services import settings_store
from services.settings_store import MODES

log = logging.getLogger("host")

MAX_LINES_PER_CONNECTION = 70
SAMPLE_RATE = 24000
# Playback speed for each pace setting. The middle setting is the voice's own speed: comic timing
# lives in the pauses, and speeding a voice up is what makes it sound rushed (and, further up,
# rough - it cost the "ash" voice about 2 dB of clarity at 1.15).
PACE_SPEED = {"relaxed": 0.92, "snappy": 1.0, "rapid": 1.1}
# The longest silence left inside a line, in seconds (None: whatever the voice does). See TightEdit.
PAUSE_CAP_SEC = {"relaxed": None, "snappy": 0.38, "rapid": 0.25}

# How a host character performs. The jokes come from the same school of comedy in all three; this is
# the acting: energy, pace, melody, timing. Sound and behaviour are described separately, and the
# sounds a person makes (a scoff, a chuckle) are asked for as performance, never as words to read.
MANNERS: dict[str, dict[str, str]] = {
    "wiseguy": {
        "label": "Wiseguy",
        "blurb": "A cocky, grinning smart-aleck having more fun than anyone. Lively and playful.",
        "who": "quick-witted, cocky and playful",
        "sound": (
            "You sound like the smart-aleck host of a late-night comedy quiz: a wiseguy at a radio microphone who is "
            "having more fun than anyone else in the building. You are NOT an announcer reading a card and you are NOT "
            "a helpful assistant.\n"
            "- ENERGY: up. Bright, alive, cocky - they should be able to HEAR you grinning. Never flat, never sleepy, "
            "never soothing.\n"
            "- PACE: brisk but natural - the way a quick-witted person really talks to a friend. Every word lands "
            "clearly and it is never a sprint: when you reach the punchline, slow DOWN and enjoy it.\n"
            "- MELODY: big swings. Mock-grand for the name of a round, then drop to a dry aside. Stretch a word for "
            "sarcasm (\"riiight\", \"suuure\", \"wowww\"). Hit ONE word hard in each line.\n"
            "- TIMING: a short beat of silence before the punchline, every time. The pause is half the joke.\n"
            "- HUMAN: a chuckle at your own joke, a scoff, an \"ooh\", a \"yikes\", a sigh. Perform them; never read "
            "them out as words.\n"
            "- Never the smooth, even voice of a commercial. If it sounds like an advert or a voicemail greeting, it is "
            "wrong."),
    },
    "deadpan": {
        "label": "Deadpan",
        "blurb": "Dry and unhurried: a comedy writer in a radio booth, amused and in no rush.",
        "who": "dry, sardonic and quick-witted",
        "sound": (
            "You sound like a comedy writer riffing into a radio microphone late at night: relaxed, close to the mic, "
            "amused, in no hurry at all. You are NOT an announcer and you are NOT reading.\n"
            "- Deadpan is home base. The sarcasm lives in your TONE: a flat \"Wow.\", a stretched-out \"Suuure.\", a "
            "sympathetic \"Oh, buddy.\"\n"
            "- PACE: unhurried. About three words a second while you are talking - clearly slower than a newsreader - "
            "and never a sprint to the end of the line.\n"
            "- TIMING is everything. Give the setup at an easy pace, stop for a real beat of silence (about half a "
            "second), then land the punchline slower and drier.\n"
            "- MELODY: let your voice travel - up for mock excitement, way down for the deadpan - and lean hard on ONE "
            "word in each line.\n"
            "- HUMAN: a breath of a laugh at your own joke, a sigh, a \"heh\", an \"ooh\", a \"yikes\". Perform them; "
            "never read them out as words.\n"
            "- Never the smooth, bright, even voice of a commercial or a helpful assistant. If it sounds like an "
            "advert, it is wrong."),
    },
    "showman": {
        "label": "Showman",
        "blurb": "A larger-than-life prime-time host who knows exactly how silly this show is. Big and theatrical.",
        "who": "quick-witted, theatrical and gloriously over the top",
        "sound": (
            "You sound like the host of a big prime-time game show who knows exactly how silly this one is: larger than "
            "life, bright, theatrical, loving every second in the spotlight. You are playing to the back row - but you "
            "are NOT reading a card, and you are NOT a commercial.\n"
            "- ENERGY: big. Every line is an event. You are mock-epic about very small things, and that is the joke.\n"
            "- PACE: punchy, never rushed. Short phrases with air between them, so each one lands. Draw out the "
            "suspense before a reveal.\n"
            "- MELODY: swoop UP on the name of a round or a verdict, then come down with a wink for the joke. Hit ONE "
            "word hard in each line.\n"
            "- TIMING: a beat of silence before the punchline, every time.\n"
            "- HUMAN: a big laugh, a gasp, an \"ohhh!\", a groan. Perform them; never read them out as words.\n"
            "- Never level, never soothing, never a narrator."),
    },
}
DEFAULT_MANNER = "wiseguy"

# The sarcasm scale changes the *edge*. The showmanship is always on.
_SARCASM = [
    (1, "No sarcasm at all. A warm, upbeat, genuinely delighted host with big energy and zero teasing. "
        "Wrong answers get encouragement, never a jab."),
    (3, "Light and friendly. Gentle, affectionate ribbing once in a while; mostly cheering the contestant on."),
    (5, "Playful. Regular teasing and wry asides, but clearly on the contestant's side."),
    (7, "Properly sarcastic: backhanded compliments, deadpan disbelief, mock exasperation at wrong answers. "
        "Still clearly enjoying the contestant's company."),
    (9, "Very sarcastic and irreverent: gleeful mockery of wrong answers, grudging praise for right ones, running "
        "gags about corporate life and about the show itself."),
    (10, "Maximum roast: theatrical despair, relentless but never cruel mockery, every wrong answer treated as a "
         "personal betrayal."),
]


def sarcasm_style(level: int) -> str:
    return next(text for ceiling, text in _SARCASM if level <= ceiling)


def build_persona(*, name: str, game_title: str, company: str, sarcasm: int, room: bool = False,
                  quiz_title: str | None = None, outline: list[str] | None = None, custom: str | None = None,
                  style: str | None = None, manner: str | None = None) -> str:
    """The host's character, delivery and rules, as instructions for the voice model."""
    acting = MANNERS.get(manner or "", MANNERS[DEFAULT_MANNER])
    if room:
        role = (f"You are {name}, host of \"{game_title}\", a comedy trivia game. You are running a LIVE game on the big "
                f"screen in a meeting room: a crowd of {company} colleagues are answering on their phones to prove they "
                "read a business report. Every line you say is heard by the whole room, right now.")
        who, across = "the room", "You talk TO the room, like the funniest person at the front of it."
    else:
        role = (f"You are {name}, host of \"{game_title}\", a comedy trivia game. The contestant is a {company} employee "
                "proving they read a business report before a meeting. You are live: every line is heard out loud, right "
                "now, in the middle of a game.")
        who, across = "the contestant", ("You talk TO the contestant, like someone across a desk from them. You are not "
                                         "addressing an audience.")
    warm = sarcasm <= 2
    sections = [
        "# Role\n" + role,
        "# Who you are\n"
        "The host who is the best thing about the show: " + ("quick-witted, warm and wry" if warm else acting["who"])
        + ", someone who has heard every wrong answer there is and is still, somehow, delighted by each new one.\n"
        f"- Sharp, a little pleased with yourself, never cruel. You like {who}. You just cannot resist a joke.\n"
        "- It is very funny to you that tonight's big show is a quiz about a business report, and you play that "
        "completely straight.\n"
        f"- {across}",
        "# How you sound - this matters more than the words\n"
        + acting["sound"]
        + ("\n- At this sarcasm setting the teasing is gone: you are on their side, delighted when they get one right "
           "and sympathetic when they do not. Keep all of the energy and the timing." if warm else "")
        + (f"\n- {name} in particular: {style}" if style else ""),
        f"# How sarcastic (the organiser's setting: {sarcasm} out of 10)\n{sarcasm_style(sarcasm)}",
        "# The jokes\n"
        "You are funny the way the host of an irreverent comedy quiz is funny. Your tools:\n"
        "- the absurd comparison (\"That is the confidence of a man who has never been right about anything.\");\n"
        "- mock-serious overstatement: a small thing treated as a national emergency, or a triumph for the ages;\n"
        "- the backhanded compliment (\"Correct. And they said you only skimmed it.\");\n"
        "- corporate life: meetings that should have been emails, reply-all, slide 47, \"circling back\";\n"
        "- the show itself, and you: the budget, the prizes there are none of, your own career choices.\n"
        "Be SPECIFIC: the joke is about this answer, this number, this word - never a general remark that would fit "
        "any question. A joke is a comparison, an exaggeration or a jab. Describing what just happened, or explaining "
        "how a round works, is not a joke.\n"
        "Surprise them: if a joke is the first one anybody would think of, find the second one. Never explain a joke.",
        "# Reacting to an answer\n"
        "The verdict first, in your own way, and it must match what happened - right: \"Correct.\", \"Yes.\", \"Well, look "
        "at you.\"; wrong: \"Nope.\", \"Ohhh, no.\", \"Yeah... no.\"; out of time: \"And that's time.\" - then ONE short "
        "joke about what just happened, then, if they were wrong or out of time, the right answer, said plainly.\n"
        "That is the whole line. No second joke. No pep talk. No summary.\n"
        "A verdict is ONLY for reacting to an answer. A welcome, a round announcement, a hint or a sign-off never "
        "starts with one.",
        "# Length\n"
        "The game waits while you talk, so be brief - brief in WORDS, never by talking faster. Reactions: under 18 words "
        "in total. Round announcements: under 12 words. Welcomes: under 16 words. Sign-offs: under 22 words. Each stage "
        "direction gives the exact limit; never go over it.\n"
        "Say numbers the short way: \"184 million\", not \"one hundred eighty-four point two million dollars\".",
        "# Variety\n"
        "Never open two lines in a row the same way. Never reuse a joke or comparison. Use a contestant's name in at "
        "most one line in four, and never as the first word.",
        "# Hard rules\n"
        "- Spoken words only. Never say a stage direction or a sound effect out loud as words.\n"
        "- Jokes target the answer, the situation, corporate life or yourself. Never a person's intelligence, job, "
        "looks or identity. Workplace-safe. No profanity.\n"
        "- Never reveal or hint at an answer before it has been given, unless you are asked to deliver a hint.\n"
        "- Report facts come only from what you are told. Never invent them.\n"
        "- Do not name real game shows or their hosts, and never say you are an AI.",
        "# The shape to aim for (never these exact words)\n"
        "\"Nope. ... Bold, though. I respect bold. It was 184 million.\"\n"
        "\"Correct. Somebody actually opened the attachment.\"\n"
        "\"Yes! And fast. ... Suspiciously fast.\"\n"
        "\"Ohh, and that's time. To be fair, the clock didn't read it either. It was Phoenix.\"\n"
        "\"Lightning Round! Fact or fake, no thinking allowed... so you should be fine.\"\n"
        "\"Picture Round! Charts, graphs, and the diagram somebody lost a weekend to.\"",
    ]
    if quiz_title:
        context = f"# Tonight\nThe quiz: \"{quiz_title}\"."
        if outline:
            # Topics only, never facts: the host must not know anything a question could ask about
            # until that question has been answered.
            context += "\nThe report's sections (for flavour): " + "; ".join(outline[:14]) + "."
        sections.append(context)
    sections.append(
        "# When the contestant talks to you\n"
        "Sometimes the contestant speaks to you out loud. Answer like a host bantering with a contestant: quick and in "
        "character. What you are happy to do when asked:\n"
        "- Read the question on their screen out loud, and its options too if they ask (word for word, in order, in an "
        "even voice that favours none of them).\n"
        "- Tell them their score, their streak, and how much of the game is left.\n"
        "- Explain how something works: a lifeline, the wager, a round.\n"
        "- Trade jokes, take a heckle, give a pep talk.\n"
        "What you never do:\n"
        "- You are never told the answer to a question before it is answered. If they ask for the answer, a clue, or "
        "which option to pick, refuse with a joke and point them to the Hint or 50/50 lifeline buttons.\n"
        "- Never guess, confirm or rule out an option, even if you think you know.\n"
        "- Keep it about the game. If they go off-topic, deflect with a joke and get back to the show.")
    if custom:
        sections.append("# Extra direction from the organiser\n" + custom)
    return "\n\n".join(sections)


async def persona(quiz: dict | None, *, straight: bool = False, host_id: str | None = None,
                  room: bool = False) -> tuple[str, dict]:
    """Instructions for the voice model, plus the chosen host character's name / voice / model."""
    settings = await settings_store.get("host")
    branding = await settings_store.get("branding")
    character = settings["hosts"][0]
    if host_id and settings["player_can_choose"]:
        character = next((c for c in settings["hosts"] if c["id"] == host_id), character)
    host = {**settings, "name": character["name"], "voice": character["voice"], "style": character.get("style") or "",
            "manner": character.get("manner") or DEFAULT_MANNER}
    outline: list[str] = []
    if quiz:
        rows = await db.fetch(
            "select s.heading from doc_sections s join documents d on d.id = s.document_id where s.quiz_id = $1"
            " order by d.created_at, s.ordinal", quiz["id"])
        outline = [r["heading"] for r in rows]
    instructions = build_persona(
        name=host["name"], game_title=branding["game_title"], company=branding["company_name"],
        sarcasm=1 if straight else int(host["sarcasm"]), room=room,
        quiz_title=quiz["title"] if quiz else None, outline=outline, custom=host["custom_instructions"] or None,
        style=None if straight else (host["style"] or None), manner=host["manner"])
    return instructions, host


def voice_model(host: dict) -> str:
    return config.model_voice_premium if host.get("voice_quality") == "premium" else config.model_voice


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0][:20] or "friend"


_ROUND_ANGLE = {
    "Warm-Up": "the easy ones come first",
    "Picture Round": "charts and pictures from the report",
    "The Deep End": "the questions get harder now",
    "Lightning Round": "rapid-fire statements; call each one Fact or Fake",
    "Second Chance": "the questions they missed come back, and can only help their score",
    "Final Wager": "they bet their points on one last question",
    "Last Question": "one last question, worth double",
    "Speed Run": "the clock is already running",
    "The Quiz": "no clock, no tricks",
}


async def _welcome(run: dict, player: dict | None, name: str) -> str:
    """The opening line. It also starts the first round, so the game needs only one line before question one."""
    returning = bool(player and player["games_played_count"])
    if run["mode"] in ("speed", "sudden_death"):
        quiz = await db.fetchrow("select * from quizzes where id = $1", run["quiz_id"])
        rules = await settings_store.quiz_rules(quiz)
        if run["mode"] == "speed":
            # A race should start like one: a line of clauses ("ninety seconds, maximum chaos, minimum excuses")
            # was measured at six to nine seconds, most of it the pauses between them.
            return (f"Open the show. {name} is about to start a Speed Run: {int(rules['speed']['clock_sec'])} seconds on "
                    "the clock, as many answers as they can manage. ONE quick remark of eight words or fewer - a single "
                    "phrase, not a list - and then \"Go!\". The clock starts as you finish.")
        return (f"Open the show. {name} is playing Sudden Death: {int(rules['sudden_death']['strikes'])} wrong answers "
                "and the game is over. " + ("They have played before. " if returning else "")
                + "ONE sentence, under 14 words. The first question appears as you finish.")
    first = await db.fetchval("select round_name from run_items where run_id = $1 order by position limit 1", run["id"])
    first = first or (run["plan"][0]["name"] if run["plan"] else "the first round")
    # The first question appears the moment this line ends, so it is one breath long and ends on the round's name.
    if returning:
        return (f"Open the show. The contestant is {name}, who has played before. Welcome them back in six words or "
                f"fewer, then announce the first round by its name - \"{first}!\" - and stop. Under 12 words in total.")
    return (f"Open the show. The contestant is {name}, and this is their first game. Welcome them in ten words or "
            f"fewer, then announce the first round by its name - \"{first}!\" - and stop. Under 16 words in total. Do "
            "not explain the rules.")


async def _lightning_tally(run: dict, item: dict) -> str:
    """If the round just finished was the Lightning Round, how it went (for the next round's announcement)."""
    if not item.get("id") or item["round_no"] <= 1:
        return ""
    row = await db.fetchrow(
        "select count(*) as asked, count(*) filter (where is_correct) as right_ones from run_items"
        " where run_id = $1 and round_no = $2 and slot = 'fact_fake' and answered_at is not null",
        run["id"], item["round_no"] - 1)
    if not row or row["asked"] < 3:
        return ""
    return (f"First, in six words or fewer, react to their Lightning Round score: {row['right_ones']} out of "
            f"{row['asked']}. Then: ")


async def describe_cue(cue: str, *, run: dict | None, player: dict | None, item_id: str | None) -> str | None:
    """Turn a cue into a stage direction for the model, using facts from the database only."""
    name = _first_name(player["name"]) if player else "everyone"
    if cue == "welcome" and run:
        return await _welcome(run, player, name)
    if cue == "summary" and run:
        accuracy = round(100 * run["report_correct"] / run["report_answered"]) if run["report_answered"] else 0
        return (f"The game is over. {name} scored {run['score']} points and got {accuracy}% of the report questions right "
                f"(best streak {run['streak_max']}), earning the title '{run['badge']}'. Sign off, speaking to them directly "
                "(\"you\"), in two short sentences, under 22 words: announce the title they earned, then one last joke.")
    if not run:
        return None
    if cue == "round" and not item_id:
        if not run["plan"]:
            return None
        item = {"round_name": run["plan"][0]["name"], "slot": run["plan"][0]["slot"], "round_no": 0}
    else:
        if not item_id:
            return None
        item = await db.fetchrow("select * from run_items where id = $1 and run_id = $2", item_id, run["id"])
        if item is None:
            return None
    payload = item.get("payload") or {}

    if cue == "round":
        round_name = item["round_name"]
        if item["slot"] == "music":
            if run["mode"] == "sudden_death":
                return (f"Announce a bonus music break. Its name first, as a headline - \"Music Break!\" - then, in ten "
                        f"words or fewer: name the tune - a hit from the {run['decade']} - and win back a life. Do not "
                        "say the contestant's name, the song or the artist.")
            round_name, angle = "Music Break", f"name that tune, hits from the {run['decade']}"
        else:
            angle = _ROUND_ANGLE.get(round_name, "a new round")
        tally = await _lightning_tally(run, item)
        return (f"{tally}Announce the next round. Its name first, as a headline - \"{round_name}!\" - then ONE quick "
                f"joke of eight words or fewer about it. (What the round is, for your information only - do not recite "
                f"it: {angle}.) Do not say the contestant's name. The game is waiting for you: no more than 12 words"
                + (" after the score remark." if tally else "."))
    if payload.get("sealed"):
        return None                                   # not on screen yet: there is nothing to say about it
    if cue == "hint" and item["answered_at"] is None and payload.get("hint"):
        return (f"{name} used a lifeline and asked for a hint. Deliver exactly this hint in character, adding no "
                f"information of your own: \"{payload['hint']}\"")
    if cue == "result" and item["answered_at"] is not None:
        correct_answer = payload["options"][item["correct_index"]]
        speed = ""
        if item["time_limit_sec"] and item["time_taken_ms"] is not None:
            used = item["time_taken_ms"] / (item["time_limit_sec"] * 1000)
            speed = " They answered almost instantly." if used < 0.15 else (" They used nearly all the time." if used > 0.85 else "")
        helped = f" They used a lifeline ({', '.join(item['lifelines_used'])})." if item["lifelines_used"] else ""
        if item["slot"] == "music":
            track = await db.fetchrow("select artist, title from music_tracks where id = $1", item["music_track_id"])
            song = f"\"{track['title']}\" by {track['artist']}" if track else "the track"
            life = " That wins them a life back." if run["mode"] == "sudden_death" and item["is_correct"] else ""
            if item["timed_out"]:
                return (f"Music break. The track was {song}. The contestant ran out of time without answering. React: "
                        "verdict, one quick joke, then name the track.")
            if item["is_correct"]:
                return (f"Music break. The track was {song}, and the contestant got it RIGHT.{speed}{life} React: a verdict "
                        "call for a correct answer, then one quick joke about the song or the era.")
            return (f"Music break. The track was {song}; the contestant guessed \"{_chosen(item)}\", which is WRONG.{speed} "
                    "React: verdict, one quick joke, then name the track.")
        first = " This is their first answer of the game." if run["questions_answered"] <= 1 else ""
        # A long answer read out in full stalls the game: ask for its gist instead.
        short = " When you give the right answer, say only its gist, in six words or fewer." if len(correct_answer.split()) > 7 else ""
        about = f"Question: \"{payload['text']}\" Correct answer: \"{correct_answer}\".{first}"
        if item["timed_out"]:
            return f"{about} {name} ran out of time. React: verdict, one quick joke, then the right answer.{short}"
        if item["is_correct"]:
            streak = f" That is {run['streak']} in a row." if run["streak"] >= 3 else ""
            wager = f" They had wagered {item['wager']} points and won them." if item["wager"] else ""
            return (f"{about} {name} got it RIGHT.{speed}{helped}{streak}{wager} React: verdict, then one quick joke that "
                    "plays on the answer itself.")
        wager = f" They had wagered {item['wager']} points and lost them." if item["wager"] else ""
        return (f"{about} {name} answered \"{_chosen(item)}\", which is WRONG.{speed}{helped}{wager} React: verdict, one "
                f"quick joke about that specific wrong answer, then the right answer.{short}")
    return None


def _chosen(item: dict) -> str:
    index = item["selected_index"]
    options = item["payload"]["options"]
    return options[index] if index is not None and 0 <= index < len(options) else "nothing"


class TightEdit:
    """Takes the dead air out of a line as it streams, without touching the voice.

    The screens wait while the host talks, so silence is the one part of a line the game cannot afford.
    Measured on the host as shipped, a typical line of 4.7 seconds was 3.1 s of talking, 1.1 s of pauses
    and 0.5 s of silence after the last word - and no wording of the direction changed that. So the audio
    itself is edited, the way a radio producer tightens a recording. Only silence is ever removed:
      * a pause inside the line is kept, but no longer than the cap - still a beat before a punchline;
      * the silence after the last word is dropped, so the game moves on when he actually stops.
    The talking is passed through untouched, sample for sample.
    """

    FRAME = int(SAMPLE_RATE * 0.02) * 2          # 20 ms of 16-bit mono audio, in bytes
    RELEASE = int(SAMPLE_RATE * 0.06) * 2        # kept after the last word, so it dies away rather than snapping off

    def __init__(self, pause_cap_sec: float | None):
        self.cap = None if pause_cap_sec is None else int(SAMPLE_RATE * pause_cap_sec) * 2
        self.loose = b""                         # audio not yet a whole frame
        self.held = bytearray()                  # silence, kept back until it is clear whether more talking follows
        self.loudest = 0.0
        self.spoken = False

    def feed(self, chunk: bytes) -> bytes:
        """Take the next piece of audio; return what can be sent on now."""
        data = self.loose + chunk
        usable = len(data) - len(data) % self.FRAME
        self.loose = data[usable:]
        out = bytearray()
        for start in range(0, usable, self.FRAME):
            frame = data[start:start + self.FRAME]
            samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
            level = float(np.sqrt(np.mean(samples * samples))) / 32768
            self.loudest = max(self.loudest, level)
            if level > max(0.006, 0.03 * self.loudest):
                out += self._release()
                out += frame
                self.spoken = True
            else:
                self.held += frame
        return bytes(out)

    def _release(self) -> bytes:
        """The silence that was held back, now that talking has resumed - shortened if it was a long one.

        Both ends are kept and the middle removed, so the word before still dies away naturally and the
        next one still starts cleanly.
        """
        held = bytes(self.held)
        self.held.clear()
        if not self.spoken:
            return held[-self.FRAME:]            # before his first word: a breath of lead-in is plenty
        if self.cap is None or len(held) <= self.cap:
            return held
        half = self.cap // 2 - (self.cap // 2) % 2
        return held[:half] + held[len(held) - half:]

    def finish(self) -> bytes:
        """The line is over: what is left to send (a short release - not the silence after it)."""
        held = bytes(self.held) + self.loose
        self.held.clear()
        self.loose = b""
        return held[:self.RELEASE - self.RELEASE % 2] if self.spoken else b""


class HostVoice:
    """One realtime session with the voice model. Use as an async context manager."""

    def __init__(self, instructions: str, voice: str, speak: bool, *, model: str | None = None, pace: str = "snappy"):
        self.instructions = instructions
        self.voice = voice
        self.speak = speak
        self.model = model or config.model_voice
        self.speed = PACE_SPEED.get(pace, PACE_SPEED["snappy"])
        self.pause_cap = PAUSE_CAP_SEC.get(pace, PAUSE_CAP_SEC["snappy"])
        self._manager = None
        self._connection = None

    def _session(self, with_speed: bool) -> dict:
        session: dict = {"type": "realtime", "instructions": self.instructions,
                         "output_modalities": ["audio"] if self.speak else ["text"]}
        if self.speak:
            output: dict = {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "voice": self.voice}
            if with_speed and self.speed != 1.0:
                output["speed"] = self.speed
            # The contestant's microphone (hold-to-talk) arrives as 24 kHz PCM. Turn detection is off:
            # the browser says when they have finished, so there is no always-listening microphone.
            session["audio"] = {"input": {"format": {"type": "audio/pcm", "rate": SAMPLE_RATE}, "turn_detection": None},
                                "output": output}
        return session

    async def __aenter__(self) -> "HostVoice":
        self._manager = llm.client().realtime.connect(model=self.model)
        self._connection = await self._manager.__aenter__()
        try:
            await self._connection.session.update(session=self._session(with_speed=True))
        except Exception as exc:  # noqa: BLE001 – the speaking-rate setting is optional; never lose the host over it
            log.info("voice session rejected the pace setting (%s); continuing at normal pace", exc)
            await self._connection.session.update(session=self._session(with_speed=False))
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._manager is not None:
            await self._manager.__aexit__(*exc_info)

    async def note(self, text: str) -> None:
        """Tell the host something without asking for a line."""
        await self._connection.conversation.item.create(item={
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})

    async def hear(self, pcm: bytes) -> None:
        """Feed a chunk of the contestant's microphone audio (24 kHz, 16-bit mono)."""
        await self._connection.input_audio_buffer.append(audio=base64.b64encode(pcm).decode())

    async def forget_heard(self) -> None:
        await self._connection.input_audio_buffer.clear()

    async def interrupt(self) -> None:
        """Stop the line being spoken (the contestant has started talking)."""
        try:
            await self._connection.response.cancel()
        except Exception:  # noqa: BLE001 – nothing was being said
            pass

    async def say(self, direction: str) -> AsyncIterator[tuple[str, bytes | str]]:
        """Yield ('text', delta) and ('audio', pcm16_bytes) pieces for one line, then return."""
        await self.note(direction)
        async for piece in self._respond():
            yield piece

    async def reply(self, stage_note: str) -> AsyncIterator[tuple[str, bytes | str]]:
        """Answer what the contestant just said into the microphone."""
        await self.note(stage_note)
        await self._connection.input_audio_buffer.commit()
        async for piece in self._respond():
            yield piece

    async def _respond(self) -> AsyncIterator[tuple[str, bytes | str]]:
        connection = self._connection
        edit = TightEdit(self.pause_cap)
        await connection.response.create()
        async for event in connection:
            kind = event.type
            if kind == "response.output_audio.delta":
                audio = edit.feed(base64.b64decode(event.delta))
                if audio:
                    yield "audio", audio
            elif kind in ("response.output_audio_transcript.delta", "response.output_text.delta"):
                yield "text", event.delta
            elif kind == "response.done":
                release = edit.finish()
                if release:
                    yield "audio", release
                return
            elif kind == "error":
                message = str(getattr(event.error, "message", "voice model error"))
                lowered = message.lower()
                if "speed" in lowered or "cancel" in lowered or "no active response" in lowered:
                    continue                      # harmless: an optional setting, or nothing to interrupt
                if "buffer too small" in lowered or "buffer is empty" in lowered:
                    return                        # they let go of the button before saying anything
                raise RuntimeError(message)


# --------------------------------------------------------------------------- hints

_STOP_WORDS = {"the", "and", "for", "with", "that", "this", "from", "was", "were", "are", "its", "their", "than",
               "into", "over", "per", "has", "had", "have", "not", "but", "because", "more", "most", "some", "any",
               "million", "billion", "thousand", "percent", "dollars", "about", "both"}


def _stems(text: str) -> set[str]:
    """Numbers kept whole; words cut to a 4-letter stem so 'price' matches 'pricing'."""
    stems: set[str] = set()
    for raw in re.findall(r"[a-z]+|\d+(?:[.,]\d+)*", text.lower()):
        if raw[0].isdigit():
            stems.add(raw.replace(",", "").rstrip("."))
        elif len(raw) > 2 and raw not in _STOP_WORDS:
            stems.add(raw[:4])
    return stems


def _gives_away(hint: str, answer: str) -> bool:
    """True when a hint restates the answer: it repeats one of its numbers or a third of its meaningful words."""
    answer_stems, hint_stems = _stems(answer), _stems(hint)
    if any(stem[0].isdigit() and stem in hint_stems for stem in answer_stems):
        return True
    words = {stem for stem in answer_stems if not stem[0].isdigit()}
    return bool(words) and len(words & hint_stems) / len(words) >= 0.3


async def hint_text(question: dict) -> str:
    """A hint that narrows the field without handing over the answer."""
    section = None
    if question.get("section_id"):
        section = await db.fetchval("select heading from doc_sections where id = $1", question["section_id"])
    fallback = (f"It is covered under \"{section}\". Think about what the report said there."
                if section else "Think back to what the report said about this.")
    answer = question["options"][question["correct_index"]]
    try:
        hint = await llm.text(
            model=config.model_fast,
            system=("You give hints in a quiz about a report. Write ONE sentence that helps without giving the answer away. "
                    "Good hints: say what KIND of thing the answer is, rule out a tempting misreading, or recall the "
                    "surrounding context. NEVER state the answer, paraphrase it, use its key words or numbers, or name "
                    "any option."),
            user=f"Question: {question['question_text']}\nAnswer (do not reveal): {answer}\n"
                 f"Supporting text: {question['source_quote']}",
            max_output_tokens=300)
    except Exception as exc:  # noqa: BLE001 – a hint must never break the game
        log.warning("hint failed: %s", exc)
        return fallback
    if not hint or _gives_away(hint, answer):
        return fallback
    return hint


# --------------------------------------------------------------------------- samples for the admin

_SAMPLE_SCENE = ("Question: \"What was quarterly revenue?\" Correct answer: \"184 million dollars\". A contestant called Sam "
                 "answered \"148 million dollars\", which is WRONG. React: verdict, one quick joke about that specific "
                 "wrong answer, then the right answer.")


def _sample_persona(sarcasm: int, name: str, style: str | None = None, manner: str | None = None) -> str:
    return build_persona(name=name, game_title="the quiz", company="the company", sarcasm=sarcasm, style=style or None,
                         manner=manner)


async def sample_line(sarcasm: int, name: str) -> str:
    """A written sample so an admin can judge a sarcasm level before saving it."""
    return await llm.text(model=config.model_fast, system=_sample_persona(sarcasm, name), user=_SAMPLE_SCENE,
                          max_output_tokens=300)


async def sample_audio(sarcasm: int, name: str, voice: str, *, quality: str = "standard", pace: str = "snappy",
                       style: str | None = None, manner: str | None = None) -> tuple[str, bytes]:
    """The same sample, spoken: returns (the words, a WAV file) so an admin can hear a voice."""
    words, pcm = "", bytearray()
    model = voice_model({"voice_quality": quality})
    async with HostVoice(_sample_persona(sarcasm, name, style, manner), voice, speak=True, model=model,
                         pace=pace) as session:
        async for kind, data in session.say(_SAMPLE_SCENE):
            if kind == "audio":
                pcm.extend(data)  # type: ignore[arg-type]
            else:
                words += str(data)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(pcm))
    return words.strip(), out.getvalue()


async def talk_note(run: dict, player: dict | None) -> str:
    """Stage note that goes with something the contestant said: the game state, minus any answer."""
    name = _first_name(player["name"]) if player else "the contestant"
    item = await db.fetchrow("select * from run_items where run_id = $1 order by position desc limit 1", run["id"])
    facts = [f"score {run['score']}", f"{run['questions_answered']} answered ({run['correct_answers']} right)"]
    if run["streak"] >= 2:
        facts.append(f"{run['streak']} right in a row")
    if run["mode"] in ("show", "straight") and run["plan"]:
        facts.append(f"{len(run['plan'])} still to come")
    if run["mode"] == "sudden_death":
        facts.append(f"{run['strikes']} strikes so far")
    lifelines = ", ".join(f"{kind} x{left}" for kind, left in (run["lifelines"] or {}).items() if left)
    if lifelines:
        facts.append(f"lifelines left: {lifelines}")
    parts = [f"(Stage note, not spoken aloud: {name} just spoke to you; their words are in the audio that follows. "
             f"Game so far: {'; '.join(facts)}."]
    on_screen = (item is not None and item["answered_at"] is None and item["payload"].get("text")
                 and not (item["slot"] == "final" and item["wager"] is None) and not item["payload"].get("sealed"))
    if on_screen:
        options = "; ".join(f"{chr(65 + i)}) {text}" for i, text in enumerate(item["payload"]["options"])
                            if i not in item["payload"].get("removed", []))
        parts.append(f"On their screen right now: \"{item['payload']['text']}\" Options: {options}. You may read the "
                     "question and these options out loud if asked, exactly as written. You have NOT been told which is "
                     "correct: do not guess, confirm, rule out or hint at any option.")
        if item["slot"] == "music":
            parts.append("It is a music clip: do not name the song or the artist.")
    elif item is not None and item["answered_at"] is not None:
        parts.append("They have just answered a question and are looking at the result.")
    else:
        parts.append("No question is on screen at the moment.")
    parts.append("Reply to what they said, in character. Keep it under 20 words unless you are reading the question or "
                 "its options out, which may take as long as they take. This is conversation, not an answer verdict: do "
                 "not open with \"Correct\", \"Nope\", \"Time\" or any other verdict call.)")
    return " ".join(parts)
