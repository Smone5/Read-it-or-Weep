"""The rules that shape a game: what is in each style of play, and when a question waits behind a title card.

These are pure functions – no database, no network – so they run anywhere in a second.
"""
from services import engine
from services.settings_store import DEFAULTS, MODES, mode_view

RULES = DEFAULTS["game"]
PLENTY = {"mcq": 40, "fact_fake": 20, "picture": 10}


def rounds(plan):
    names = []
    for slot in plan:
        if not names or names[-1] != slot["name"]:
            names.append(slot["name"])
    return names


def test_game_show_has_every_round_in_order():
    plan = engine.build_plan("show", RULES, PLENTY, ["s1", "s2"], music=True)
    assert rounds(plan) == ["Warm-Up", "Music Break", "Picture Round", "The Deep End", "Lightning Round",
                            "Music Break", "Second Chance", "Final Wager"]
    assert [s["slot"] for s in plan].count("music") == RULES["show"]["music_rounds"]
    assert [s["slot"] for s in plan].count("fact_fake") == RULES["show"]["lightning_items"]
    assert plan[-1]["slot"] == "final"
    report = [s for s in plan if s["slot"] in ("report", "image", "final")]
    assert len(report) == RULES["show"]["questions"]


def test_game_show_without_music_or_pictures_still_works():
    plan = engine.build_plan("show", RULES, {"mcq": 10, "fact_fake": 0, "picture": 0}, ["s1"], music=False)
    assert "music" not in {s["slot"] for s in plan}
    assert "Picture Round" not in rounds(plan) and "Lightning Round" not in rounds(plan)
    assert plan[-1]["slot"] == "final"


def test_first_question_is_easy_and_the_deep_end_is_hard():
    plan = engine.build_plan("show", RULES, PLENTY, ["s1"], music=True)
    assert plan[0]["difficulty"] == "easy"
    deep = [s for s in plan if s["name"] == "The Deep End"]
    assert deep[-1]["difficulty"] == "hard"


def test_straight_quiz_covers_every_section_and_nothing_else():
    plan = engine.build_plan("straight", RULES, PLENTY, ["a", "b", "c"], music=True)
    assert {s["slot"] for s in plan} == {"report"}
    assert [s["section"] for s in plan].count("a") == RULES["straight"]["per_section"]
    assert all(s["limit"] == 0 for s in plan)            # no clock


def test_speed_run_mixes_statements_and_questions_without_title_cards():
    plan = engine.build_plan("speed", RULES, PLENTY, [], music=True)
    assert {s["slot"] for s in plan} == {"fact_fake", "report"}
    assert {s["name"] for s in plan} == {"Speed Run"}


def test_sudden_death_gets_faster_and_ends_each_level_on_music():
    first = engine._sudden_death_block(RULES, 0, PLENTY, music=True)
    later = engine._sudden_death_block(RULES, 4, PLENTY, music=True)
    assert first[-1]["slot"] == "music" and later[-1]["slot"] == "music"
    assert later[0]["limit"] < first[0]["limit"]
    assert later[0]["limit"] >= RULES["sudden_death"]["timer_min_sec"]


# --- sealing: an item that waits, unseen and with its clock stopped, behind a title card --------------

def fields(slot, round_no):
    return {"slot": slot, "round_no": round_no}


def test_the_opening_item_is_always_sealed():
    for mode in MODES:
        assert engine._deals_sealed({"mode": mode}, [], fields("report", 1))


def test_show_seals_the_first_item_of_each_round_only():
    run = {"mode": "show"}
    earlier = [{"round_no": 1}]
    assert not engine._deals_sealed(run, earlier, fields("report", 1))
    assert engine._deals_sealed(run, earlier, fields("image", 2))


def test_music_is_introduced_in_every_style_but_levels_are_not():
    run = {"mode": "sudden_death"}
    earlier = [{"round_no": 1}]
    assert engine._deals_sealed(run, earlier, fields("music", 1))
    assert not engine._deals_sealed(run, earlier, fields("report", 2))


def test_the_final_is_held_by_its_wager_not_a_seal():
    assert not engine._deals_sealed({"mode": "show"}, [{"round_no": 6}], fields("final", 7))


def test_music_gets_a_longer_grace_than_text():
    assert engine._grace_ms({"slot": "music"}) > engine._grace_ms({"slot": "report"})


# --- scoring ----------------------------------------------------------------------------------------------

def item(slot="report", multiplier=1.0, wager=None, round_no=1):
    return {"slot": slot, "points_multiplier": multiplier, "wager": wager, "round_no": round_no}


def test_show_scoring_rewards_speed_and_streaks_without_timeout_exploit():
    run = {"mode": "show", "streak": 0}
    fast = engine._points(run, RULES, item(), True, False, 1.0)
    slow = engine._points(run, RULES, item(), True, False, 0.0)
    assert fast == 100 and slow == 50
    assert engine._points({"mode": "show", "streak": 3}, RULES, item(), True, False, 1.0) == 130
    assert engine._points(run, RULES, item(), False, False, 0.5) == 0          # wrong guess == timeout (no stalling exploit)
    assert engine._points(run, RULES, item(), False, True, 0.0) == 0


def test_music_and_second_chances_can_only_help():
    run = {"mode": "show", "streak": 0}
    assert engine._points(run, RULES, item("music"), False, False, 0.5) == 0
    assert engine._points(run, RULES, item("second_chance", 0.5), False, False, 0.5) == 0
    assert engine._points(run, RULES, item("second_chance", 0.5), True, False, 1.0) == 50


def test_the_wager_is_won_or_lost_exactly():
    run = {"mode": "show", "streak": 2}
    assert engine._points(run, RULES, item("final", wager=250), True, False, 0.2) == 250
    assert engine._points(run, RULES, item("final", wager=250), False, False, 0.2) == -250


def test_a_wager_can_never_wipe_out_a_good_game():
    assert engine._wager_options(2000) == [200, 500, 1000]
    assert max(engine._wager_options(0)) == 200            # even from nothing there is something to play for


def test_lifelines_cost_points():
    assert all(0 < factor < 1 for factor in engine.LIFELINE_MULTIPLIER.values())


# --- what players are told about each style ----------------------------------------------------------------

def test_taglines_quote_the_quiz_s_own_numbers():
    rules = {**RULES, "sudden_death": {**RULES["sudden_death"], "strikes": 5}, "speed": {**RULES["speed"], "clock_sec": 60}}
    assert mode_view("sudden_death", rules)["tagline"].startswith("Five strikes")
    assert mode_view("speed", rules)["tagline"].startswith("60 seconds")
    assert mode_view("sudden_death", RULES)["tagline"].startswith("Three strikes")
    one = {**RULES, "sudden_death": {**RULES["sudden_death"], "strikes": 1}}
    assert mode_view("sudden_death", one)["tagline"].startswith("One strike and")


# --- when a question is pulled for review ------------------------------------------------------------------

def test_a_hard_question_is_not_mistaken_for_a_broken_one():
    from services.stats import worse_than_guessing

    assert not worse_than_guessing(1, 8, 4)            # eight unlucky players prove nothing
    assert not worse_than_guessing(4, 20, 4)           # 20% right: hard, but no worse than guessing
    assert not worse_than_guessing(0, 11, 4)           # too few answers to say


def test_a_question_players_do_worse_than_guessing_on_is_pulled():
    from services.stats import worse_than_guessing

    assert worse_than_guessing(0, 14, 4)               # nobody in fourteen: the marked answer is probably wrong
    assert worse_than_guessing(1, 24, 4)
    assert worse_than_guessing(2, 12, 2)               # a true/false statement almost everyone gets "wrong"
    assert not worse_than_guessing(5, 12, 2)


def test_fact_fake_is_unpredictable_yet_balanced():
    import random
    rng_state = random.getstate()
    random.seed(42)
    try:
        # After a True, Q2 must NOT always be False: both repeats and flips should happen naturally.
        after_true = [engine._next_binary_choice([True]) for _ in range(200)]
        assert 50 < sum(after_true) < 110              # ~40% True, ~60% False (never 0%!)

        # Hard guardrails: 3 identical in a row always flips; imbalance never exceeds ±3.
        assert engine._next_binary_choice([True, True, True]) is False
        assert engine._next_binary_choice([False, False, False]) is True

        seq: list[bool] = []
        for _ in range(60):
            seq.append(engine._next_binary_choice(seq))
            assert abs(sum(1 if v else -1 for v in seq)) <= 3
        # Verify no 4-in-a-row runs anywhere.
        for i in range(len(seq) - 3):
            assert not (seq[i] == seq[i + 1] == seq[i + 2] == seq[i + 3])
    finally:
        random.setstate(rng_state)


def test_every_mode_starts_easy_and_ramps_harder():
    # Show: first 2 Warm-Up slots are easy, Picture Round starts easy, Deep End finishes hard, Final is hard.
    show = engine.build_plan("show", RULES, PLENTY, ["s1", "s2", "s3"], music=True)
    warmup = [s for s in show if s["name"] == "Warm-Up"]
    assert warmup[0]["difficulty"] == "easy" and warmup[1]["difficulty"] == "easy"
    pictures = [s for s in show if s["name"] == "Picture Round"]
    assert pictures[0]["difficulty"] == "easy"
    assert [s for s in show if s["slot"] == "final"][0]["difficulty"] == "hard"

    # Straight & Speed Run also start easy and ramp to hard.
    straight = engine.build_plan("straight", RULES, PLENTY, ["s1", "s2", "s3"], music=False)
    assert straight[0]["difficulty"] == "easy" and straight[-1]["difficulty"] == "hard"
    speed = engine.build_plan("speed", RULES, PLENTY, ["s1", "s2"], music=False)
    assert speed[0]["difficulty"] == "easy" and speed[10]["difficulty"] == "medium" and speed[20]["difficulty"] == "hard"

    # bank.choose honors difficulty='easy' even when a hard question sits in an untouched section.
    from services import bank
    easy_q = {"id": "e1", "section_id": "s1", "difficulty": "easy", "pinned": False, "kind": "mcq",
              "question_text": "Easy", "options": ["A", "B", "C", "D"], "correct_index": 0}
    hard_q = {"id": "h1", "section_id": "s2", "difficulty": "hard", "pinned": False, "kind": "mcq",
              "question_text": "Hard", "options": ["A", "B", "C", "D"], "correct_index": 0}
    picked = bank.choose([hard_q, easy_q], seen={}, used=set(), difficulty="easy",
                         section_states={"s1": "cleared", "s2": "new"})
    assert picked is not None and picked["id"] == "e1"


def test_reveal_image_only_shows_when_actually_relevant_to_question():
    imgs = [
        {"id": "sb", "section_id": "s5",
         "caption": "Figure 5: Points scored by Denver versus opponents across Denver's three Super Bowl victories.",
         "description": "Grouped bar chart of Super Bowl XXXII (Green Bay), Super Bowl XXXIII (Atlanta), and Super Bowl 50 (Carolina)."},
        {"id": "map", "section_id": "s2",
         "caption": "Figure 2: Fan perimeter map showing gates, four-corner ramps, 50-yard elevators, RTD Light Rail, and rideshare zones.",
         "description": "Bird's-eye perimeter map with NW, NE, SW, SE ramps, Gate 5 rideshare drop-off, and Ball Arena pick-up."},
        {"id": "food", "section_id": "s3",
         "caption": "Figure 3: Level-by-level fan directory of food favorites, First Aid centers, and guest services.",
         "description": "500 Level Upper Deck First Aid Sec 534, Cupbop 518, Chillville 539, 100 Level Reverse ATMs."},
    ]
    # Unrelated question in Section 5 (Thunder the mascot) must NOT pull up the Super Bowl chart!
    assert engine._match_reveal_image(
        "What does Thunder, the Broncos' live mascot, do after every Denver touchdown?",
        "Gallops across the field",
        imgs,
        section_id="s5",
    ) is None

    # Directly related Super Bowl question DOES match Section 5's Super Bowl chart.
    matched_sb = engine._match_reveal_image(
        "Who earned Super Bowl XXXII MVP in Denver's win over Green Bay?",
        "Terrell Davis",
        imgs,
        section_id="s5",
    )
    assert matched_sb is not None and matched_sb["id"] == "sb"

    # Section 2 ramp question matches Section 2's Perimeter Map (NEVER Section 3's food directory!).
    matched_ramp = engine._match_reveal_image(
        "From which parts of Empower Field can fans take an ADA-compliant pedestrian ramp all the way to the Upper Deck?",
        "All four stadium corners",
        imgs,
        section_id="s2",
    )
    assert matched_ramp is not None and matched_ramp["id"] == "map"
