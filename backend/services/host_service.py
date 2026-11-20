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

import asyncio
import base64
import contextlib
import hashlib
import io
import logging
import random
import re
import time
import wave
from typing import AsyncIterator

import numpy as np

import db
import llm
from config import config
from services import eleven, settings_store
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


# --------------------------------------------------------------------------- lines that are written, then performed
# With an ElevenLabs voice (see services/eleven.py) the model that writes a line does not speak it: it directs
# the performance inside the line, with a short tag in square brackets at the start of each beat. Measured on
# identical scripts, a note for every beat was what made a line sound acted rather than read, and the
# organiser judged it so by ear ("P5 tone and diction is right").

# Each manner's usual tags. The writer may use others; these set the colour.
TAG_PALETTE = {
    "wiseguy": "[smug], [teasing], [amused], [mock-impressed], [grinning], [dry]",
    "deadpan": "[deadpan], [dry], [flat], [unimpressed], [sardonic] - and let the words be the funny part",
    "showman": "[booming], [excited], [theatrical], [dramatic], [delighted] - then drop to [aside] for the joke",
}


def _performance_notes(name: str, manner: str, warm: bool, style: str | None) -> str:
    """The "how you sound" section for a host whose lines are performed by someone else."""
    return (
        "# How your lines are performed - this matters as much as the words\n"
        "A voice actor performs every line you write, and you direct them inside the line itself: a short delivery tag "
        "in square brackets at the start of each beat, saying HOW that beat is played. The actor never says a tag.\n"
        "- Two to four beats in a line, one tag each. Tags are one to three words: [amused], [deadpan], [teasing], "
        "[mock-impressed], [building], [excited], [sarcastic], [wincing], [delighted], [suspicious], [whispering].\n"
        f"- {name}'s usual colours: {TAG_PALETTE.get(manner, TAG_PALETTE[DEFAULT_MANNER])}.\n"
        "- CHANGE GEAR between beats: the attitude must move - up for mock excitement, down for the dry aside.\n"
        "- A beat of silence is \"...\" in the text. Put one before a punchline.\n"
        "- A sound you make can be a tag on its own - [laughs], [chuckles], [sighs], [scoffs] - at most one a line.\n"
        "- Never a sound effect such as [applause], [drumroll] or [buzzer]: the game plays its own.\n"
        "- The name of a round is a TITLE. Give it a run-up and its own beat, announced: \"[building] It's the... "
        "[announcing, big] Warm-Up Round!\", \"[building] Hold on to something... [announcing] the Lightning Round!\" - "
        "a different run-up every time, never the same one twice in a row."
        + ("\n- At this sarcasm setting the teasing is gone: you are on their side, delighted when they get one right "
           "and sympathetic when they do not. Keep all of the energy and the timing." if warm else "")
        + (f"\n- {name} in particular: {style}" if style else ""))


_TAGGED_SHAPES = (
    "# The shape to aim for (never these exact words)\n"
    "\"[deadpan] Nope - [plainly] 184 million. [amused] ... Bold guess, though. I respect bold.\"\n"
    "\"[delighted] Correct! [dry] Somebody actually opened the attachment.\"\n"
    "\"[impressed] Yes! And fast. [suspicious] ... Suspiciously fast.\"\n"
    "\"[wincing] Ohh, that's time - [plainly] Phoenix. [dry] The clock didn't read it either.\"\n"
    "\"[smooth] Yes! [delighted] Pure eighties gold... [announcing] Blondie, 'Heart of Glass.'\"\n"
    "\"[building] Hold on... [announcing, big] the Lightning Round! [teasing] Fact or fake, no thinking... "
    "[dry] your specialty.\"")

_TAG = re.compile(r"\[[^\[\]]{0,60}\]")


def spoken_text(line: str) -> str:
    """A written line as it is heard: the delivery tags taken out (that is what the host box shows)."""
    text = _TAG.sub(" ", line)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,!?]|\.(?!\.))", r"\1", text)      # "Sam !" -> "Sam!", but a "..." keeps its space
    return text.strip(" \"'")


def build_persona(*, name: str, game_title: str, company: str, sarcasm: int, room: bool = False,
                  quiz_title: str | None = None, outline: list[str] | None = None, custom: str | None = None,
                  style: str | None = None, manner: str | None = None, tags: bool = False) -> str:
    """The host's character, delivery and rules, as instructions for the voice model.

    With ``tags`` the instructions are for a model that writes lines for someone else to perform: it
    directs the delivery with tags in square brackets instead of performing it itself.
    """
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
        _performance_notes(name, manner or DEFAULT_MANNER, warm, style) if tags else (
            "# How you sound - this matters more than the words\n"
            + acting["sound"]
            + ("\n- At this sarcasm setting the teasing is gone: you are on their side, delighted when they get one right "
               "and sympathetic when they do not. Keep all of the energy and the timing." if warm else "")
            + (f"\n- {name} in particular: {style}" if style else "")),
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
        "The music comes up the moment you stop, so your LAST word is the button: the funniest word of the joke, a "
        "title, a song. Never end on filler (\"good luck\", \"let's go\"), on a question, or on an answer they can "
        "already read on their screen.\n"
        "- The verdict first, in your own way, and it must match what happened - right: \"Correct.\", \"Yes.\", \"Well, "
        "look at you.\"; wrong: \"Nope.\", \"Ohhh, no.\", \"Yeah... no.\"; out of time: \"And that's time.\"\n"
        "- Wrong or out of time: the right answer goes INSIDE the verdict, said plainly (\"Nope - Phoenix.\"); then ONE "
        "short joke, ending on its funniest word.\n"
        "- Right: the verdict, then ONE short joke, ending on its funniest word.\n"
        "- A song: end on its title and artist, the way a DJ would.\n"
        "That is the whole line. No second joke. No pep talk. No summary.\n"
        "A verdict is ONLY for reacting to an answer. A welcome, a round announcement, a hint or a sign-off never "
        "starts with one.",
        "# Length\n"
        "The game waits while you talk, so be brief - brief in WORDS, never by talking faster. Reactions: 10 words when "
        "they are right, 14 when they are wrong or out of time, 16 for a song. Round announcements: 12 words. Sign-offs: "
        "24 words. The show's opening is the one place you take your time. Each stage direction gives the exact limit; "
        "never go over it.\n"
        "Say numbers the short way: \"184 million\", not \"one hundred eighty-four point two million dollars\".",
        "# Variety\n"
        "Never open two lines in a row the same way. Never reuse a joke or comparison. Use a contestant's name in at "
        "most one line in four, and never as the first word.",
        "# Hard rules\n"
        + ("- Write only the words you say out loud, plus the delivery tags in square brackets. No quotation marks, no "
           "stage directions in parentheses, no asterisks, no emoji. Word limits count spoken words, not tags.\n" if tags
           else "- Spoken words only. Never say a stage direction or a sound effect out loud as words.\n") +
        "- Jokes target the answer, the situation, corporate life or yourself. Never a person's intelligence, job, "
        "looks or identity. No profanity.\n"
        "- Workplace-safe, as if their manager and HR were in the room: no romance, dating, flirting or innuendo - "
        "nothing like \"get a room\", and the contestant never has a \"relationship\" with the report or with "
        "anything else - and nothing about bodies, politics or religion. Never assume anyone's gender from a name: no "
        "\"he\", \"she\", \"man\", \"lady\" or \"guy\" for the contestant - use their name or \"you\".\n"
        "- Never reveal or hint at an answer before it has been given, unless you are asked to deliver a hint.\n"
        "- Report facts come only from what you are told. Never invent them.\n"
        "- Do not name real game shows or their hosts, and never say you are an AI.",
        _TAGGED_SHAPES if tags else (
            "# The shape to aim for (never these exact words)\n"
            "\"Nope - 184 million. ... Bold guess, though. I respect bold.\"\n"
            "\"Correct. Somebody actually opened the attachment.\"\n"
            "\"Yes! And fast. ... Suspiciously fast.\"\n"
            "\"Ohh, that's time - Phoenix. The clock didn't read it either.\"\n"
            "\"Hold on... Lightning Round! Fact or fake, no thinking... your specialty.\"\n"
            "\"Next up... Picture Round! The diagram somebody lost a weekend to.\""),
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
    host = {**settings, "name": character["name"], "style": character.get("style") or "",
            "manner": character.get("manner") or DEFAULT_MANNER, **resolve_voice(character["voice"])}
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
        style=None if straight else (host["style"] or None), manner=host["manner"], tags=host["engine"] == "eleven")
    return instructions, host


def resolve_voice(voice: str) -> dict:
    """Who will perform a character's voice: ElevenLabs when it is an ElevenLabs voice and a key is set;
    otherwise the realtime model, with the OpenAI voice that stands in for it."""
    if eleven.is_eleven(voice):
        if eleven.configured():
            return {"engine": "eleven", "voice": voice}
        return {"engine": "realtime", "voice": eleven.BY_ID[voice].fallback}
    return {"engine": "realtime", "voice": voice}


def open_voice(instructions: str, host: dict, speak: bool) -> "AnyVoice":
    """The voice session for a host, as persona() described it."""
    pace = host.get("pace", "snappy")
    if host.get("engine") == "eleven":
        return WrittenHostVoice(instructions, eleven.BY_ID[host["voice"]], speak, pace=pace,
                                manner=host.get("manner") or DEFAULT_MANNER,
                                budget=int(host.get("voice_budget", settings_store.DEFAULTS["host"]["voice_budget"])))
    return HostVoice(instructions, host["voice"], speak, model=voice_model(host), pace=pace)


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
    # Office Jukebox, the music side game
    "Morning Stand-up": "a few seconds of a hit: name the artist. An easy start to the working day",
    "Jargon Jukebox": "a song title rewritten as corporate jargon: pick the real title, and the song plays",
    "Song or Synergy?": "a phrase: is it a hit song, or office jargon?",
    "Closing Bell": "name the song from its clip, for double points: the last two before home time",
}


# --------------------------------------------------------------------------- the opening
# The show's big moment, and the one place the host takes his time: he opens the show by name, introduces
# himself and the contestant, explains how the game works, and leads into the first round. (The organiser,
# 2026-11-20: "the host needs to explain the game show before the game starts ... better introduction and
# leading into the game. Needs to change each game too.") A returning player gets a shorter one, and anyone can
# skip it. Every opening gets a different angle, and the host is shown the player's last openings.

_LIFELINE_WORDS = {"fifty": "50/50 (two wrong answers removed)", "peek": "a peek at the report's passage, for half points",
                   "hint": "a hint from you, the host"}


def _how_it_works(run: dict, rules: dict, rounds: list[str]) -> str:
    """The rules of this game, as facts for the host to put in his own words."""
    mode = run["mode"]
    if mode == "show":
        cfg = rules["show"]
        facts = [f"{len(rounds)} rounds tonight, in this order: {', '.join(rounds)}.",
                 f"About {int(cfg['timer_sec'])} seconds a question. The faster a right answer, the more points; a streak "
                 "of right answers adds a bonus; a wrong answer costs a few points."]
        if "Music Break" in rounds:
            facts.append("Music Break: a song plays, name that tune.")
        if "Lightning Round" in rounds:
            facts.append("Lightning Round: quick statements, call each one Fact or Fake.")
        if "Second Chance" in rounds:
            facts.append("Second Chance: questions they missed come back, and can only help.")
        if "Final Wager" in rounds:
            facts.append("Final Wager: they bet their points on one last question.")
        lifelines = [_LIFELINE_WORDS[kind] for kind, left in (run["lifelines"] or {}).items() if left and kind in _LIFELINE_WORDS]
        if lifelines:
            facts.append("Lifelines, once each: " + "; ".join(lifelines) + ".")
        return " ".join(facts)
    if mode == "sudden_death":
        cfg = rules["sudden_death"]
        return (f"{int(cfg['strikes'])} wrong answers and the game is over. The questions keep coming and the clock gets "
                f"shorter, from {int(cfg['timer_start_sec'])} seconds down to {int(cfg['timer_min_sec'])}. Every so often "
                "a song plays: name that tune and win a strike back. One 50/50 lifeline.")
    if mode == "jukebox":
        return ("Office Jukebox, the music side game: four quick rounds of hits from the decade they chose - Morning "
                "Stand-up (name the artist), Jargon Jukebox (a song title in corporate-speak: pick the real one), Song or "
                "Synergy? (hit song or office jargon?) and the Closing Bell (name the song, double points). Nothing "
                "costs points: it is the warm-up.")
    if mode == "speed":
        cfg = rules["speed"]
        return (f"{int(cfg['clock_sec'])} seconds on one clock for the whole game, as many answers as they can manage. "
                f"A wrong answer costs {int(cfg['wrong_penalty_sec'])} seconds.")
    cfg = rules["straight"]
    return (f"No clock, no jokes, no tricks: questions from every section of the report, and {int(cfg['pass_mark'])}% "
            "to pass.")


def _when(moment) -> str | None:
    """A meeting time, the way a person would say it."""
    if moment is None:
        return None
    import datetime as _dt
    today = _dt.datetime.now(moment.tzinfo).date() if moment.tzinfo else _dt.date.today()
    days = (moment.date() - today).days
    if days < 0:
        return None
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return f"on {moment.strftime('%A')}"
    return f"on {moment.strftime('%B')} {moment.day}"


def _opening_angles(run: dict, player: dict | None, quiz: dict, returning: bool) -> list[str]:
    """What an opening can be about. (A returning player's rematch is a sporting one: framed as the contestant and
    the report "meeting again", it drew a "someone should book a room" joke - wrong for a work quiz.)"""
    angles = [
        "a fake sponsor from office life (the jammed printer, the 4 pm meeting, reply-all...): one quick line",
        "mock-epic stakes: treat this like a championship final, for a quiz about a business report",
        "the show's production values: no budget, no prizes, all the glory",
        f"the report itself, \"{quiz['title']}\", and how many people swear they read every page",
    ]
    meeting = _when(quiz["meeting_at"])
    if meeting:
        angles.append(f"the meeting this report is for, {meeting}: this is their chance to walk in ready")
    if run["decade"]:
        angles.append(f"tonight's soundtrack: hits from the {run['decade']}")
    if player and player["team"]:
        angles.append(f"the contestant's team, {player['team']}, and its reputation riding on this")
    if returning:
        angles += ["a rematch, sports-style: the contestant is back for another round against this report, like a title "
                   "fight or a replay",
                   "the contestant's record so far (see the facts), and whether tonight beats it"]
    return angles


async def _opening_angle(run: dict, player: dict | None, quiz: dict, returning: bool) -> str:
    """One thing to make this opening about, different each game."""
    import random as _random
    return _random.choice(_opening_angles(run, player, quiz, returning))


async def _welcome(run: dict, player: dict | None, name: str) -> str:
    """The opening: the show, the host, the contestant, how it works, then the first round. One line, so the game
    needs nothing else before question one; long enough to set the show up, never so long that it drags."""
    returning = bool(player and player["games_played_count"])
    quiz = await db.fetchrow("select * from quizzes where id = $1", run["quiz_id"])
    rules = await settings_store.quiz_rules(quiz)
    branding = await settings_store.get("branding")
    dealt = [r["round_name"] for r in await db.fetch("select round_name from run_items where run_id = $1 order by position",
                                                     run["id"])]
    rounds: list[str] = []
    for round_name in dealt + [slot.get("name") for slot in (run["plan"] or [])]:
        if round_name and round_name not in rounds:
            rounds.append(round_name)
    first = rounds[0] if rounds else "the first round"
    company = branding["company_name"]
    company = "" if company.strip().lower() in ("", settings_store.DEFAULTS["branding"]["company_name"].lower()) else company
    facts = [f"The show: \"{branding['game_title']}\", where {company + ' ' if company else ''}colleagues prove they read the "
             f"report \"{quiz['title']}\". The style of play tonight: {MODES.get(run['mode'], {}).get('name', run['mode'])}.",
             f"The contestant: {name}" + (f", from {player['team']}" if player and player["team"] else "") + "."]
    if returning:
        best = await db.fetchval("select max(score) from runs where player_id = $1 and quiz_id = $2 and mode = $3"
                                 " and status = 'completed'", run["player_id"], run["quiz_id"], run["mode"])
        facts.append(f"They have played {player['games_played_count']} game"
                     f"{'' if player['games_played_count'] == 1 else 's'} before"
                     + (f"; their best score at this style of play is {best}." if best else "."))
    facts.append("How it works: " + _how_it_works(run, rules, rounds))
    recent = [r["host_opening"] for r in await db.fetch(
        "select host_opening from runs where player_id = $1 and host_opening is not null and id <> $2"
        " order by started_at desc limit 3", run["player_id"], run["id"])] if player else []
    angle = await _opening_angle(run, player, quiz, returning)

    # Spoken words: a first game gets the full opening (about 20 to 25 seconds), a returning player a short one.
    # Measured: asked for "50 to 70 words", the writer gave 94 to 115 - so the limit is stated as a hard one, and
    # WrittenHostVoice asks for one rewrite of anything well over it.
    limit = {("show", False): 65, ("show", True): 35, ("sudden_death", False): 55, ("sudden_death", True): 30,
             ("speed", False): 40, ("speed", True): 20, ("straight", False): 35, ("straight", True): 20,
             ("jukebox", False): 45, ("jukebox", True): 25}[
        (run["mode"] if run["mode"] in ("show", "sudden_death", "speed", "straight", "jukebox") else "show", returning)]
    if run["mode"] == "speed":
        ending = "the rules in one breath" if not returning else "a one-line reminder of the rules"
        finish = "End on \"Go!\" - the clock starts the moment you finish."
    elif run["mode"] == "straight":
        ending = "how it works" if not returning else "a one-line reminder"
        finish = "End by inviting them to begin; the first question appears as you finish. Calm and encouraging."
    else:
        ending = ("how it works, picking only the two or three things that matter most" if not returning
                  else "a quick \"you know the drill\" reminder of one or two rules")
        # A Sudden Death game's card says "Sudden Death", not its first level: the opening ends on the game's name.
        title = "Sudden Death" if run["mode"] == "sudden_death" else first
        finish = (f"Lead into the first round with a run-up and end on its name as a title - \"{title}!\" - and stop. "
                  "The first question appears as you finish.")
    parts = [
        "Open the show. This is the big opening, the one moment you take your time - but it must never drag: "
        f"{limit} spoken words AT MOST, in four to six short beats. Count them.",
        "You start over the theme music as it fades down, so your first two or three words are half heard: begin with "
        "a [building] run-up in your own words (never a name - not the show's, not yours, not theirs - and not a word "
        "about the music), then open the "
        "show by name, like the top of a broadcast. Then, in this order: "
        + ("introduce yourself in a line; " if not returning else "welcome them back - you have met before; ")
        + f"introduce tonight's contestant like a contestant on a show; then {ending}, in plain words, as a host "
        "talks - never as a list; then tonight's angle as one joke, so the run-up into the game starts from a laugh. "
        + finish + " Say the word \"welcome\" once at most in the whole opening: a contestant must never feel welcomed "
        "twice.",
        f"Tonight's angle - the one joke the opening is about, in your own way: {angle}.",
        "The facts, for you (use what helps, never recite them):\n" + "\n".join(f"- {fact}" for fact in facts),
    ]
    if recent:
        parts.append("Your last openings for this contestant. This one must be completely different - not their jokes, "
                     "not their words, not their shape:\n" + "\n".join(f"- \"{line[:300]}\"" for line in recent))
    parts.append(f"Word limit: {limit}.")
    return "\n\n".join(parts)


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
        if run["mode"] == "jukebox":
            share = round(100 * run["music_correct"] / run["music_answered"]) if run["music_answered"] else 0
            return (f"Office Jukebox, the music side game, is over. {name} scored {run['score']} points and got {share}% "
                    f"of the music right, earning the title '{run['badge']}' (on their screen). Sign off warmly, speaking "
                    "to them directly (\"you\"): the title, announced like an office promotion; one last joke; then send "
                    "them off to the main game, ready for it. Word limit: 24.")
        accuracy = round(100 * run["report_correct"] / run["report_answered"]) if run["report_answered"] else 0
        best = await db.fetchval("select max(score) from runs where player_id = $1 and quiz_id = $2 and mode = $3"
                                 " and status = 'completed' and id <> $4", run["player_id"], run["quiz_id"], run["mode"],
                                 run["id"])
        # The results screen says "New high score!" exactly when this is true (see stats.summary and Summary.tsx).
        record = run["score"] > 0 and run["status"] == "completed" and (best is None or run["score"] > best)
        screen = (" Their screen also says \"New high score!\"" + (" (it is their first game)." if best is None else ".")
                  if record else "")
        return (f"The game is over. {name} scored {run['score']} points and got {accuracy}% of the report questions right "
                f"(best streak {run['streak_max']}), earning the title '{run['badge']}'. The title is on their screen."
                f"{screen} Sign off, speaking to them directly (\"you\"), in three short beats: the title, announced like an "
                "award; one last joke; then the show's name as your very last words, like the end of a broadcast - the "
                "music comes up under it. Word limit: 24.")
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
        # The song plays the moment he stops, so a music break ends on the throw that starts it.
        throw = ("End on a two- or three-word throw that starts the song (\"Roll it!\", \"Hit it!\"): it plays the "
                 "moment you stop.")
        if item["slot"] == "music" and run["mode"] == "jukebox":
            # The side game's clip rounds keep their own names; a clip still starts as he stops.
            angle = _ROUND_ANGLE.get(round_name, "name the song")
            ending = f"then, if you like, ONE joke of five words or fewer about it. {throw}"
        elif item["slot"] == "music":
            if run["mode"] == "sudden_death":
                return (f"Announce a bonus music break. Its name first, as a headline - \"Music Break!\" - then, in ten "
                        f"words or fewer: name the tune - a hit from the {run['decade']} - and win back a life. {throw} "
                        "Do not say the contestant's name, the song or the artist. Word limit: 14.")
            round_name, angle = "Music Break", f"name that tune, hits from the {run['decade']}"
            ending = f"then, if you like, ONE joke of five words or fewer. {throw}"
        elif round_name == "Final Wager":
            angle = _ROUND_ANGLE.get(round_name, "a new round")
            ending = ("then ONE joke of six words or fewer, and end by putting the bet to them in three words or fewer "
                      "(\"Place your bet!\"): the betting screen is next.")
        else:
            angle = _ROUND_ANGLE.get(round_name, "a new round")
            ending = "then ONE joke of six words or fewer about it, ending on its funniest word."
        tally = await _lightning_tally(run, item)
        limit = 12 + (6 if tally else 0) + (2 if round_name == "Final Wager" else 0)
        return (f"{tally}Announce the next round: a run-up of two or three words, then its name as a headline - "
                f"\"{round_name}!\" - "
                f"{ending} (What the round is, for your information only - do not recite it: {angle}.) Do not say the "
                f"contestant's name. The game is waiting for you. Word limit: {limit}.")
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
        warm = (" This is the warm-up side game: be on their side - celebrate a right answer big, and make a miss a "
                "gentle laugh, never a roast.") if run["mode"] == "jukebox" else ""
        if item["slot"] == "jargon":
            track = await db.fetchrow("select artist, title from music_tracks where id = $1", item["music_track_id"])
            song = f"\"{track['title']}\" by {track['artist']}" if track else "the song"
            jargon = payload.get("text", "")
            verdict = ("ran out of time" if item["timed_out"] else "got it RIGHT" if item["is_correct"]
                       else f"guessed \"{_chosen(item)}\", which is WRONG")
            return (f"Jargon Jukebox: the corporate title was \"{jargon}\"; the song is {song}. {name} {verdict}.{warm} "
                    "React: the verdict, one quick joke about the corporate title, and end on the song's title and artist, "
                    "like a DJ - the song plays the moment you stop. Word limit: 16.")
        if item["slot"] == "music":
            track = await db.fetchrow("select artist, title from music_tracks where id = $1", item["music_track_id"])
            song = f"\"{track['title']}\" by {track['artist']}" if track else "the track"
            life = " That wins them a life back." if run["mode"] == "sudden_death" and item["is_correct"] else ""
            if item["timed_out"]:
                return (f"Music break. The track was {song}. The contestant ran out of time without answering.{warm} React: "
                        "the verdict, one quick joke, and end on the track's title and artist, like a DJ. Word limit: 16.")
            if item["is_correct"]:
                return (f"Music break. The track was {song}, and the contestant got it RIGHT.{speed}{life}{warm} React: a "
                        "verdict call for a correct answer, one quick joke about the song or the era, and end on the song's "
                        "title and artist, like a DJ. Word limit: 16.")
            return (f"Music break. The track was {song}; the contestant guessed \"{_chosen(item)}\", which is WRONG.{speed}"
                    f"{warm} React: the verdict, one quick joke about that guess, and end on the track's title and artist, "
                    "like a DJ. Word limit: 16.")
        first = " This is their first answer of the game." if run["questions_answered"] <= 1 else ""
        # A long answer read out in full stalls the game, and it is on their screen anyway: its gist will do.
        short = " The right answer is long: say only its gist, in four words or fewer." if len(correct_answer.split()) > 4 else ""
        about = f"Question: \"{payload['text']}\" Correct answer: \"{correct_answer}\".{first}"
        if item["timed_out"]:
            return (f"{about} {name} ran out of time. React: the verdict with the right answer inside it, then one quick "
                    f"joke, ending on its funniest word.{short} Word limit: 14.")
        if item["is_correct"]:
            streak = f" That is {run['streak']} in a row." if run["streak"] >= 3 else ""
            wager = f" They had wagered {item['wager']} points and won them." if item["wager"] else ""
            return (f"{about} {name} got it RIGHT.{speed}{helped}{streak}{wager} React: the verdict, then one quick joke "
                    "that plays on the answer itself, ending on its funniest word. Word limit: 10.")
        wager = f" They had wagered {item['wager']} points and lost them." if item["wager"] else ""
        return (f"{about} {name} answered \"{_chosen(item)}\", which is WRONG.{speed}{helped}{wager} React: the verdict "
                f"with the right answer inside it, then one quick joke about that specific wrong answer, ending on its "
                f"funniest word.{short} Word limit: 14.")
    return None


# --------------------------------------------------------------------------- said once, replayed after
# ElevenLabs is paid by the character, so a line whose exact words come round again is recorded the first time and
# replayed from then on: the final question read out, and the round introductions, which come from a rotating set per
# host and round (written and recorded the first ROUND_SET times a round comes up). Reactions, the opening and the
# sign-off stay live: they are about this player and this answer.
ROUND_SET = 6
REPLAY_CHUNK = 9600              # 0.2 s of 24 kHz 16-bit audio per message
KEEP_UP_TO_SEC = 30


def _audio_key(voice: "eleven.Voice", line: str, pause_cap: float | None) -> str:
    return hashlib.sha256(f"{voice.voice_id}|{eleven.model()}|{pause_cap}|{line}".encode()).hexdigest()


async def _recorded(key: str) -> bytes | None:
    row = await db.fetchrow("update host_line_audio set used_count = used_count + 1, last_used_at = now() where key = $1"
                            " returning audio", key)
    return bytes(row["audio"]) if row else None


async def _record(key: str, voice: "eleven.Voice", line: str, audio: bytes) -> None:
    await db.execute("insert into host_line_audio (key, voice, line, audio, seconds) values ($1, $2, $3, $4, $5)"
                     " on conflict (key) do nothing", key, voice.id, line, audio, len(audio) / 2 / SAMPLE_RATE)


async def _bank_lines(bank: str) -> list[dict]:
    return [dict(r) for r in await db.fetch("select id, line, used_count from host_line_bank where bank = $1", bank)]


async def _bank_add(bank: str, line: str) -> None:
    await db.execute("insert into host_line_bank (bank, line, used_count) values ($1, $2, 1)", bank, line)


async def _bank_used(line_id: int) -> None:
    await db.execute("update host_line_bank set used_count = used_count + 1 where id = $1", line_id)


async def round_set(run: dict, item_id: str | None) -> str | None:
    """Which rotating set a round introduction comes from - or None when it must be said live (after the Lightning
    Round, when he remarks on its score)."""
    if not item_id:
        return None
    item = await db.fetchrow("select * from run_items where id = $1 and run_id = $2", item_id, run["id"])
    if item is None or await _lightning_tally(run, dict(item)):
        return None
    decade = run["decade"] if item["slot"] == "music" else ""     # a music break's line may mention the decade
    return f"{run['mode']}|{item['slot']}|{item['round_name']}|{decade}"


async def final_question_line(run: dict, item_id: str | None) -> str | None:
    """The final question as the host reads it: word for word, never through the writer (nothing paraphrased, nothing
    given away), after a lead-in that says nothing about it, and with no tag inside it to single a word out."""
    if not item_id:
        return None
    item = await db.fetchrow("select slot, wager, answered_at, payload from run_items where id = $1 and run_id = $2",
                             item_id, run["id"])
    if not item or item["slot"] != "final" or item["wager"] is None or item["answered_at"] is not None:
        return None
    text = (item["payload"] or {}).get("text")
    return f"[dramatic] Your final question... [clearly] {text}" if text else None


def _chosen(item: dict) -> str:
    index = item["selected_index"]
    options = item["payload"]["options"]
    return options[index] if index is not None and 0 <= index < len(options) else "nothing"


REWRITE_IF_WITHIN_SEC = 4.0    # a draft slower than this is used as it is, even if long (see WrittenHostVoice._write)
_clock = time.monotonic        # (the tests set the clock)


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

    async def recite(self, line: str, **_: object) -> AsyncIterator[tuple[str, bytes | str]]:
        """Say a fixed line (the final question, a round introduction), word for word."""
        async for piece in self.say(f"Read this out exactly as written - every word, nothing added or changed: "
                                    f"\"{spoken_text(line)}\""):
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


# --------------------------------------------------------------------------- the host as writer and performer

# The longest pause left inside a written line. A little more room than the realtime host gets: these lines
# carry their own "..." beats, and a beat before a punchline is half the joke.
WRITTEN_PAUSE_CAP_SEC = {"relaxed": None, "snappy": 0.5, "rapid": 0.35}
WORDS_IN_AUDIO = "their words are in the audio that follows"
# The backup voice speaks through the text-to-speech model, which may not have every realtime voice.
_SPEECH_VOICE = {"cedar": "ash", "marin": "sage"}


def wav_file(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm[:len(pcm) - len(pcm) % 2])
    return out.getvalue()


async def transcribe(pcm: bytes) -> str:
    """What the contestant said into the microphone, as text."""
    reply = await llm.client().audio.transcriptions.create(model=config.model_transcribe,
                                                           file=("talk.wav", wav_file(pcm)), language="en")
    return (getattr(reply, "text", "") or "").strip()


_FOREIGN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff00-\uffef]")   # CJK and full-width: never in a line
_LEAKS = re.compile(r"</?final>|\bword count\b|\b\d+ words\. count\b|\bcount: ", re.I)  # the writer's notes to itself


def tidy_line(line: str) -> str:
    """A safety net for what the writer returns: one line, said once, with nothing after it.

    The writer has been seen to return its line twice, back to back, and to add its own word count or stray
    characters after it. Whatever produced that, the host must never say it.
    """
    line = re.sub(r"\s+", " ", line).strip().strip("\"'")
    for pattern in (_FOREIGN, _LEAKS):
        found = pattern.search(line)
        if found:
            line = line[:found.start()].rstrip(" ,;:-")
    # The same line twice in a row: keep one.
    for middle in [i for i, ch in enumerate(line) if ch == " "]:
        first, second = line[:middle].strip(), line[middle:].strip()
        if first and first == second:
            return first
    return line


async def write_line(instructions: str, history: list[dict]) -> str:
    """A line for a host whose lines are performed by someone else: the words, with delivery tags."""
    request = {"model": config.model_host_writer, "instructions": instructions, "input": history,
               "max_output_tokens": 800}
    try:
        response = await llm.client().responses.create(**request, reasoning={"effort": config.host_writer_effort})
    except Exception as exc:  # noqa: BLE001
        if "reasoning" not in str(exc).lower():
            raise
        response = await llm.client().responses.create(**request)     # a writer that does not reason
    return tidy_line(llm.final_text(response))


class WrittenHostVoice:
    """The host as a writer and a performer: a text model writes each line, with a delivery tag for every
    beat, and ElevenLabs performs it. The same interface as HostVoice, so the host channel does not care
    which one it has.

    When ElevenLabs fails (no answer, refused, out of credit) the line is spoken by the OpenAI voice that
    stands in for this one, and after a refusal or two failures in a row, so is the rest of the session:
    the host loses some polish, never his voice.
    """

    KEEP = 40                     # messages remembered besides the brief: enough not to repeat a joke

    def __init__(self, instructions: str, voice: eleven.Voice, speak: bool, *, pace: str = "snappy",
                 manner: str = DEFAULT_MANNER, budget: int = 0):
        self.instructions = instructions
        self.voice = voice
        self.speak = speak
        self.manner = manner
        self.pause_cap = WRITTEN_PAUSE_CAP_SEC.get(pace, WRITTEN_PAUSE_CAP_SEC["snappy"])
        self.history: list[dict] = []
        self.heard = bytearray()
        self.stopped = False      # interrupt() was called for the line being said (cleared when the line ends)
        self.failures = 0         # ElevenLabs failures in a row
        self.backup_only = False  # ElevenLabs is not used again in this session
        self.budget = budget      # % of the month's plan after which new lines use the backup voice (0: no guard)
        self.last_by = ""         # who performed the last line: "eleven", "backup" or "replay"
        self.timings: list[dict] = []

    async def __aenter__(self) -> "WrittenHostVoice":
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    def _remember(self, role: str, text: str) -> None:
        self.history.append({"role": role, "content": text})
        del self.history[:-self.KEEP]

    async def note(self, text: str) -> None:
        self._remember("user", text)

    async def hear(self, pcm: bytes) -> None:
        self.heard.extend(pcm)

    async def forget_heard(self) -> None:
        self.heard.clear()

    async def interrupt(self) -> None:
        self.stopped = True

    async def say(self, direction: str) -> AsyncIterator[tuple[str, bytes | str]]:
        await self.note(direction)
        async for piece in self._respond():
            yield piece

    async def recite(self, line: str, *, note: str = "Read the final question out, word for word.",
                     keep: bool = True) -> AsyncIterator[tuple[str, bytes | str]]:
        """Perform a line already written (the final question, a round introduction from its set): no writer, so it
        starts sooner. With `keep` it is recorded the first time and replayed after."""
        await self.note(note)
        self._remember("assistant", line)
        try:
            yield "text", spoken_text(line)
            if not self.speak:
                return
            async for piece in self._perform(line, keep=keep):
                yield piece
        finally:
            self.stopped = False

    async def from_set(self, round_key: str, direction: str) -> str | None:
        """A round introduction from this host's rotating set for the round: a new one is written (without the game
        so far, so it fits any game) until the set is full; then the least-used is picked."""
        bank = hashlib.sha256(f"{self.instructions}|{self.voice.voice_id}|{round_key}".encode()).hexdigest()
        try:
            lines = await _bank_lines(bank)
            if len(lines) >= ROUND_SET:
                pick = min(lines, key=lambda line: (line["used_count"], random.random()))
                await _bank_used(pick["id"])
                return pick["line"]
            line = await self._write([{"role": "user", "content": direction}])
            if line:
                await _bank_add(bank, line)
            return line
        except Exception as exc:  # noqa: BLE001 - the set is a saving; without it he says the line live
            log.warning("round introduction set unavailable: %s", exc)
            return None

    async def reply(self, stage_note: str) -> AsyncIterator[tuple[str, bytes | str]]:
        """Answer what the contestant just said into the microphone: it is written down first."""
        pcm, self.heard = bytes(self.heard), bytearray()
        try:
            words = await transcribe(pcm)
        except Exception as exc:  # noqa: BLE001 – a host who did not catch that is better than no game
            log.warning("could not make out what the contestant said: %s", exc)
            words = ""
        if not words:
            self.stopped = False
            return
        await self.note(stage_note.replace(WORDS_IN_AUDIO, "what they said is below")
                        + f"\nWhat they said: \"{words[:600]}\"")
        async for piece in self._respond():
            yield piece

    async def _write(self, history: list[dict] | None = None) -> str:
        """The next line, with its delivery tags. A line well over the word limit its direction states is sent back
        once for a shorter version: the game is standing still while the host talks."""
        history = self.history if history is None else history
        began = _clock()
        line = await write_line(self.instructions, history)
        took = _clock() - began
        asked = history[-1]["content"] if history and history[-1]["role"] == "user" else ""
        ""
        found = re.search(r"Word limit: (\d+)\.", asked)
        if found and line:
            limit, words = int(found.group(1)), len(spoken_text(line).split())
            # Only a line far over, and only if the first draft came back quickly: a rewrite doubles the wait before
            # he speaks, and a slow one could make him miss his moment altogether (the opening gives up after 12 s).
            if words > limit + 15 and took < REWRITE_IF_WITHIN_SEC:
                shorter = await write_line(self.instructions, history + [
                    {"role": "assistant", "content": line},
                    {"role": "user", "content": f"That was {words} words; the limit is {limit}. Say it again in {limit} "
                                                "words or fewer - same order, same ending, every word earning its place."}])
                if shorter and len(spoken_text(shorter).split()) < words:
                    line = shorter
        return line

    async def _audio(self, line: str) -> AsyncIterator[bytes]:
        """The line performed: by ElevenLabs, or by the backup voice when ElevenLabs cannot (or the month's plan is
        past the organiser's budget)."""
        if not self.backup_only and self.budget:
            share = await eleven.share_used()
            if share is not None and share * 100 >= self.budget:
                self.backup_only = True
                log.warning("ElevenLabs plan %d%% used (budget %d%%): the backup voice takes over", share * 100, self.budget)
        if not self.backup_only:
            sent = False
            try:
                self.last_by = "eleven"
                async with contextlib.aclosing(eleven.perform(line, self.voice)) as audio:
                    async for chunk in audio:
                        sent = True
                        yield chunk
                self.failures = 0
                return
            except eleven.ElevenError as exc:
                self.failures += 1
                # Busy (other games using the plan's slots) is no reason to give up on ElevenLabs for the whole game.
                if exc.permanent or (self.failures >= 2 and not exc.busy):
                    self.backup_only = True
                log.warning("ElevenLabs failed (%s); the backup voice %s", exc,
                            "takes over for this session" if self.backup_only else "says this line")
                if sent:
                    return            # half a line was heard: do not start it again in another voice
        self.last_by = "backup"
        async with contextlib.aclosing(self._backup(spoken_text(line))) as audio:
            async for chunk in audio:
                yield chunk

    async def _backup(self, words: str) -> AsyncIterator[bytes]:
        """The OpenAI voice that stands in for this one, performing the line from its words."""
        speech_voice = _SPEECH_VOICE.get(self.voice.fallback, self.voice.fallback)
        async with llm.client().audio.speech.with_streaming_response.create(
                model=config.model_speech, voice=speech_voice, input=words,
                instructions=MANNERS.get(self.manner, MANNERS[DEFAULT_MANNER])["sound"],
                response_format="pcm") as response:
            async for chunk in response.iter_bytes():
                yield chunk

    async def _respond(self) -> AsyncIterator[tuple[str, bytes | str]]:
        began = time.monotonic()
        try:
            # A line cut off while it is still being written is abandoned at once, so the next one (the reaction to
            # an answer, a round's introduction) is not kept waiting for words nobody will hear.
            writing = asyncio.ensure_future(self._write())
            while not writing.done():
                if self.stopped:
                    writing.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await writing
                    return
                await asyncio.wait({writing}, timeout=0.05)
            line = writing.result()
            wrote = time.monotonic()
            if not line or self.stopped:
                return
            self._remember("assistant", line)
            yield "text", spoken_text(line)
            if not self.speak:
                return
            first: list[float] = []
            async for piece in self._perform(line, first):
                yield piece
            self.timings.append({"write_sec": round(wrote - began, 2),
                                 "first_sound_sec": round((first[0] if first else time.monotonic()) - began, 2),
                                 "by": "backup" if self.backup_only or self.failures else "eleven", "line": line})
        finally:
            self.stopped = False

    async def _perform(self, line: str, first: list[float] | None = None, keep: bool = False
                       ) -> AsyncIterator[tuple[str, bytes | str]]:
        """The line's audio, with the dead air taken out; stops as soon as the host is cut off. With `keep`, a line
        already recorded in this voice is replayed rather than paid for again, and a new one is recorded as it goes."""
        key = _audio_key(self.voice, line, self.pause_cap) if keep else None
        began, sound_at = time.monotonic(), None
        if key:
            try:
                saved = await _recorded(key)
            except Exception as exc:  # noqa: BLE001 - the recording is a saving, never a reason to fail
                log.warning("could not read a recorded line: %s", exc)
                saved = None
            if saved:
                self.last_by = "replay"
                for start in range(0, len(saved), REPLAY_CHUNK):
                    if self.stopped:
                        return
                    if first is not None and not first:
                        first.append(time.monotonic())
                    yield "audio", saved[start:start + REPLAY_CHUNK]
                log.info("host line replayed (%d characters saved)", len(line))
                return
        edit = TightEdit(self.pause_cap)
        kept = bytearray()
        async with contextlib.aclosing(self._audio(line)) as audio:
            async for chunk in audio:
                if self.stopped:
                    return                # cut off: nothing is recorded
                out = edit.feed(chunk)
                if out:
                    if first is not None and not first:
                        first.append(time.monotonic())
                    sound_at = sound_at or time.monotonic()
                    if key:
                        kept.extend(out)
                    yield "audio", out
        tail = edit.finish()
        if tail and not self.stopped:
            if key:
                kept.extend(tail)
            yield "audio", tail
        # One line a log entry: who performed it, how long it took to start, and what it cost.
        log.info("host line by %s: %d characters, first sound after %.1fs", self.last_by, len(line),
                 (sound_at or time.monotonic()) - began)
        # Only a whole line, in the real voice, is kept (never the backup voice standing in, never a line cut short).
        if key and kept and not self.stopped and self.last_by == "eleven" and len(kept) <= KEEP_UP_TO_SEC * SAMPLE_RATE * 2:
            try:
                await _record(key, self.voice, line, bytes(kept))
            except Exception as exc:  # noqa: BLE001
                log.warning("could not record a line: %s", exc)


AnyVoice = HostVoice | WrittenHostVoice


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


def _sample_persona(sarcasm: int, name: str, style: str | None = None, manner: str | None = None,
                    tags: bool = False) -> str:
    return build_persona(name=name, game_title="the quiz", company="the company", sarcasm=sarcasm, style=style or None,
                         manner=manner, tags=tags)


async def sample_line(sarcasm: int, name: str) -> str:
    """A written sample so an admin can judge a sarcasm level before saving it."""
    return await llm.text(model=config.model_fast, system=_sample_persona(sarcasm, name), user=_SAMPLE_SCENE,
                          max_output_tokens=300)


async def sample_audio(sarcasm: int, name: str, voice: str, *, quality: str = "standard", pace: str = "snappy",
                       style: str | None = None, manner: str | None = None) -> tuple[str, bytes]:
    """The same sample, spoken: returns (the words, a WAV file) so an admin can hear a voice."""
    words, pcm = "", bytearray()
    host = {"voice_quality": quality, "pace": pace, "manner": manner or DEFAULT_MANNER, **resolve_voice(voice)}
    persona = _sample_persona(sarcasm, name, style, manner, tags=host["engine"] == "eleven")
    async with open_voice(persona, host, True) as session:
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
    parts = [f"(Stage note, not spoken aloud: {name} just spoke to you; {WORDS_IN_AUDIO}. "
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
