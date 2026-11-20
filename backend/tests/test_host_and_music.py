"""The host's hint guard, the results-screen playlist, live-game lead-ins and the music safety net."""
import pytest

from services import host_service, live, music_service


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
    assert await music_service.preview_is_dead("https://clips.example/1.m4a") is dead


async def test_a_clip_keeps_the_benefit_of_the_doubt_when_we_cannot_tell(monkeypatch):
    monkeypatch.setattr(music_service.httpx, "AsyncClient", fake_client(music_service.httpx.ConnectError("offline")))
    assert await music_service.preview_is_dead("https://clips.example/1.m4a") is False


# --- live games: how long the phones stay locked while the host reads ------------------------------------

def test_lead_in_grows_with_the_question_but_never_runs_away():
    short = live._lead_in({"kind": "mcq", "text": "What was revenue?"})
    long = live._lead_in({"kind": "mcq", "text": " ".join(["word"] * 30)})
    endless = live._lead_in({"kind": "mcq", "text": " ".join(["word"] * 400)})
    assert short < long <= endless == live.MAX_LEAD_IN_SEC
    assert live._lead_in({"kind": "music", "text": "Who performed this track?"}) <= 12
    assert live.OPEN_BEAT_SEC >= 0.5                       # long enough for every phone to hear that it opened
