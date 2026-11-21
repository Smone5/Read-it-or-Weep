"""Solo game engine.

One engine, four styles of play (see ``settings_store.MODES``). A run has a queue of
*slots* (``runs.plan``); each time the player asks for the next item the front slot
is turned into a concrete ``run_items`` row – a question with its options shuffled –
and only the public part is sent to the browser. The correct answer stays in the
database until the player has answered, and all scoring happens here.

Timing. An item that opens a round (and every music break) is dealt *sealed*: the
browser learns only which round is coming, shows its title card and lets the host
introduce it, then asks to open the item. Opening reveals the question and starts
its clock, so no introduction ever eats into the player's answering time.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import db
from security import sign_media
from services import badges, favorites, bank, jukebox, music_service, stats, video
from services.settings_store import MODES

GRACE_MS = 1500           # network + render time not counted against the player
MUSIC_GRACE_MS = 3000     # a music clip also has to start playing
# The final question is read out by the host before its answers appear (the advice of a game-show producer and a voice
# director, 2026-11-20). Its clock waits for the reading - the same wait for everyone, sound on or off - and starts the
# moment the answers are shown (start_clock), never later than this estimate of how long the reading takes.
READ_WORDS_PER_SEC = 2.0  # the host's measured pace, pauses included
READ_LEAD_WORDS = 8       # bet-tier reaction + "Your final question..."
READ_PAD_SEC = 1.5        # his first sound, and a breath before the answers
READ_MAX_SEC = 20.0
LATE_MS = 4000            # an answer this long after the limit counts as a timeout
UNTIMED_SEC = 0

BADGES = badges.REPORT_BADGES
LIFELINE_MULTIPLIER = {"fifty": 0.75, "hint": 0.8, "peek": 0.5}
PEEK_GRACE_SEC = 10
UNUSED_LIFELINE_BONUS = 40
REPORT_SLOTS = ("report", "image", "final", "second_chance")
MUSIC_SLOTS = ("music", "jargon", "song_or_jargon")    # counted as music in a game's score card
# A song plays while these are on screen: a music question's clip, and the song behind an Office Jukebox jargon title
# (its clue - and the background music steps aside for it, or the player hears the wrong song; user, 2026-11-20).
# Not Song or Synergy?, where hearing it would give the answer away: its song plays once it has been answered.
CLIP_SLOTS = ("music", "jargon")


class GameError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_binary_choice(prior: list[bool]) -> bool:
    """Pick the next True/False (or Song/Jargon) target unpredictably while staying balanced.

    Previously, strictly forcing the rarer side whenever counts differed made every even-numbered
    question (Q2, Q4, Q6...) 100% predictable as the opposite of the previous one. Instead:
      * Never allow 4 identical answers in a row, and never let the cumulative split exceed ±3.
      * Otherwise draw randomly with a gentle pull back toward 50/50 (40/60 at ±1, 25/75 at ±2),
        so back-to-back TRUEs or FALSEs occur naturally and players cannot game the pattern.
    """
    if len(prior) >= 3 and prior[-1] == prior[-2] == prior[-3]:
        return not prior[-1]
    diff = sum(1 if v else -1 for v in prior)
    if diff >= 3:
        return False
    if diff <= -3:
        return True
    p_true = {2: 0.25, 1: 0.40, 0: 0.50, -1: 0.60, -2: 0.75}[diff]
    return random.random() < p_true


# --------------------------------------------------------------------------- plans

def _slot(kind: str, round_no: int, name: str, limit: int, **extra: Any) -> dict:
    return {"slot": kind, "round": round_no, "name": name, "limit": limit, **extra}


def build_plan(mode: str, rules: dict, stock: dict[str, int], section_ids: list[str], music: bool) -> list[dict]:
    if mode == "show":
        cfg = rules["show"]
        total = max(3, int(cfg["questions"]))
        pictures = min(2 if total >= 6 else 1, stock["picture"])
        texts = total - pictures - 1                       # one question is held back for the final
        first, second = math.ceil(texts / 2), texts // 2
        limit, picture_limit = int(cfg["timer_sec"]), int(cfg["picture_timer_sec"])
        breaks = int(cfg["music_rounds"]) if music else 0
        plan: list[dict] = []
        number = 1
        plan += [_slot("report", number, "Warm-Up", limit, difficulty="easy" if i == 0 else "medium") for i in range(first)]
        if breaks >= 1:
            number += 1
            plan.append(_slot("music", number, "Music Break", 25))
        if pictures:
            number += 1
            plan += [_slot("image", number, "Picture Round", picture_limit) for _ in range(pictures)]
        if second:
            number += 1
            plan += [_slot("report", number, "The Deep End", limit, difficulty="medium" if i == 0 else "hard")
                     for i in range(second)]
        lightning = min(int(cfg["lightning_items"]), stock["fact_fake"])
        if lightning >= 4:
            number += 1
            plan += [_slot("fact_fake", number, "Lightning Round", 8) for _ in range(lightning)]
        for extra_break in range(1, breaks):
            number += 1
            plan.append(_slot("music", number, "Music Break", 25))
        if int(cfg.get("second_chance", 0)):
            number += 1
            plan += [_slot("second_chance", number, "Second Chance", limit, mult=0.5)
                     for _ in range(int(cfg["second_chance"]))]
        number += 1
        if cfg.get("final_wager", True):
            plan.append(_slot("final", number, "Final Wager", limit + 10, difficulty="hard"))
        else:
            plan.append(_slot("report", number, "Last Question", limit, difficulty="hard", mult=2))
        return plan

    if mode == "jukebox":
        # Office Jukebox, the music side game (see services/jukebox.py): generous clocks, and nothing costs points.
        warm_up, jargon, synergy, closing = jukebox.ROUNDS
        return ([_slot("music", 1, warm_up, 30, asks="artist") for _ in range(3)]
                + [_slot("jargon", 2, jargon, 30) for _ in range(3)]
                + [_slot("song_or_jargon", 3, synergy, 15) for _ in range(4)]
                + [_slot("music", 4, closing, 30, asks="title", mult=2) for _ in range(2)])

    if mode == "straight":
        cfg = rules["straight"]
        per_section = max(1, int(cfg["per_section"]))
        targets = [sid for _ in range(per_section) for sid in section_ids][: int(cfg["max_questions"])]
        if not targets:
            targets = [None] * min(10, stock["mcq"])
        return [_slot("report", 1, "The Quiz", UNTIMED_SEC, section=sid, any_source=True) for sid in targets]

    if mode == "speed":
        pattern = ["fact_fake", "fact_fake", "report"] if stock["fact_fake"] >= 6 else ["report"]
        return [_slot(pattern[i % len(pattern)], 1, "Speed Run", UNTIMED_SEC) for i in range(80)]

    return _sudden_death_block(rules, 0, stock, music)     # sudden_death grows as it is played


def _sudden_death_block(rules: dict, level: int, stock: dict[str, int], music: bool) -> list[dict]:
    cfg = rules["sudden_death"]
    limit = max(int(cfg["timer_min_sec"]), int(cfg["timer_start_sec"]) - 3 * level)
    difficulty = "easy" if level == 0 else ("medium" if level <= 2 else "hard")
    size = max(3, int(cfg["music_every"]))
    block = [_slot("report", level + 1, f"Level {level + 1}", limit, difficulty=difficulty, level=level)
             for _ in range(size - 1)]
    if stock["picture"] and level % 2 == 0:
        block[1] = _slot("image", level + 1, f"Level {level + 1}", limit + 8, level=level)
    if music:
        block.append(_slot("music", level + 1, f"Level {level + 1}", 20, level=level))
    else:
        block.append(_slot("report", level + 1, f"Level {level + 1}", limit, difficulty=difficulty, level=level))
    return block


# --------------------------------------------------------------------------- starting a run

async def start_run(quiz: dict, rules: dict, player: dict, mode: str, decade: str | None) -> dict:
    if mode not in rules["modes"]:
        raise GameError("That style of play is not switched on for this quiz.")
    questions = await bank.approved(quiz["id"], rules["question_sources"])
    stock = bank.counts(questions)
    if stock["mcq"] < 3 and mode != "jukebox":                # the music side game needs no report questions
        raise GameError("This quiz does not have enough questions yet. Check back soon.", 409)
    if mode == "jukebox" and not rules["music_enabled"]:
        raise GameError("Music is switched off for this quiz, so Office Jukebox cannot be played.")

    music = bool(rules["music_enabled"]) and mode in ("show", "sudden_death", "jukebox")
    if music:
        allowed = rules["decades"]
        decade = decade if decade in allowed else random.choice(allowed)
        music_service.ensure_decade(decade)
    else:
        decade = None

    section_ids = [s["id"] for s in await bank.sections(quiz["id"])
                   if any(q["section_id"] == s["id"] and q["kind"] == "mcq" for q in questions)]
    plan = build_plan(mode, rules, stock, section_ids, music)
    if mode == "jukebox":
        jukebox.prepare_soon(await music_service.tracks_for(decade))   # round two's jargon titles, written ahead
    lifelines = dict(rules["show"]["lifelines"]) if mode == "show" else ({"fifty": 1} if mode == "sudden_death" else {})
    await db.execute("update runs set status = 'abandoned', finished_at = now()"
                     " where player_id = $1 and quiz_id = $2 and status = 'in_progress'", player["id"], quiz["id"])
    return await db.fetchrow(
        "insert into runs (player_id, quiz_id, mode, decade, plan, lifelines) values ($1,$2,$3,$4,$5,$6) returning *",
        player["id"], quiz["id"], mode, decade, plan, lifelines)   # a Speed Run's clock starts when question one opens


# --------------------------------------------------------------------------- serving items

def _image_view(image_id: str, variant: str, description: str | None, width: int | None = None,
                height: int | None = None) -> dict:
    expires, signature = sign_media(f"{image_id}:{variant}")
    view = {"url": f"/api/media/image/{image_id}?v={variant}&exp={expires}&sig={signature}",
            "alt": description or "A picture from the report", "mode": "memory" if variant == "blur" else "show"}
    if width and height:
        view["width"] = width
        view["height"] = height
    return view


def _picture_mode(rules: dict, question: dict) -> str:
    setting = rules.get("picture_mode", "show")
    if setting == "memory":
        return "memory"
    return "show"


def _wager_options(score: int) -> list[int]:
    """Safe / Bold / Go big. The biggest bet is half your points, so one question cannot erase a good game."""
    pot = max(score, 400)
    return sorted({max(50, round(pot * fraction / 10) * 10) for fraction in (0.1, 0.25, 0.5)})


async def _materialise(run: dict, rules: dict, slot: dict, items: list[dict]) -> dict | None:
    """Turn a slot into the fields of a new run_items row, or None if nothing suitable is left."""
    kind = slot["slot"]
    base = {"slot": kind, "round_no": slot["round"], "round_name": slot["name"],
            "time_limit_sec": int(slot["limit"]), "points_multiplier": float(slot.get("mult", 1))}

    if kind == "music":
        avoid = {i["music_track_id"] for i in items if i["music_track_id"]}
        past = await db.fetch("select distinct music_track_id from run_items where player_id = $1 and quiz_id = $2"
                              " and music_track_id is not null", run["player_id"], run["quiz_id"])
        avoid |= {r["music_track_id"] for r in past}
        if rules.get("soundtrack", True):
            # The songs playing behind this game are not asked about: the player has just been listening to them.
            avoid |= {t["id"] for t in await music_service.soundtrack_rows(run["decade"], run["id"])}
            # Nor are the favourite artist's songs that play now and then: the questions are the same for everyone.
            liked = favorites.keys(await favorites.for_player(run["player_id"]))
            if liked:
                avoid |= {t["id"] for t in await music_service.tracks_for(run["decade"])
                          if favorites.song_key(t["artist"], t["title"]) in liked}
        built = await music_service.build_question(run["decade"], avoid, asks=slot.get("asks"))
        if built is None:
            return None
        track = built["track"]
        return {**base, "music_track_id": track["id"], "correct_index": built["correct_index"],
                "payload": {"title": "Name That Tune", "text": built["text"], "options": built["options"],
                            "asks": built["asks"], "preview_url": track["preview_url"],
                            "attempt": int(slot.get("attempt", 1))}}

    if kind in ("jargon", "song_or_jargon"):
        tracks = await music_service.tracks_for(run["decade"])
        avoid = {i["music_track_id"] for i in items if i["music_track_id"]}
        if rules.get("soundtrack", True):            # never a song the background music is playing: no spoilers
            avoid |= {t["id"] for t in await music_service.soundtrack_rows(run["decade"], run["id"])}
        if kind == "jargon":
            built = await jukebox.jargon_item(tracks, avoid)
        else:
            used = {i["payload"].get("phrase") for i in items if i["slot"] == "song_or_jargon"}
            prior = [i["correct_index"] == 0 for i in items if i["slot"] == "song_or_jargon"]
            prefer_song = _next_binary_choice(prior)
            built = jukebox.song_or_jargon_item(tracks, avoid, used, prefer_song=prefer_song)
        if built is None:
            return None
        payload = {**built["payload"], "attempt": int(slot.get("attempt", 1))}
        if slot.get("no_clip"):
            payload["no_clip"] = True           # this player's browser cannot play the songs (see the media-error route)
        return {**base, "music_track_id": built["track"]["id"] if built["track"] else None,
                "correct_index": built["correct_index"], "payload": payload}

    if kind == "second_chance":
        retried = {i["payload"].get("retry_of") for i in items if i["slot"] == "second_chance"}
        missed = [i for i in items if i["slot"] in ("report", "image") and i["answered_at"] and not i["is_correct"]
                  and i["id"] not in retried and i["question_id"]]
        if not missed:
            return None
        original = missed[0]
        question = await db.fetchrow("select * from questions where id = $1 and status = 'approved'", original["question_id"])
        if question is None:
            return None
        row = _mcq_fields(base, question, rules, force_picture="show")
        row["payload"]["retry_of"] = original["id"]
        return row

    questions = await bank.approved(run["quiz_id"], rules["question_sources"])
    seen = await bank.history(run["player_id"], run["quiz_id"])
    sec_states = await stats.section_states(run["quiz_id"], run["player_id"])
    used = {i["question_id"] for i in items if i["question_id"]}
    load = Counter(i["section_id"] for i in items if i["section_id"])
    asked = [bank.key_terms(q) for q in questions if q["id"] in used]   # so the same fact is not asked twice

    if kind == "fact_fake":
        pool = [q for q in questions if q["kind"] == "fact_fake"]
        prior = [i["correct_index"] == 0 for i in items if i["slot"] == "fact_fake"]
        want_fact = _next_binary_choice(prior)
        question = (bank.choose([q for q in pool if q["is_true"] == want_fact], seen, used, section_load=load,
                                asked=asked, section_states=sec_states)
                    or bank.choose(pool, seen, used, section_load=load, asked=asked, section_states=sec_states))
        if question is None:
            return None
        return {**base, "question_id": question["id"], "section_id": question["section_id"],
                "correct_index": 0 if question["is_true"] else 1,
                "payload": {"title": "Fact or Fake?", "text": question["question_text"], "options": ["FACT", "FAKE"]}}

    mcqs = [q for q in questions if q["kind"] == "mcq"]
    if kind == "image":
        pool = [q for q in mcqs if q["source_type"] == "image"]
    elif slot.get("any_source"):
        pool = mcqs
    else:
        pool = [q for q in mcqs if q["source_type"] != "image"]
    question = bank.choose(pool, seen, used, section_load=load, difficulty=slot.get("difficulty"),
                           section_id=slot.get("section"), asked=asked, section_states=sec_states)
    if question is None and (kind == "image" or slot.get("section")):
        question = bank.choose(mcqs, seen, used, section_load=load, difficulty=slot.get("difficulty"),
                               asked=asked, section_states=sec_states)
    if question is None:
        return None
    row = _mcq_fields(base, question, rules)
    if kind == "final":
        row["payload"]["wager_options"] = _wager_options(run["score"])
    return row


def _mcq_fields(base: dict, question: dict, rules: dict, force_picture: str | None = None) -> dict:
    order = list(range(len(question["options"])))
    random.shuffle(order)
    payload: dict[str, Any] = {"title": question["title"], "text": question["question_text"],
                               "options": [question["options"][i] for i in order]}
    if question["image_id"]:
        payload["image_id"] = question["image_id"]
        payload["picture_mode"] = force_picture or _picture_mode(rules, question)
    row = {**base, "question_id": question["id"], "section_id": question["section_id"],
           "correct_index": order.index(question["correct_index"]), "payload": payload}
    if question["image_id"] and base["slot"] != "image":
        row["time_limit_sec"] = base["time_limit_sec"] and base["time_limit_sec"] + 10
    return row


async def _items(run_id: str) -> list[dict]:
    return await db.fetch("select * from run_items where run_id = $1 order by position", run_id)


def _grace_ms(item: dict) -> int:
    return MUSIC_GRACE_MS if item["slot"] in CLIP_SLOTS else GRACE_MS


def _clip(item: dict) -> dict | None:
    """The song the browser plays while this question is on screen (see CLIP_SLOTS), if any. A player whose browser
    cannot play the songs gets the jargon titles without theirs (`no_clip`, see the media-error route)."""
    payload = item["payload"]
    if item["slot"] not in CLIP_SLOTS or not payload.get("preview_url") or payload.get("no_clip"):
        return None
    return {"previewUrl": payload["preview_url"], "asks": payload.get("asks", "title" if item["slot"] == "jargon" else "artist")}


def _deals_sealed(run: dict, items: list[dict], fields: dict) -> bool:
    """Whether a new item waits, unseen and with its clock stopped, for the browser to open it."""
    if fields["slot"] == "final":
        return False                       # the wager is its own gate
    if not items:
        return True                        # the opening of the game
    if run["mode"] == "jukebox":
        return fields["round_no"] != items[-1]["round_no"]   # each round has a title card; its clips load as they go
    if fields["slot"] == "music":
        return True                        # every music break is introduced, and its clip gets time to load
    return run["mode"] == "show" and fields["round_no"] != items[-1]["round_no"]


async def current_item(run: dict) -> dict | None:
    """The item waiting for an answer, if there is one. Never deals a new one."""
    if run["status"] != "in_progress":
        return None
    item = await db.fetchrow("select * from run_items where run_id = $1 order by position desc limit 1", run["id"])
    return item if item and item["answered_at"] is None else None


async def next_item(run: dict, rules: dict) -> dict | None:
    """Return the current unanswered item, creating it if needed. None means the game is over."""
    if run["status"] != "in_progress":
        return None
    if run["mode"] == "speed" and run["deadline_at"] and _now() >= run["deadline_at"]:
        await finish_run(run, rules)
        return None
    items = await _items(run["id"])
    if items and items[-1]["answered_at"] is None:
        return items[-1]

    plan: list[dict] = list(run["plan"])
    while True:
        if not plan and run["mode"] == "sudden_death":
            questions = await bank.approved(run["quiz_id"], rules["question_sources"])
            level = max((i["round_no"] for i in items), default=0)
            if len(items) < int(rules["sudden_death"]["max_questions"]):
                plan = _sudden_death_block(rules, level, bank.counts(questions), bool(run["decade"]))
        if not plan:
            await db.execute("update runs set plan = '[]'::jsonb where id = $1", run["id"])
            await finish_run(run, rules)
            return None
        slot = plan.pop(0)
        fields = await _materialise(run, rules, slot, items)
        if fields is None:
            if slot["slot"] in ("report", "final") and run["mode"] != "show":
                plan = []                                   # the bank is exhausted for this player and game
            continue
        break

    position = len(items)
    fields.setdefault("question_id", None)
    fields.setdefault("section_id", None)
    fields.setdefault("music_track_id", None)
    needs_wager = fields["slot"] == "final"
    if _deals_sealed(run, items, fields):
        fields["payload"] = {**fields["payload"], "sealed": True}
    item = await db.fetchrow(
        "insert into run_items (run_id, player_id, quiz_id, position, slot, round_no, round_name, question_id,"
        " section_id, music_track_id, payload, correct_index, time_limit_sec, points_multiplier, served_at)"
        " values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) returning *",
        run["id"], run["player_id"], run["quiz_id"], position, fields["slot"], fields["round_no"], fields["round_name"],
        fields["question_id"], fields["section_id"], fields["music_track_id"], fields["payload"],
        fields["correct_index"], fields["time_limit_sec"], fields["points_multiplier"],
        _now() + (timedelta(days=1) if needs_wager else timedelta()))
    await db.execute("update runs set plan = $2, position = $3 where id = $1", run["id"], plan, position + 1)
    return item


async def public_item(item: dict, run: dict) -> dict:
    """What the browser is allowed to see about an unanswered item."""
    payload = item["payload"]
    waiting_for_wager = item["slot"] == "final" and item["wager"] is None
    section = None
    if item["section_id"]:
        section = await db.fetchrow("select id, heading from doc_sections where id = $1", item["section_id"])
    view: dict[str, Any] = {
        "id": item["id"], "position": item["position"], "slot": item["slot"],
        "round": {"number": item["round_no"], "name": item["round_name"]},
        "title": payload.get("title"),
        "section": section,
        "timeLimitSec": item["time_limit_sec"],
        "multiplier": float(item["points_multiplier"]),
        "lifelinesUsed": item["lifelines_used"],
        "removed": payload.get("removed", []),
        "hint": payload.get("hint"),
        "hintRefunded": bool(payload.get("hint_refunded")),
        "peek": payload.get("peek"),
    }
    if waiting_for_wager:
        view.update({"needsWager": True, "wagerOptions": payload.get("wager_options", []), "text": None, "options": None})
        return view
    if payload.get("sealed"):
        # Only what the title card needs. A music clip's address is included so it can load during the introduction.
        view.update({"sealed": True, "title": None, "section": None, "text": None, "options": None})
        if clip := _clip(item):
            view["music"] = clip
        return view
    starts_in_ms = int((item["served_at"] - _now()).total_seconds() * 1000)
    if item["slot"] == "final" and starts_in_ms > 0:
        # Being read out: the question, but not its answers, until its clock starts (see place_wager).
        view.update({"text": payload["text"], "options": None, "wager": item["wager"], "startsInMs": starts_in_ms,
                     "remainingMs": item["time_limit_sec"] * 1000 if item["time_limit_sec"] else None})
        return view
    elapsed_ms = max(0, int((_now() - item["served_at"]).total_seconds() * 1000))
    view.update({
        "text": payload["text"], "options": payload["options"], "wager": item["wager"],
        "remainingMs": (max(0, item["time_limit_sec"] * 1000 - max(0, elapsed_ms - _grace_ms(item)))
                        if item["time_limit_sec"] else None),
    })
    if payload.get("image_id"):
        row = await db.fetchrow("select description, width, height from doc_images where id = $1", payload["image_id"])
        variant = "sharp" if payload.get("picture_mode") == "show" or "peek" in item["lifelines_used"] else "blur"
        view["image"] = _image_view(
            payload["image_id"], variant,
            row["description"] if row and variant == "sharp" else None,
            row["width"] if row else None, row["height"] if row else None,
        )
    if clip := _clip(item):
        view["music"] = clip
    return view


# --------------------------------------------------------------------------- opening, wager + lifelines

async def _current_item(run: dict, item_id: str) -> dict:
    item = await db.fetchrow("select * from run_items where id = $1 and run_id = $2", item_id, run["id"])
    if item is None:
        raise GameError("That question is not part of this game.", 404)
    return item


async def open_item(run: dict, rules: dict, item_id: str) -> tuple[dict, dict]:
    """Show a sealed item to the player and start its clock. Returns (item, run); safe to call twice."""
    item = await _current_item(run, item_id)
    if item["answered_at"] is None and item["payload"].get("sealed"):
        payload = {key: value for key, value in item["payload"].items() if key != "sealed"}
        opened = await db.fetchrow(
            "update run_items set payload = $2, served_at = $3 where id = $1 and payload ? 'sealed' returning *",
            item_id, payload, _now())
        item = opened or await _current_item(run, item_id)
    if run["mode"] == "speed" and run["deadline_at"] is None and run["status"] == "in_progress":
        deadline = _now() + timedelta(seconds=int(rules["speed"]["clock_sec"]) + 1)
        await db.execute("update runs set deadline_at = $2 where id = $1 and deadline_at is null", run["id"], deadline)
        run = await db.fetchrow("select * from runs where id = $1", run["id"])
    return item, run


def reading_seconds(text: str | None) -> float:
    """How long the host takes to read a question out, lead-in included (see READ_WORDS_PER_SEC)."""
    words = len((text or "").split())
    return min(READ_MAX_SEC, (READ_LEAD_WORDS + words) / READ_WORDS_PER_SEC + READ_PAD_SEC)


async def place_wager(run: dict, item_id: str, amount: int, *, read_aloud: bool = False) -> dict:
    item = await _current_item(run, item_id)
    if item["slot"] != "final" or item["answered_at"] is not None:
        raise GameError("There is nothing to wager on right now.")
    if item["wager"] is not None:
        return item
    if amount not in item["payload"].get("wager_options", []):
        raise GameError("Pick one of the wager amounts shown.")
    # The question is read out first: its clock starts when the answers appear (start_clock), or at the latest when
    # the reading should be over. Only for a page that knows to ask for the answers - one loaded before this existed
    # (a game carried across an update of the server) gets its question at once, as it always did.
    starts = _now() + timedelta(seconds=reading_seconds(item["payload"].get("text")) if read_aloud else 0)
    return await db.fetchrow("update run_items set wager = $2, served_at = $3 where id = $1 returning *",
                             item_id, amount, starts)


async def start_clock(run: dict, item_id: str) -> dict:
    """The final question's answers are on screen (the reading is over, or the player skipped it): its clock starts
    now. It can only ever start earlier than planned, so skipping the reading never gains anyone time."""
    item = await _current_item(run, item_id)
    if item["slot"] == "final" and item["wager"] is None:
        raise GameError("Place your wager first.")
    if item["answered_at"] is not None:
        return item
    return await db.fetchrow("update run_items set served_at = least(served_at, $2) where id = $1 returning *",
                             item_id, _now())


async def use_lifeline(run: dict, item_id: str, kind: str, hint_writer=None) -> dict:
    item = await _current_item(run, item_id)
    if item["answered_at"] is not None:
        raise GameError("That question has already been answered.")
    if item["payload"].get("sealed"):
        raise GameError("That question has not been opened yet.")
    if item["slot"] not in ("report", "image") or kind not in LIFELINE_MULTIPLIER:
        raise GameError("That lifeline cannot be used on this question.")
    if kind in item["lifelines_used"]:
        return item
    remaining = dict(run["lifelines"])
    if remaining.get(kind, 0) <= 0:
        raise GameError("You have used that lifeline already.")
    payload = dict(item["payload"])
    question = await db.fetchrow("select * from questions where id = $1", item["question_id"])
    thinking = timedelta()

    charge = True
    if kind == "fifty":
        wrong = [i for i in range(len(payload["options"])) if i != item["correct_index"]]
        payload["removed"] = sorted(random.sample(wrong, 2))
    elif kind == "peek":
        excerpt = question["source_quote"] or ""
        if question["chunk_id"]:
            chunk = await db.fetchval("select chunk_text from doc_chunks where id = $1", question["chunk_id"])
            excerpt = _excerpt(chunk or "", question["source_quote"] or "")
        if payload.get("image_id"):
            if payload.get("picture_mode") == "memory":
                excerpt = "Take a proper look at the picture."
            else:
                img_ctx = await db.fetchval("select context_text from doc_images where id = $1", payload["image_id"])
                excerpt = _excerpt(img_ctx or "", question["source_quote"] or "") if img_ctx else ""
                if not excerpt.strip():
                    raise GameError("The full picture is already on screen. Try 50/50 or Hint.")
        if not excerpt.strip():                   # a hand-written question with no passage behind it
            raise GameError("There is no passage to peek at for this one. Try another lifeline.")
        payload["peek"] = excerpt[:700]
        thinking = timedelta(seconds=PEEK_GRACE_SEC)   # reading grace so peeking does not drain the clock
    else:
        asked = _now()
        raw_hint = await hint_writer(question) if hint_writer else "Think about what the report said on this."
        if isinstance(raw_hint, tuple):
            hint_str, is_fallback = raw_hint
        else:
            hint_str, is_fallback = raw_hint, False
        payload["hint"] = hint_str
        thinking = _now() - asked                 # the clock does not run while the hint is being written
        if is_fallback:
            charge = False                        # never charge 20% + a lifeline for a generic fallback reminder
            payload["hint_refunded"] = True

    multiplier = float(item["points_multiplier"])
    if charge:
        remaining[kind] -= 1
        multiplier *= LIFELINE_MULTIPLIER[kind]
        await db.execute("update runs set lifelines = $2 where id = $1", run["id"], remaining)
    return await db.fetchrow(
        "update run_items set payload = $2, points_multiplier = $3, lifelines_used = array_append(lifelines_used, $4),"
        " served_at = served_at + $5 where id = $1 returning *", item_id, payload, multiplier, kind, thinking)


def _excerpt(passage: str, quote: str, radius: int = 320) -> str:
    """The part of the passage around the supporting quote."""
    position = passage.lower().find(quote.lower()[:40]) if quote else -1
    if position < 0 or len(passage) <= radius * 2:
        return passage[: radius * 2]
    start = max(0, position - radius // 2)
    end = min(len(passage), position + len(quote) + radius // 2)
    return ("…" if start else "") + passage[start:end].strip() + ("…" if end < len(passage) else "")


# --------------------------------------------------------------------------- answering

def _points(run: dict, rules: dict, item: dict, correct: bool, timed_out: bool, time_fraction: float) -> int:
    mode, slot = run["mode"], item["slot"]
    multiplier = float(item["points_multiplier"])
    if slot == "final" and item["wager"] is not None:
        return item["wager"] if correct else -item["wager"]
    if mode == "straight":
        return 100 if correct else 0
    if mode == "speed":
        return round(100 * (1 + 0.1 * min(run["streak"], 10))) if correct else 0
    if mode == "jukebox":
        if not correct:
            return 0                                        # a warm-up: nothing costs points
        return round((50 + 50 * time_fraction) * multiplier) + 10 * min(run["streak"], 5)
    if mode == "sudden_death":
        if not correct:
            return 0
        if slot == "music":
            return 75
        return round((50 + 50 * time_fraction) * (1 + 0.1 * (item["round_no"] - 1)) * multiplier)
    # Game Show — wrong guesses and timeouts are symmetric (0 pts) so stalling never beats guessing.
    if slot == "fact_fake":
        return round(30 + 20 * time_fraction) if correct else 0
    if not correct:
        return 0
    streak_bonus = 10 * min(run["streak"], 5)               # run["streak"] is the streak *before* this answer
    return round((50 + 50 * time_fraction) * multiplier) + streak_bonus


async def submit_answer(run: dict, rules: dict, item_id: str, choice: int | None) -> dict:
    item = await _current_item(run, item_id)
    if item["answered_at"] is not None:
        return await _reveal(item, await db.fetchrow("select * from runs where id = $1", run["id"]), rules)
    if item["slot"] == "final" and item["wager"] is None:
        raise GameError("Place your wager first.")
    if item["payload"].get("sealed"):
        raise GameError("That question has not been opened yet.")
    if item["slot"] == "final" and item["served_at"] > _now():
        raise GameError("The answers are not up yet.")
    if run["status"] != "in_progress":
        raise GameError("This game is already over.", 409)

    now = _now()
    elapsed_ms = max(0, int((now - item["served_at"]).total_seconds() * 1000))
    limit_ms = item["time_limit_sec"] * 1000
    clock_expired = run["mode"] == "speed" and run["deadline_at"] and now > run["deadline_at"] + timedelta(seconds=2)
    timed_out = choice is None or clock_expired or (limit_ms and elapsed_ms > limit_ms + LATE_MS)
    if choice is not None and not (0 <= choice < len(item["payload"]["options"])):
        raise GameError("That is not one of the options.")
    if choice is not None and choice in item["payload"].get("removed", []):
        raise GameError("That option was removed.")
    correct = (not timed_out) and choice == item["correct_index"]
    time_fraction = 1.0 if not limit_ms else max(0.0, min(1.0, 1 - max(0, elapsed_ms - _grace_ms(item)) / limit_ms))
    points = _points(run, rules, item, correct, bool(timed_out), time_fraction)

    saved = await db.fetchrow(
        "update run_items set answered_at = $2, selected_index = $3, is_correct = $4, timed_out = $5,"
        " time_taken_ms = $6, points = $7 where id = $1 and answered_at is null returning *",
        item_id, now, None if timed_out else choice, correct, bool(timed_out), elapsed_ms, points)
    if saved is None:                                       # a double tap: the first answer stands
        return await _reveal(await _current_item(run, item_id),
                             await db.fetchrow("select * from runs where id = $1", run["id"]), rules)

    is_music = item["slot"] in MUSIC_SLOTS
    counts_for_streak = item["slot"] != "fact_fake" or run["mode"] == "speed"
    streak = (run["streak"] + 1 if correct else 0) if counts_for_streak else run["streak"]
    strikes = run["strikes"]
    strike_restored = False
    deadline = run["deadline_at"]
    if run["mode"] == "sudden_death":
        if is_music:
            if correct and strikes > 0:
                strikes -= 1
                strike_restored = True
        elif not correct:
            strikes += 1
    if run["mode"] == "speed" and not correct and deadline:
        deadline = deadline - timedelta(seconds=int(rules["speed"]["wrong_penalty_sec"]))

    updated = await db.fetchrow(
        "update runs set score = greatest(0, score + $2), streak = $3, streak_max = greatest(streak_max, $3),"
        " strikes = $4, deadline_at = $5, questions_answered = questions_answered + 1,"
        " correct_answers = correct_answers + $6, report_answered = report_answered + $7,"
        " report_correct = report_correct + $8, music_answered = music_answered + $9,"
        " music_correct = music_correct + $10 where id = $1 returning *",
        run["id"], points, streak, strikes, deadline, int(correct), int(not is_music), int(correct and not is_music),
        int(is_music), int(correct and is_music))
    if item["question_id"]:
        await db.execute("update questions set times_served = times_served + 1, times_correct = times_correct + $2"
                         " where id = $1", item["question_id"], int(correct))

    over = (updated["mode"] == "sudden_death" and strikes >= int(rules["sudden_death"]["strikes"])) or (
        updated["mode"] == "speed" and deadline is not None and now >= deadline)
    if over:
        updated = await finish_run(updated, rules)
    reveal = await _reveal(saved, updated, rules)
    reveal["strikeRestored"] = strike_restored
    return reveal


async def _reveal(item: dict, run: dict, rules: dict) -> dict:
    payload = item["payload"]
    result: dict[str, Any] = {
        "itemId": item["id"], "quizId": run["quiz_id"], "questionId": item["question_id"],
        "slot": item["slot"], "correct": bool(item["is_correct"]), "timedOut": item["timed_out"],
        "selectedIndex": item["selected_index"], "correctIndex": item["correct_index"],
        "correctAnswer": payload["options"][item["correct_index"]], "points": item["points"],
        "state": run_state(run, rules), "finished": run["status"] != "in_progress",
    }
    if item["question_id"]:
        question = await db.fetchrow(
            "select q.explanation, q.source_quote, q.source_type, q.video_url, q.video_title,"
            " s.id as section_id, s.heading from questions q"
            " left join doc_sections s on s.id = q.section_id where q.id = $1", item["question_id"])
        if question:
            result["explanation"] = question["explanation"]
            result["sourceQuote"] = question["source_quote"]
            if question["section_id"]:
                result["section"] = {"id": question["section_id"], "heading": question["heading"]}
            clip = video.parse_youtube(question.get("video_url"), question.get("video_title"))
            if clip:
                result["video"] = clip
    if payload.get("image_id"):
        row = await db.fetchrow("select description, width, height from doc_images where id = $1", payload["image_id"])
        result["image"] = _image_view(
            payload["image_id"], "sharp",
            row["description"] if row else None,
            row["width"] if row else None, row["height"] if row else None,
        )
    if item["music_track_id"]:
        track = await db.fetchrow("select artist, title, artwork_url, track_url from music_tracks where id = $1",
                                  item["music_track_id"])
        if track:
            result["music"] = {"artist": track["artist"], "title": track["title"],
                               "artworkUrl": track["artwork_url"], "trackUrl": track["track_url"]}
            # Song or Synergy?'s song plays once it has been answered: the reward for working it out. (A jargon title's
            # song is already playing; pages from before it was, still play it from here.)
            if item["slot"] in ("jargon", "song_or_jargon") and payload.get("preview_url") and not payload.get("no_clip"):
                result["music"]["previewUrl"] = payload["preview_url"]
    if payload.get("explain"):
        result["explanation"] = payload["explain"]
    return result


def run_state(run: dict, rules: dict) -> dict:
    """The scoreboard numbers the browser shows during a game."""
    state: dict[str, Any] = {
        "runId": run["id"], "mode": run["mode"], "modeName": MODES[run["mode"]]["name"], "status": run["status"],
        "score": run["score"], "streak": run["streak"], "answered": run["questions_answered"],
        "correct": run["correct_answers"], "decade": run["decade"], "lifelines": run["lifelines"],
        "remaining": len(run["plan"]) if run["mode"] in ("show", "straight", "jukebox") else None,   # "Question 3/12"
        # Lets the browser show a round's title card *before* asking for its first question,
        # so the card never eats into the player's answering time.
        "nextRound": run["plan"][0]["name"] if run["plan"] else None,
    }
    if run["mode"] == "sudden_death":
        state["strikes"] = run["strikes"]
        state["maxStrikes"] = int(rules["sudden_death"]["strikes"])
    if run["mode"] == "speed":
        state["clockSec"] = int(rules["speed"]["clock_sec"])
        if run["deadline_at"]:
            state["deadlineAt"] = run["deadline_at"].isoformat()
            state["remainingMs"] = max(0, int((run["deadline_at"] - _now()).total_seconds() * 1000))
    return state


# --------------------------------------------------------------------------- finishing

async def finish_run(run: dict, rules: dict, abandoned: bool = False) -> dict:
    current = await db.fetchrow("select * from runs where id = $1", run["id"])
    if current["status"] != "in_progress":
        return current
    accuracy = round(100 * current["report_correct"] / current["report_answered"]) if current["report_answered"] else 0
    status = "abandoned" if abandoned and current["questions_answered"] < 3 else "completed"
    badge = badges.evaluate_run(current, rules, status=status)["label"]
    passed = accuracy >= int(rules["straight"]["pass_mark"]) if current["mode"] == "straight" else None
    unused_lifelines = (
        sum(max(0, int(v)) for v in (current["lifelines"] or {}).values())
        if status == "completed" and not abandoned and (
            current["mode"] == "show"
            or (current["mode"] == "sudden_death" and current["strikes"] < int(rules["sudden_death"]["strikes"]))
        )
        else 0
    )
    lifeline_bonus = unused_lifelines * UNUSED_LIFELINE_BONUS
    duration = int((_now() - current["started_at"]).total_seconds())
    finished = await db.fetchrow(
        "update runs set status = $2, finished_at = now(), duration_sec = $3, badge = $4, passed = $5,"
        " score = score + $6, plan = '[]'::jsonb"
        " where id = $1 and status = 'in_progress' returning *", run["id"], status, duration, badge, passed, lifeline_bonus)
    if finished is None:
        return await db.fetchrow("select * from runs where id = $1", run["id"])
    await db.execute("delete from run_items where run_id = $1 and answered_at is null", run["id"])
    if status == "completed":
        await db.execute("update players set games_played_count = games_played_count + 1, last_seen_at = now()"
                         " where id = $1", run["player_id"])
    return finished
