"""The host's hint guard, the results-screen playlist, live-game lead-ins and the music safety net."""
import numpy as np
import pytest

from services import host_service, live, music_service, settings_store


# --- hints must help without handing over the answer ---------------------------------------------------

def test_a_hint_that_repeats_the_answer_is_rejected():
    assert host_service._gives_away("It was 184.2 million dollars in the third quarter.", "$184.2 million")
    assert host_service._gives_away("Think about the pallet-wrap standard at Columbus.", "A new pallet-wrap standard at the Columbus hub")


def test_a_hint_that_only_narrows_the_field_is_allowed():
    assert not host_service._gives_away("Look at what changed in how freight is prepared before loading.",
                                        "A new pallet-wrap standard at the Columbus hub")
    assert not host_service._gives_away("It is a figure from the income statement, in millions.", "$184.2 million")


# --- sarcasm scale ---------------------------------------------------------------------------------------

def test_every_sarcasm_level_has_a_style_and_the_ends_differ():
    styles = [host_service.sarcasm_style(level) for level in range(0, 11)]
    assert all(styles)
    assert styles[0] != styles[10]
    assert "No sarcasm" in styles[0]


def test_the_persona_never_carries_report_facts():
    persona = host_service.build_persona(name="Max", game_title="Read It or Weep", company="Acme", sarcasm=6,
                                         quiz_title="Q3 Review", outline=["1. Summary", "2. Finance"])
    assert "Max" in persona and "Read It or Weep" in persona
    assert "1. Summary" in persona                      # section names are fine: they give nothing away
    assert "never" in persona.lower() and "answer" in persona.lower()


# --- how the host performs -------------------------------------------------------------------------------

def test_every_manner_has_its_own_direction_and_none_asks_for_speed():
    assert set(host_service.MANNERS) == set(settings_store.HOST_MANNERS)
    personas = {manner: host_service.build_persona(name="Max", game_title="Read It or Weep", company="Acme", sarcasm=6,
                                                   manner=manner) for manner in settings_store.HOST_MANNERS}
    assert len(set(personas.values())) == len(personas)          # three manners, three different directions
    for text in personas.values():
        assert "talk fast" not in text.lower()                    # speed is what made the host sound rushed
        assert "never rushed" in text.lower() or "never a sprint" in text.lower()
        assert "# The jokes" in text                              # the comedy guidance travels with every manner


def test_an_unknown_manner_plays_as_the_default():
    default = host_service.build_persona(name="Max", game_title="G", company="Acme", sarcasm=6)
    assert host_service.build_persona(name="Max", game_title="G", company="Acme", sarcasm=6, manner="shouty") == default
    assert host_service.MANNERS[host_service.DEFAULT_MANNER]["sound"] in default


def test_the_voice_is_not_sped_up_unless_asked():
    assert host_service.PACE_SPEED["snappy"] == 1.0
    assert host_service.PACE_SPEED["relaxed"] < 1.0 < host_service.PACE_SPEED["rapid"]


def test_a_gentle_host_keeps_the_manner_but_loses_the_teasing():
    gentle = host_service.build_persona(name="Max", game_title="G", company="Acme", sarcasm=1, manner="deadpan")
    assert "the teasing is gone" in gentle and "No sarcasm at all" in gentle


def test_a_saved_character_keeps_a_known_manner_only():
    cleaned = settings_store._clean_host({"hosts": [
        {"name": "Max", "voice": "cedar", "manner": "showman"},
        {"name": "Roxy", "voice": "coral", "manner": "shouty"},
        {"name": "Sam", "voice": "verse"}]})
    assert [host["manner"] for host in cleaned["hosts"]] == ["showman", "wiseguy", "wiseguy"]


def test_default_hosts_cover_all_manners_and_distinct_crt_registers():
    from services import eleven
    hosts = settings_store.DEFAULTS["host"]["hosts"]
    assert {h["manner"] for h in hosts} == set(settings_store.HOST_MANNERS)
    assert all(h["style"].strip() for h in hosts)
    registers = [eleven.BY_ID[h["voice"]].register for h in hosts]
    assert len(set(registers)) == len(hosts)
    assert eleven.model_for(eleven.BY_ID["eleven:southern"]) == "eleven_v3"
    assert eleven.match_account_voices([{"voice_id": "v1", "name": "Southern charmer", "sharing": {}}]) == {
        "eleven:southern": "v1"
    }


def test_live_final_two_questions_are_double_points():
    assert not live.is_double_points(0, 10)
    assert not live.is_double_points(7, 10)
    assert live.is_double_points(8, 10)
    assert live.is_double_points(9, 10)
    assert not live.is_double_points(2, 3)                  # tiny 3-question games skip double points


def test_contextual_music_cues_match_moods_sections_and_favorites():
    sample = [
        {"id": "1", "title": "The Final Countdown", "artist": "Europe", "preview_url": "https://a/1.m4a", "artwork_url": None, "track_url": None},
        {"id": "2", "title": "Jump", "artist": "Van Halen", "preview_url": "https://a/2.m4a", "artwork_url": None, "track_url": None},
        {"id": "3", "title": "Money for Nothing", "artist": "Dire Straits", "preview_url": "https://a/3.m4a", "artwork_url": None, "track_url": None},
        {"id": "4", "title": "On the Road Again", "artist": "Willie Nelson", "preview_url": "https://a/4.m4a", "artwork_url": None, "track_url": None},
        {"id": "5", "title": "We Are the Champions", "artist": "Queen", "preview_url": "https://a/5.m4a", "artwork_url": None, "track_url": None},
    ]
    sections = [
        {"id": "sec-fin", "heading": "2. Q3 Financial Performance & Margin"},
        {"id": "sec-fleet", "heading": "4. Fleet & Supply Chain Logistics"},
    ]
    fav = [{"title": "Your Fav", "artist": "Prince", "previewUrl": "https://a/fav.m4a", "artworkUrl": None, "trackUrl": None, "because": "Prince"}]
    cues = music_service.build_cues(sample, sections, favorite_tracks=fav, seed="run-42")
    assert cues["tension"]["title"] == "The Final Countdown"
    assert cues["victory"]["title"] == "Your Fav"           # prefers player's favorite artist for victory
    assert cues["bySection"]["sec-fin"]["title"] == "Money for Nothing"
    assert "Matched to:" in cues["bySection"]["sec-fin"]["cueReason"]
    assert "because" not in cues["bySection"]["sec-fin"]
    assert cues["bySection"]["sec-fleet"]["title"] == "On the Road Again"


def test_confidential_quiz_controls_validate_urls_and_disable_elevenlabs():
    cleaned = settings_store.clean_game({
        **settings_store.DEFAULTS["game"],
        "report_url": "javascript:alert(1)",
        "allow_download": True,
        "unlisted": True,
        "confidential_voice": True,
        "purge_source_after_process": True,
    })
    assert cleaned["report_url"] is None
    assert cleaned["unlisted"] is True and cleaned["confidential_voice"] is True
    assert cleaned["allow_download"] is True and cleaned["purge_source_after_process"] is True

    valid = settings_store.clean_game({**settings_store.DEFAULTS["game"], "report_url": "https://company.sharepoint.com/q3"})
    assert valid["report_url"] == "https://company.sharepoint.com/q3"
    assert host_service.resolve_voice("eleven:southern", allow_eleven=False) == {"engine": "realtime", "voice": "marin"}


# --- the playlist behind the results ---------------------------------------------------------------------

def track(number, artist):
    return {"id": f"t{number:02d}", "title": f"Song {number}", "artist": artist, "preview_url": f"https://clips.example/{number}.m4a",
            "artwork_url": None, "track_url": None}


@pytest.fixture
def catalogue(monkeypatch):
    tracks = [track(n, f"Artist {n % 7}") for n in range(1, 21)]          # 20 songs by 7 artists

    async def fake_tracks(decade):
        return tracks if decade == "80s" else []

    monkeypatch.setattr(music_service, "tracks_for", fake_tracks)
    return tracks


async def test_playlist_opens_with_the_songs_from_the_game(catalogue):
    playlist = await music_service.after_party("80s", ["t05", "t11"])
    assert [song["title"] for song in playlist[:2]] == ["Song 5", "Song 11"]


async def test_playlist_has_one_song_per_artist(catalogue):
    playlist = await music_service.after_party("80s", count=10)
    artists = [song["artist"] for song in playlist]
    assert len(artists) == len(set(artists)) == 7


async def test_playlist_is_the_same_every_time_for_the_same_game(catalogue):
    first = await music_service.after_party("80s", seed="run-1")
    again = await music_service.after_party("80s", seed="run-1")
    other = await music_service.after_party("80s", seed="run-2")
    assert first == again
    assert first != other


async def test_no_music_for_an_empty_decade(catalogue):
    assert await music_service.after_party("50s") == []


# --- dead air is edited out of the host's lines; his voice is not touched ------------------------------------

RATE = host_service.SAMPLE_RATE


def tone(seconds, level=0.3):
    """A stretch of talking, as far as the editor can tell: a steady 200 Hz tone."""
    t = np.arange(int(RATE * seconds)) / RATE
    return (np.sin(2 * np.pi * 200 * t) * level * 32767).astype(np.int16).tobytes()


def hush(seconds):
    return np.zeros(int(RATE * seconds), dtype=np.int16).tobytes()


def tighten(audio, cap, piece=4800):
    edit = host_service.TightEdit(cap)
    out = b"".join(edit.feed(audio[i:i + piece]) for i in range(0, len(audio), piece))
    return out + edit.finish()


def seconds_of(audio):
    return len(audio) / 2 / RATE


def test_a_long_pause_is_shortened_and_the_silence_after_the_line_is_dropped():
    line = tone(1.0) + hush(0.9) + tone(1.0) + hush(0.6)
    out = tighten(line, 0.38)
    assert abs(seconds_of(out) - (1.0 + 0.38 + 1.0 + 0.06)) < 0.03      # talk, capped pause, talk, a short release
    assert out.startswith(tone(1.0))                                     # the talking itself is passed through untouched
    assert tone(1.0) in out[len(tone(1.0)):]


def test_a_natural_gap_between_words_is_left_alone():
    line = tone(0.5) + hush(0.2) + tone(0.5)
    assert tighten(line, 0.38) == line


def test_the_relaxed_pace_keeps_every_pause_but_still_ends_when_he_stops():
    line = tone(1.0) + hush(0.9) + tone(1.0) + hush(0.6)
    out = tighten(line, None)
    assert abs(seconds_of(out) - (1.0 + 0.9 + 1.0 + 0.06)) < 0.03


def test_the_edit_is_the_same_however_the_audio_arrives():
    line = hush(0.3) + tone(0.7) + hush(0.8) + tone(0.4) + hush(0.5)
    assert tighten(line, 0.38, piece=4800) == tighten(line, 0.38, piece=777) == tighten(line, 0.38, piece=len(line))


def test_a_line_with_no_sound_in_it_sends_nothing():
    assert tighten(hush(1.0), 0.38) == b""


def test_each_pace_has_a_speed_and_a_pause_setting():
    assert set(host_service.PACE_SPEED) == set(host_service.PAUSE_CAP_SEC) == {"relaxed", "snappy", "rapid"}
    assert host_service.PAUSE_CAP_SEC["relaxed"] is None
    assert host_service.PAUSE_CAP_SEC["rapid"] < host_service.PAUSE_CAP_SEC["snappy"]


# --- the soundtrack behind the questions ------------------------------------------------------------------

async def test_the_soundtrack_is_fixed_for_a_game_and_differs_between_games(catalogue):
    first = await music_service.soundtrack("80s", "run-1")
    assert first == await music_service.soundtrack("80s", "run-1")      # the browser and the game must agree on it
    assert first != await music_service.soundtrack("80s", "run-2")
    artists = [song["artist"] for song in first]
    assert len(artists) == len(set(artists)) == 7                        # one song per artist


async def test_a_song_already_asked_about_is_left_out_of_the_soundtrack(catalogue):
    rows = await music_service.soundtrack_rows("80s", "run-1")
    asked = rows[0]
    songs = await music_service.soundtrack("80s", "run-1", {asked["id"]})
    assert asked["preview_url"] not in [song["previewUrl"] for song in songs]
    assert len(songs) == len(rows) - 1


async def test_the_soundtrack_is_not_the_results_playlist_in_the_same_order(catalogue):
    behind = [song["previewUrl"] for song in await music_service.soundtrack("80s", "run-1")]
    after = [song["previewUrl"] for song in await music_service.after_party("80s", seed="run-1")]
    assert behind != after


def test_a_game_without_music_breaks_still_gets_one_decade_for_all_its_music():
    rules = {"decades": ["70s", "80s", "90s"]}
    speed_run = {"id": "run-7", "decade": None}
    assert music_service.game_decade(speed_run, rules) == music_service.game_decade(speed_run, rules)
    assert music_service.game_decade(speed_run, rules) in rules["decades"]
    assert music_service.game_decade({"id": "run-8", "decade": "60s"}, rules) == "60s"
    assert music_service.game_decade(speed_run, {"decades": []}) is None


# --- a browser failing to play a clip proves nothing about the clip --------------------------------------

class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def fake_client(result):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            if isinstance(result, Exception):
                raise result
            return FakeResponse(result)

    return Client


@pytest.mark.parametrize("status, dead", [(200, False), (206, False), (404, True), (410, True), (403, True), (500, False)])
async def test_a_clip_is_only_retired_when_the_music_host_says_it_is_gone(monkeypatch, status, dead):
    monkeypatch.setattr(music_service.httpx, "AsyncClient", fake_client(status))
    assert await music_service.preview_is_dead("https://audio-ssl.itunes.apple.com/itunes-assets/1.m4a") is dead


async def test_a_clip_keeps_the_benefit_of_the_doubt_when_we_cannot_tell(monkeypatch):
    monkeypatch.setattr(music_service.httpx, "AsyncClient", fake_client(music_service.httpx.ConnectError("offline")))
    assert await music_service.preview_is_dead("https://audio-ssl.itunes.apple.com/itunes-assets/1.m4a") is False


async def test_preview_probe_refuses_non_apple_hosts(monkeypatch):
    called = False

    class GuardClient:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(music_service.httpx, "AsyncClient", GuardClient)
    assert await music_service.preview_is_dead("https://clips.example/1.m4a") is False
    assert await music_service.preview_is_dead("http://169.254.169.254/latest/meta-data/") is False
    assert called is False


# --- live games: how long the phones stay locked while the host reads ------------------------------------

def test_lead_in_grows_with_the_question_but_never_runs_away():
    short = live._lead_in({"kind": "mcq", "text": "What was revenue?"})
    long = live._lead_in({"kind": "mcq", "text": " ".join(["word"] * 30)})
    endless = live._lead_in({"kind": "mcq", "text": " ".join(["word"] * 400)})
    assert short < long <= endless == live.MAX_LEAD_IN_SEC
    assert live._lead_in({"kind": "music", "text": "Who performed this track?"}) <= 12
    assert live.OPEN_BEAT_SEC >= 0.5                       # long enough for every phone to hear that it opened


def test_the_organisers_music_levels_stay_within_their_ranges():
    from services import settings_store
    as_tuned = settings_store.clean_game({})["mix"]
    assert as_tuned == {"opening": 0, "opening_sec": 6, "under_host": 0, "questions": 0, "between": 0, "ease_down": True}
    mix = settings_store.clean_game({"mix": {"opening": 9, "opening_sec": 30, "under_host": 3, "questions": -9,
                                             "between": "loud", "ease_down": False}})["mix"]
    # Under the host one step up at most (he must stay easy to follow); a question can be silent (-3).
    assert mix == {"opening": 2, "opening_sec": 10, "under_host": 1, "questions": -3, "between": 0, "ease_down": False}
    assert settings_store.clean_game({"mix": "nonsense"})["mix"] == as_tuned


def test_host_persona_and_angles_follow_quiz_topic():
    title = "Game-Day Staff Guide: Empower Field & Denver Football"
    persona = host_service.build_persona(
        name="June", game_title="Read It or Weep", company="Acme", sarcasm=6, quiz_title=title,
    )
    assert title in persona
    assert "a quiz about a business report" not in persona
    assert "jammed printer" not in persona

    angles = host_service._opening_angles(
        {"decade": "90s", "mode": "show"},
        {"team": None},
        {"title": title, "meeting_at": None},
        returning=True,
    )
    joined = "\n".join(angles).lower()
    assert title.lower() in joined
    assert "business report" not in joined
    assert "office life" not in joined


def test_decade_labels_and_tts_pronunciation_expand_00s_10s_20s():
    assert host_service.decade_label("00s") == "2000s"
    assert host_service.decade_label("10s") == "2010s"
    assert host_service.decade_label("20s") == "2020s"
    assert host_service.decade_label("80s") == "1980s"

    # Opening angles use full decades so the writer never sees bare "00s"
    angles = host_service._opening_angles(
        {"decade": "00s", "mode": "show"},
        {"team": None},
        {"title": "Q3 Report", "meeting_at": None},
        returning=False,
    )
    assert any("2000s" in a for a in angles)
    assert not any("the 00s" in a for a in angles)

    # speech_text turns 00's / 00s / 2000s / 10's / 2010s / 20's / 2020s into spoken words while preserving tags
    assert host_service.speech_text("[excited] Hits from the 00's!") == "[excited] Hits from the two-thousands!"
    assert host_service.speech_text("[warm] Pure '00s gold, y'all.") == "[warm] Pure two-thousands gold, y'all."
    assert host_service.speech_text("Back to the 2000s and 2010's!") == "Back to the two-thousands and twenty-tens!"
    assert host_service.speech_text("Spinning the 10s and the 20s!") == "Spinning the twenty-tens and the twenty-twenties!"
    assert host_service.speech_text("Best of the 2020s!") == "Best of the twenty-twenties!"
    assert host_service.speech_text("Pure 80's and 1990's hits!") == "Pure 80s and 1990s hits!"

    # Non-decade numbers, durations, and ages stay untouched
    assert host_service.speech_text("Finished in 10s flat.") == "Finished in 10s flat."
    assert host_service.speech_text("Still in her early 20s and paid in $100s.") == "Still in her early 20s and paid in $100s."
    assert host_service.speech_text("The Roaring 20s jazz era.") == "The Roaring 20s jazz era."

    # On-screen captions normalize ugly "00's" to "2000s"
    assert host_service.spoken_text("[excited] Hits from the 00's!") == "Hits from the 2000s!"


def test_music_blocklist_and_governance_rules():
    policy = {
        "blocked_artists": ["Kanye West", "Sia"],
        "blocked_titles": ["Pumped Up Kicks", "WAP"],
    }
    assert music_service.is_blocked("Kanye West", "Stronger", policy)
    assert music_service.is_blocked("Kanye West feat. Daft Punk", "Stronger", policy)
    assert music_service.is_blocked("Sia", "Chandelier", policy)
    assert not music_service.is_blocked("Asia", "Heat of the Moment", policy)       # short artist name must not substring-match
    assert music_service.is_blocked("Foster the People", "Pumped Up Kicks (Remastered)", policy)
    assert music_service.is_blocked("Cardi B", "WAP", policy)
    assert not music_service.is_blocked("Taylor Swift", "Swap Meet", policy)       # word-boundary title check
    assert not music_service.is_blocked("Queen", "Don't Stop Me Now", policy)

    # settings_store cleans corporate soundtrack controls and validates pinned UUIDs
    valid_id = "11111111-2222-3333-4444-555555555555"
    cleaned = settings_store.clean_game({
        **settings_store.DEFAULTS["game"],
        "allow_player_favorites": False,
        "approved_tracks_only": True,
        "pinned_track_ids": [valid_id.upper(), "not-a-uuid", valid_id],
    })
    assert cleaned["allow_player_favorites"] is False
    assert cleaned["approved_tracks_only"] is True
    assert cleaned["pinned_track_ids"] == [valid_id]

    cleaned_policy = settings_store._clean_music({
        "blocked_artists": ["  Kanye West ", "kanye west", ""],
        "blocked_titles": ["Blurred Lines", "blurred lines"],
    })
    assert cleaned_policy == {"blocked_artists": ["Kanye West"], "blocked_titles": ["Blurred Lines"]}


async def test_approved_only_and_pinned_soundtrack_never_leak_unapproved_tracks(monkeypatch):
    t1 = {"id": "11111111-1111-1111-1111-111111111111", "decade": "80s", "artist": "Queen", "title": "Radio Ga Ga",
          "preview_url": "https://a/1.m4a", "artwork_url": None, "track_url": None, "active": True, "approved": True}
    t2 = {"id": "22222222-2222-2222-2222-222222222222", "decade": "80s", "artist": "Madonna", "title": "Like a Virgin",
          "preview_url": "https://a/2.m4a", "artwork_url": None, "track_url": None, "active": True, "approved": False}

    async def fake_raw(decade):
        return [t1, t2] if decade == "80s" else []

    async def fake_policy():
        return {"blocked_artists": [], "blocked_titles": []}

    monkeypatch.setattr(music_service, "_raw_tracks_for", fake_raw)
    monkeypatch.setattr(music_service, "music_policy", fake_policy)

    rows = await music_service.soundtrack_rows("80s", "run-corp", approved_only=True)
    assert [r["id"] for r in rows] == [t1["id"]]

    # When no approved tracks exist in a decade, strict mode returns None without pulling an unvetted starter track
    q = await music_service.build_question("90s", set(), approved_only=True)
    assert q is None


