"""Company-level settings (branding, host, game defaults) and per-quiz overrides.

Everything an admin can personalise lives here as plain JSON with safe defaults, so
a brand-new install is playable before anyone opens the settings screens.
"""
from __future__ import annotations

import copy
import re
import time
from typing import Any

import db
from services import eleven

ALL_DECADES = ["50s", "60s", "70s", "80s", "90s", "00s", "10s", "20s"]

# The four styles of play. Each is a rule preset over the same engine.
MODES: dict[str, dict[str, str]] = {
    "show": {
        "name": "Game Show",
        "tagline": "The full pub quiz: themed rounds, picture round, music round, lightning round and a final wager.",
    },
    "sudden_death": {
        "name": "Sudden Death",
        "tagline": "{strikes} strikes and you're out. It keeps going and keeps getting faster. Music wins a strike back.",
    },
    "speed": {
        "name": "Speed Run",
        "tagline": "{clock} seconds on the clock. As many as you can. Wrong answers cost time.",
    },
    "straight": {
        "name": "Straight Quiz",
        "tagline": "No music, no jokes, no clock. Every section covered. Hit the pass mark.",
    },
    "jukebox": {
        "name": "Office Jukebox",
        "tagline": "The warm-up: five minutes of hits with an office twist. Name the artist, crack corporate-speak song "
                   "titles, song or jargon? Nothing costs points.",
    },
}

_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six"}


def mode_view(mode: str, rules: dict) -> dict:
    """A style of play as shown to players and admins: its name, and a tagline quoting this quiz's own numbers."""
    strikes = int(rules["sudden_death"]["strikes"])
    tagline = MODES[mode]["tagline"].format(strikes=_NUMBER_WORDS.get(strikes, str(strikes)),
                                            clock=int(rules["speed"]["clock_sec"]))
    if strikes == 1:
        tagline = tagline.replace("One strikes", "One strike")
    return {"id": mode, "name": MODES[mode]["name"], "tagline": tagline}

DEFAULTS: dict[str, dict[str, Any]] = {
    "branding": {
        "company_name": "Your Company",
        "game_title": "Read It or Weep",
        "tagline": "Prove you read it before the meeting does.",
        "logo_key": None,
        "primary_color": "#6D5EF8",
        "accent_color": "#FFB020",
        "theme": "dark",                  # dark | light
        "support_email": None,
    },
    "host": {
        "sarcasm": 6,                     # 0 (warm) .. 10 (full roast)
        "voice_enabled": True,
        "custom_instructions": "",
        # One to four host characters. Players may pick between them in the lobby. "manner" is how the
        # character performs (see HOST_MANNERS); "style" is an optional extra line about that character.
        # The voices are the game-show-host voices of the ElevenLabs voice library, chosen by ear by the
        # organiser (2026-11-20). Without an ElevenLabs key each one is played by the OpenAI voice closest to it.
        "hosts": [
            {"id": "max", "name": "Max", "voice": "eleven:classic", "manner": "wiseguy",
             "style": "Polished prime-time game-show anchor: treats every trivia question like championship boxing, quick "
                      "with a raised-eyebrow aside and old-school broadcast swagger."},
            {"id": "roxy", "name": "Roxy", "voice": "eleven:british", "manner": "deadpan",
             "style": "Razor-sharp and bone-dry, mildly exasperated: the funniest person in the room, finally "
                      "handed a microphone. Understated British wit where a flat pause does the heavy lifting."},
            {"id": "june", "name": "June", "voice": "eleven:southern", "manner": "wiseguy",
             "style": "Unmistakably Southern through and through — write EVERY line in warm, sassy Southern American "
                      "speech, rhythm, and idioms: 'y'all', 'well now', 'bless your heart', 'sugar', 'darlin'', "
                      "'hold your horses', 'fixin' to', 'Lord have mercy', 'reckon', 'hot dog', 'sweet tea', and "
                      "homespun porch-swing zingers. Big-hearted, honeyed, and sharp as a tack — she teases like "
                      "your funniest Southern aunt and cheers every right answer like a Friday-night touchdown."},
            {"id": "dex", "name": "Dex", "voice": "eleven:hype", "manner": "showman",
             "style": "Young, quick and hyped: every right answer is a buzzer-beater highlight reel, every wrong one "
                      "an instant-replay blooper."},
        ],
        "player_can_choose": True,
        # standard is cheaper; premium is the default because, measured on identical scenes, only the premium
        # model follows direction about pace and timing, and it writes the sharper jokes (about 3x the cost).
        "voice_quality": "premium",
        "pace": "snappy",                 # relaxed (slower) | snappy (the voice's own speed) | rapid (faster)
        "talk_enabled": True,
        "voice_budget": 90,               # % of the month's ElevenLabs plan; then the backup voice (0 = no guard)             # players may hold a button and talk to the host
        # The first character, repeated for convenience (kept in step with hosts[0]).
        "name": "Max",
        "voice": "cedar",
    },
    "game": {
        "modes": ["show"],
        "default_mode": "show",
        "auto_approve": True,             # AI questions go live without review
        "question_sources": "all",        # all | manual | ai
        "music_enabled": True,
        "after_party": True,              # hits from the game's decade play on the results screen
        "soundtrack": True,               # ...and quietly behind the questions, from the first one to the last
        "soundtrack_volume": "medium",    # quiet | medium | loud
        # The music moment by moment, in steps of about 3 dB from the mix tuned by ear (0): the opening theme, under
        # the host, under a question (-3 = silent), and between questions (title cards and the wait for Next too).
        # Plus how long the theme plays on its own before the host, and whether it eases off while a player lingers.
        "mix": {"opening": 0, "opening_sec": 6, "under_host": 0, "questions": 0, "between": 0, "ease_down": True},
        "decades": ["60s", "70s", "80s", "90s", "00s", "10s", "20s"],
        "picture_mode": "show",           # show | auto | memory
        "ready_threshold": 80,            # % of sections cleared to count as "ready"
        "leaderboard": True,
        "report_url": None,               # optional external report URL (SharePoint/Drive/Confluence)
        "allow_download": False,          # opt-in: let signed-in players open/download the uploaded report
        "unlisted": False,                # hide from the front door (direct link only)
        "confidential_voice": False,      # keep report answers off third-party ElevenLabs voice synthesis
        "purge_source_after_process": False,  # delete raw uploaded file once questions are generated
        "show": {
            "questions": 9, "music_rounds": 2, "lightning_items": 6, "second_chance": 2,
            "final_wager": True, "timer_sec": 30, "picture_timer_sec": 40,
            "lifelines": {"fifty": 1, "peek": 1, "hint": 1},
        },
        "sudden_death": {"strikes": 3, "timer_start_sec": 30, "timer_min_sec": 12, "music_every": 5, "max_questions": 60},
        "speed": {"clock_sec": 90, "wrong_penalty_sec": 3},
        "straight": {"per_section": 2, "max_questions": 20, "pass_mark": 80},
        "live": {"questions": 10, "timer_sec": 20, "music_rounds": 1, "head_start": 200},
    },
}

# Voices the realtime model accepts, with two things measured on an identical sentence:
#   pitch   - median fundamental frequency (how low or high the voice sits)
#   clarity - harmonics-to-noise ratio in dB (higher = cleaner tone; low values sound raspy)
# These are facts about the audio. Whether a voice "sounds like your host" is for the admin to
# judge by ear with the sample button.
VOICE_FACTS = {
    "echo": (129, 3.0), "ash": (130, 2.8), "cedar": (154, 4.7), "verse": (167, 4.9), "shimmer": (175, 6.3),
    "alloy": (183, 6.3), "marin": (187, 7.0), "ballad": (191, 5.9), "coral": (260, 9.2), "sage": (273, 8.0),
}
# The voices an admin can choose: OpenAI's (performed by the realtime model) and ElevenLabs' host voices.
VOICES = sorted(VOICE_FACTS) + [voice.id for voice in eleven.VOICES]
MAX_HOSTS = 4
# How a host character performs. The direction for each lives in host_service.MANNERS.
HOST_MANNERS = ("wiseguy", "deadpan", "showman")


def voice_register(voice: str) -> str:
    pitch = VOICE_FACTS.get(voice, (180, 6))[0]
    return "deeper" if pitch < 145 else ("lower-mid" if pitch < 160 else ("middle" if pitch < 220 else "higher"))


def voice_tone(voice: str) -> str:
    clarity = VOICE_FACTS.get(voice, (180, 6))[1]
    return "clear" if clarity >= 6.5 else ("smooth" if clarity >= 4.5 else "gravelly")


VOICE_INFO: dict[str, dict] = {
    **{voice: {"provider": "openai", "pitchHz": pitch, "register": voice_register(voice), "clarityDb": clarity,
               "tone": voice_tone(voice)} for voice, (pitch, clarity) in VOICE_FACTS.items()},
    **{voice.id: {"provider": "elevenlabs", "label": voice.label, "blurb": voice.blurb, "short": voice.short,
                  "fallback": voice.fallback, "register": voice.register, "addLink": voice.share_link} for voice in eleven.VOICES},
}

_cache: dict[str, tuple[float, dict]] = {}
_TTL = 15.0


_NULLABLE_KEYS = frozenset({"report_url", "support_email", "logo_key"})


def deep_merge(base: dict, override: dict | None) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        elif value is not None or key not in result or key in _NULLABLE_KEYS:
            result[key] = value
    return result


async def get(key: str) -> dict[str, Any]:
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _TTL:
        return copy.deepcopy(cached[1])
    stored = await db.fetchval("select value from settings where key = $1", key)
    merged = deep_merge(DEFAULTS.get(key, {}), stored or {})
    if key == "host":
        if stored and "hosts" not in stored:        # saved before host characters existed
            for simple in ("name", "voice"):
                if stored.get(simple):
                    merged["hosts"][0][simple] = stored[simple]
        merged["name"], merged["voice"] = merged["hosts"][0]["name"], merged["hosts"][0]["voice"]
    _cache[key] = (time.monotonic(), merged)
    return copy.deepcopy(merged)


async def put(key: str, value: dict[str, Any]) -> dict[str, Any]:
    merged = deep_merge(await get(key), value)
    if key == "host" and "hosts" not in value and merged.get("hosts"):
        # Someone edited the simple name/voice fields: apply them to the first character.
        for simple in ("name", "voice"):
            if simple in value:
                merged["hosts"][0][simple] = value[simple]
    cleaned = _CLEANERS.get(key, lambda v: v)(merged)
    await db.execute(
        "insert into settings (key, value) values ($1, $2)"
        " on conflict (key) do update set value = excluded.value, updated_at = now()", key, cleaned)
    _cache.pop(key, None)
    return await get(key)


def _color(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def _clean_branding(value: dict) -> dict:
    defaults = DEFAULTS["branding"]
    value["primary_color"] = _color(value.get("primary_color"), defaults["primary_color"])
    value["accent_color"] = _color(value.get("accent_color"), defaults["accent_color"])
    value["theme"] = value.get("theme") if value.get("theme") in ("dark", "light") else "dark"
    for field, limit in (("company_name", 80), ("game_title", 60), ("tagline", 140)):
        value[field] = (str(value.get(field) or defaults[field])).strip()[:limit]
    return value


VOICE_BUDGETS = (0, 50, 75, 90)


def _clean_host(value: dict) -> dict:
    try:
        value["sarcasm"] = max(0, min(10, int(value.get("sarcasm", 6))))
    except (TypeError, ValueError):
        value["sarcasm"] = 6
    hosts: list[dict] = []
    taken: set[str] = set()
    for raw in (value.get("hosts") or [])[:MAX_HOSTS]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:30]
        if not name:
            continue
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "host"
        host_id, n = base, 2
        while host_id in taken:
            host_id, n = f"{base}-{n}", n + 1
        taken.add(host_id)
        voice = raw.get("voice") if raw.get("voice") in VOICES else DEFAULTS["host"]["hosts"][0]["voice"]
        manner = raw.get("manner") if raw.get("manner") in HOST_MANNERS else HOST_MANNERS[0]
        hosts.append({"id": host_id, "name": name, "voice": voice, "manner": manner,
                      "style": str(raw.get("style") or "").strip()[:300]})
    value["hosts"] = hosts or copy.deepcopy(DEFAULTS["host"]["hosts"][:1])
    value["name"], value["voice"] = value["hosts"][0]["name"], value["hosts"][0]["voice"]
    value["player_can_choose"] = bool(value.get("player_can_choose", True))
    value["voice_quality"] = (value.get("voice_quality") if value.get("voice_quality") in ("standard", "premium")
                              else DEFAULTS["host"]["voice_quality"])
    value["pace"] = value.get("pace") if value.get("pace") in ("relaxed", "snappy", "rapid") else "snappy"
    value["voice_enabled"] = bool(value.get("voice_enabled", True))
    value["talk_enabled"] = bool(value.get("talk_enabled", True))
    value["custom_instructions"] = str(value.get("custom_instructions") or "")[:600]
    # The share of the month's ElevenLabs plan after which new lines use the backup voice (0: only when it runs out).
    try:
        budget = int(value.get("voice_budget", DEFAULTS["host"]["voice_budget"]))
    except (TypeError, ValueError):
        budget = DEFAULTS["host"]["voice_budget"]
    value["voice_budget"] = budget if budget in VOICE_BUDGETS else DEFAULTS["host"]["voice_budget"]
    return value


MIX_STEPS = {"opening": (-2, 2), "under_host": (-2, 1), "questions": (-3, 2), "between": (-2, 2)}
OPENING_SEC = (4, 10)


def _clean_mix(given: Any) -> dict:
    """The organiser's music levels, each kept to its range. (Under the host the music goes no more than one step up:
    beyond that he stops being easy to follow.)"""
    mix = dict(DEFAULTS["game"]["mix"])
    given = given if isinstance(given, dict) else {}
    for key, (low, high) in {**MIX_STEPS, "opening_sec": OPENING_SEC}.items():
        try:
            mix[key] = max(low, min(high, int(given.get(key, mix[key]))))
        except (TypeError, ValueError):
            pass
    mix["ease_down"] = bool(given.get("ease_down", mix["ease_down"]))
    return mix


def clean_game(value: dict) -> dict:
    modes = [m for m in value.get("modes") or [] if m in MODES] or ["show"]
    value["modes"] = modes
    value["default_mode"] = value.get("default_mode") if value.get("default_mode") in modes else modes[0]
    value["decades"] = [d for d in value.get("decades") or [] if d in ALL_DECADES] or DEFAULTS["game"]["decades"]
    if value.get("question_sources") not in ("all", "manual", "ai"):
        value["question_sources"] = "all"
    if value.get("picture_mode") not in ("auto", "show", "memory"):
        value["picture_mode"] = "auto"
    if value.get("soundtrack_volume") not in ("quiet", "medium", "loud"):
        value["soundtrack_volume"] = "medium"
    value["mix"] = _clean_mix(value.get("mix"))
    raw_url = str(value.get("report_url") or "").strip()[:500]
    value["report_url"] = raw_url if re.match(r"^https?://\S+$", raw_url, re.I) else None
    for flag in ("allow_download", "unlisted", "confidential_voice", "purge_source_after_process"):
        value[flag] = bool(value.get(flag, False))
    return value


_CLEANERS = {"branding": _clean_branding, "host": _clean_host, "game": clean_game}


async def quiz_rules(quiz: dict) -> dict[str, Any]:
    """Game rules for one quiz: company defaults with the quiz's own overrides on top."""
    return clean_game(deep_merge(await get("game"), quiz.get("settings") or {}))
