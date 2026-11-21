"""Choosing what to ask: new questions first, must-asks jump the queue, and no fact twice in one game."""
from collections import Counter

from services import bank


def question(qid, answer, *, kind="mcq", pinned=False, section="s1", difficulty="medium", text="A question?"):
    options = [answer, "wrong one", "wrong two", "wrong three"] if kind == "mcq" else None
    return {"id": qid, "kind": kind, "options": options, "correct_index": 0 if kind == "mcq" else None,
            "question_text": text, "pinned": pinned, "section_id": section, "difficulty": difficulty}


def test_same_fact_spots_an_answer_reworded():
    a = bank.key_terms(question("a", "Whether to add a second pilot team"))
    b = bank.key_terms(question("b", "Add a second pilot team"))
    c = bank.key_terms(question("c", "Delay the customer portal"))
    assert bank.same_fact(a, b)
    assert not bank.same_fact(a, c)


def test_same_fact_treats_numbers_as_whole_values():
    a = bank.key_terms(question("a", "$184.2 million"))
    assert bank.same_fact(a, bank.key_terms(question("b", "184.2 million dollars")))
    assert not bank.same_fact(a, bank.key_terms(question("c", "$148.2 million")))


def test_a_statement_is_compared_by_its_own_words():
    fact = question("a", None, kind="fact_fake", text="Annualized driver turnover reached 25%, above the 22% ceiling.")
    fake = question("b", None, kind="fact_fake", text="Driver turnover fell to 12%, well under the ceiling.")
    again = question("c", None, kind="fact_fake", text="Driver turnover reached 25%, above its 22% ceiling.")
    assert bank.same_fact(bank.key_terms(fact), bank.key_terms(again))
    assert not bank.same_fact(bank.key_terms(fact), bank.key_terms(fake))


def test_new_questions_come_before_ones_already_seen():
    seen = {"old-right": {"times": 1, "correct": True}, "old-wrong": {"times": 1, "correct": False}}
    pool = [question("old-right", "alpha"), question("old-wrong", "beta"), question("new", "gamma")]
    picks = Counter(bank.choose(pool, seen, set())["id"] for _ in range(40))
    assert picks == {"new": 40}
    picks = Counter(bank.choose(pool, seen, {"new"})["id"] for _ in range(40))
    assert picks == {"old-wrong": 40}                     # then the ones they got wrong


def test_a_pinned_question_jumps_the_queue_until_it_is_answered_correctly():
    pool = [question("pinned", "alpha", pinned=True), question("plain", "beta")]
    assert all(bank.choose(pool, {}, set())["id"] == "pinned" for _ in range(30))
    seen = {"pinned": {"times": 1, "correct": True}}
    assert all(bank.choose(pool, seen, set())["id"] == "plain" for _ in range(30))


def test_the_same_fact_is_not_asked_twice_while_anything_else_is_left():
    asked = [bank.key_terms(question("first", "Add a second pilot team"))]
    pool = [question("repeat", "Whether to add a second pilot team"), question("fresh", "Dock scheduling delays")]
    assert all(bank.choose(pool, {}, {"first"}, asked=asked)["id"] == "fresh" for _ in range(40))
    # ...but it is still better than nothing
    assert bank.choose(pool[:1], {}, {"first"}, asked=asked)["id"] == "repeat"


def test_questions_spread_across_sections():
    pool = [question("a", "alpha", section="s1"), question("b", "beta", section="s2")]
    load = Counter({"s1": 3})
    picks = Counter(bank.choose(pool, {}, set(), section_load=load)["id"] for _ in range(40))
    assert picks == {"b": 40}


def test_nothing_left_is_reported_as_nothing():
    assert bank.choose([question("a", "alpha")], {}, {"a"}) is None


def test_blind_spot_sections_are_prioritised_on_replays():
    pool = [question("cleared-q", "alpha", section="s1"), question("blind-q", "beta", section="s2")]
    states = {"s1": "cleared", "s2": "tried"}
    picks = Counter(bank.choose(pool, {}, set(), section_states=states)["id"] for _ in range(40))
    assert picks == {"blind-q": 40}


def test_difficulty_band_beats_random_jitter():
    pool = [question("easy-q", "alpha", difficulty="easy"), question("hard-q", "beta", difficulty="hard")]
    picks = Counter(bank.choose(pool, {}, set(), difficulty="easy")["id"] for _ in range(40))
    assert picks == {"easy-q": 40}
