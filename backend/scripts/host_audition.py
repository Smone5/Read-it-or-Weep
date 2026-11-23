"""Record audition clips for the host and put them on one page, to choose a voice by ear.

    python -m scripts.host_audition                       # Max: three manners, five voices
    python -m scripts.host_audition --host roxy           # the second character's line-up
    python -m scripts.host_audition --host eleven --manners wiseguy   # the ElevenLabs host voices
    python -m scripts.host_audition --name Sam --voices verse,echo --manners wiseguy,showman

Every clip is the host playing the same four moments of a game - opening the show, a wrong
answer, a right answer, a new round - through the game's own code (host_service.HostVoice
and build_persona), so a clip is exactly what a game would sound like with that setting.

Why this exists: nothing but a person's ear can judge a voice. Measurements say how fast a
host talks and how long he pauses, and a model that listens was tried as a judge and failed
its sanity test (it preferred whichever recording it heard first). So the honest way to
choose is to line the candidates up and listen. The page is one self-contained file:

    samples/out/host-audition.html      (ignored by git; open it in any browser)

Needs the AI service to be configured (backend/.env) and, for small files, ffmpeg.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import html
import io
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import config  # noqa: E402
from services import eleven, host_service, settings_store  # noqa: E402

RATE = host_service.SAMPLE_RATE
OUT = Path(__file__).resolve().parents[2] / "samples" / "out"

# The same four moments for everyone, worded exactly as the game words them.
MOMENTS = [
    ("Opening the show",
     "Open the show. The contestant is Sam, and this is their first game. Welcome them in ten words or fewer, then announce "
     "the first round by its name - \"Opening Round!\" - and stop. Under 16 words in total. Do not explain the rules."),
    ("A wrong answer",
     "Question: \"What was quarterly revenue?\" Correct answer: \"184 million dollars\". This is their first answer of the "
     "game. Sam answered \"148 million dollars\", which is WRONG. React: verdict, one quick joke about that specific wrong "
     "answer, then the right answer."),
    ("A right answer",
     "Question: \"Which city hosts the new cross-dock?\" Correct answer: \"Phoenix\". Sam got it RIGHT. They answered almost "
     "instantly. React: verdict, then one quick joke that plays on the answer itself."),
    ("A new round",
     "Announce the next round. Its name first, as a headline - \"Lightning Round!\" - then ONE quick joke of eight words or "
     "fewer about it. (What the round is, for your information only - do not recite it: rapid-fire statements; call each "
     "one Fact or Fake.) Do not say the contestant's name. The game is waiting for you: no more than 12 words."),
]

LINE_UPS = {
    "max": {"name": "Max", "style": "", "voices": ["cedar", "verse", "ballad", "alloy", "echo"]},
    "roxy": {"name": "Roxy", "style": settings_store.DEFAULTS["host"]["hosts"][1]["style"],
             "voices": ["coral", "marin", "shimmer", "sage"]},
    # The ElevenLabs host voices (needs ELEVENLABS_API_KEY; without it each is played by its stand-in).
    "eleven": {"name": "Max", "style": "", "voices": ["eleven:classic", "eleven:british", "eleven:southern", "eleven:hype"]},
}


def voice_name(voice: str) -> str:
    return eleven.BY_ID[voice].label if eleven.is_eleven(voice) else voice.capitalize()


async def record(*, name: str, voice: str, manner: str, style: str, quality: str, pace: str) -> dict:
    """One candidate plays the four moments. Returns the words, the audio and where each line starts."""
    persona = host_service.build_persona(name=name, game_title="Read It or Weep", company="Meridian", sarcasm=6,
                                         quiz_title="Q3 Business Review", style=style or None, manner=manner)
    host = {"voice_quality": quality, "pace": pace, "manner": manner, **host_service.resolve_voice(voice)}
    if host["engine"] == "eleven":           # written with delivery tags, performed by ElevenLabs
        persona = host_service.build_persona(name=name, game_title="Read It or Weep", company="Meridian", sarcasm=6,
                                             quiz_title="Q3 Business Review", style=style or None, manner=manner, tags=True)
    lines: list[tuple[str, np.ndarray]] = []
    async with host_service.open_voice(persona, host, True) as session:
        for _, scene in MOMENTS:
            pcm, words = bytearray(), ""
            async for kind, data in session.say(scene):
                if kind == "audio":
                    pcm.extend(data)  # type: ignore[arg-type]
                else:
                    words += str(data)
            lines.append((" ".join(words.split()), np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) / 32768))
    gap = np.zeros(int(RATE * 0.7), dtype=np.float32)
    starts, at = [], 0.0
    for _, audio in lines:
        starts.append(at)
        at += (len(audio) + len(gap)) / RATE
    return {"voice": voice, "manner": manner, "lines": [text for text, _ in lines], "starts": starts,
            "audio": np.concatenate([part for _, audio in lines for part in (audio, gap)]), "pace": talking_rate(lines)}


def talking_rate(lines: list[tuple[str, np.ndarray]]) -> float:
    """Words per second while actually talking: pauses of a fifth of a second or more do not count."""
    frame, hop = int(RATE * 0.03), int(RATE * 0.01)
    words, talking = 0, 0.0
    for text, audio in lines:
        words += len(re.findall(r"[A-Za-z0-9']+", text))
        if len(audio) < frame:
            continue
        rms = np.array([np.sqrt((audio[i:i + frame] ** 2).mean()) for i in range(0, len(audio) - frame, hop)])
        loud = np.where(rms > max(0.008, 0.035 * rms.max()))[0]
        if not len(loud):
            continue
        inner = rms[loud[0]:loud[-1] + 1] > max(0.008, 0.035 * rms.max())
        run, paused = 0, 0.0
        for is_loud in inner:
            if is_loud:
                paused += run * 0.01 if run * 0.01 >= 0.2 else 0
                run = 0
            else:
                run += 1
        talking += len(inner) * 0.01 - paused
    return round(words / talking, 1) if talking else 0.0


def clip_url(audio: np.ndarray) -> str:
    """The clip as a data: address, every clip at the same loudness so a louder one does not win by being louder."""
    frame = int(RATE * 0.05)
    rms = np.array([np.sqrt((audio[i:i + frame] ** 2).mean()) for i in range(0, len(audio) - frame, frame)])
    voiced = rms[rms > 0.02]
    audio = audio * (0.11 / float(np.sqrt((voiced ** 2).mean())) if len(voiced) else 1.0)
    audio = audio * min(1.0, 0.97 / (float(np.abs(audio).max()) or 1.0))
    raw = io.BytesIO()
    with wave.open(raw, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    if shutil.which("ffmpeg"):
        done = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0", "-ac", "1", "-codec:a",
                               "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1"], input=raw.getvalue(), capture_output=True)
        if done.returncode == 0 and done.stdout:
            return "data:audio/mpeg;base64," + base64.b64encode(done.stdout).decode()
    return "data:audio/wav;base64," + base64.b64encode(raw.getvalue()).decode()     # no ffmpeg: a larger page, same sound


def card(code: str, title: str, note: str, take: dict) -> str:
    register = (eleven.BY_ID[take["voice"]].blurb.rstrip(".") if eleven.is_eleven(take["voice"]) else
                {"deeper": "deeper voice", "lower-mid": "lower voice", "middle": "mid-range voice",
                 "higher": "higher voice"}[settings_store.voice_register(take["voice"])])
    lines = "".join(f'<li data-at="{start:.2f}"><span class="moment">{html.escape(moment)}</span>{html.escape(text)}</li>'
                    for (moment, _), text, start in zip(MOMENTS, take["lines"], take["starts"]))
    return f'''
      <article class="card" data-clip="{code}">
        <button class="play" type="button" aria-label="Play {code}: {html.escape(title)}"><span class="icon"></span></button>
        <div class="body">
          <h3><span class="code">{code}</span>{html.escape(title)}</h3>
          <p class="facts">{html.escape(take["voice"])} · {register} · about {take["pace"]:.1f} words a second</p>
          {f'<p class="note">{html.escape(note)}</p>' if note else ''}
          <div class="bar"><div class="fill"></div></div>
          <ol class="lines">{lines}</ol>
        </div>
      </article>'''


PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Host auditions - Read It or Weep</title>
<style>
  :root { --bg:#0d0b1e; --panel:#17143a; --line:#2e2a66; --ink:#f2f0ff; --dim:#a9a4d6; --hot:#ffb020; --go:#6d5ef8; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  main { max-width:760px; margin:0 auto; padding:28px 16px 64px; }
  h1 { font-size:28px; line-height:1.15; margin:0 0 8px; }
  h1 small { display:block; font-size:13px; font-weight:600; letter-spacing:.14em; text-transform:uppercase; color:var(--hot); margin-bottom:6px; }
  .lead { color:var(--dim); margin:0 0 6px; }
  section { margin-top:34px; }
  h2 { font-size:19px; margin:0 0 4px; }
  .blurb { color:var(--dim); margin:0 0 12px; font-size:15px; }
  .card { display:flex; gap:14px; background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px; margin-top:10px; }
  .card.on { border-color:var(--hot); }
  .play { flex:none; width:56px; height:56px; border-radius:50%; border:0; background:var(--go); cursor:pointer; display:grid; place-items:center; }
  .play:focus-visible { outline:3px solid var(--hot); outline-offset:3px; }
  .card.on .play { background:var(--hot); }
  .icon { width:0; height:0; border-style:solid; border-width:11px 0 11px 18px; border-color:transparent transparent transparent #fff; margin-left:4px; }
  .card.on .icon { width:18px; height:20px; border:0; margin:0; background:linear-gradient(90deg,#1a1300 0 6px,transparent 6px 12px,#1a1300 12px 18px); }
  .body { min-width:0; flex:1; }
  h3 { margin:0; font-size:17px; }
  .code { display:inline-block; min-width:34px; text-align:center; font-size:13px; font-weight:700; color:#1a1300; background:var(--hot); border-radius:6px; padding:1px 6px; margin-right:8px; vertical-align:2px; }
  .facts { margin:2px 0 0; font-size:13px; color:var(--dim); }
  .note { margin:4px 0 0; font-size:14px; }
  .bar { height:6px; background:#0b0920; border-radius:3px; margin:10px 0 8px; overflow:hidden; cursor:pointer; }
  .fill { height:100%; width:0; background:var(--hot); }
  .lines { list-style:none; margin:0; padding:0; font-size:14px; color:var(--dim); }
  .lines li { padding:3px 0 3px 10px; border-left:2px solid transparent; }
  .lines li.now { color:var(--ink); border-left-color:var(--hot); }
  .moment { display:block; font-size:11px; letter-spacing:.08em; text-transform:uppercase; opacity:.7; }
</style>
</head>
<body>
<main>
  <h1><small>Read It or Weep</small>Host auditions: __NAME__</h1>
  <p class="lead">Every clip is the host playing the same four moments of a game, improvising the lines the way a real game does. Pick by ear: the codes are there so a choice is easy to name.</p>
  __SECTIONS__
</main>
<script>
const CLIPS = __CLIPS__;
const audio = new Audio();
let current = null;
const reset = (card) => { card.classList.remove('on'); card.querySelector('.fill').style.width = '0'; card.querySelectorAll('li').forEach((li) => li.classList.remove('now')); };
document.querySelectorAll('.card').forEach((card) => {
  card.querySelector('.play').addEventListener('click', () => {
    if (current === card) { if (audio.paused) audio.play(); else audio.pause(); return; }
    if (current) reset(current);
    current = card;
    audio.src = CLIPS[card.dataset.clip];
    audio.play();
  });
  card.querySelector('.bar').addEventListener('click', (event) => {
    if (current !== card || !audio.duration) return;
    const box = event.currentTarget.getBoundingClientRect();
    audio.currentTime = audio.duration * Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
  });
});
audio.addEventListener('play', () => current && current.classList.add('on'));
audio.addEventListener('pause', () => current && current.classList.remove('on'));
audio.addEventListener('ended', () => { if (current) reset(current); current = null; });
audio.addEventListener('timeupdate', () => {
  if (!current || !audio.duration) return;
  current.querySelector('.fill').style.width = (100 * audio.currentTime / audio.duration) + '%';
  let live = null;
  current.querySelectorAll('li').forEach((li) => { if (audio.currentTime >= Number(li.dataset.at) - 0.15) live = li; li.classList.remove('now'); });
  if (live) live.classList.add('now');
});
</script>
</body>
</html>
'''


async def main() -> None:
    parser = argparse.ArgumentParser(description="Record host audition clips and build a page to choose from by ear.")
    parser.add_argument("--host", choices=sorted(LINE_UPS), default="max", help="which character's line-up to record")
    parser.add_argument("--name", help="the character's name (default: the line-up's)")
    parser.add_argument("--style", help="an extra line about this character (default: the line-up's)")
    parser.add_argument("--voices", help="voices to try, comma-separated; the first one also plays every manner")
    parser.add_argument("--manners", default=",".join(settings_store.HOST_MANNERS), help="manners to try on the first voice")
    parser.add_argument("--quality", choices=["standard", "premium"], default="premium")
    parser.add_argument("--pace", choices=sorted(host_service.PACE_SPEED), default="snappy")
    parser.add_argument("--out", default=str(OUT / "host-audition.html"))
    args = parser.parse_args()
    if not config.ai_configured:
        sys.exit("The AI service is not set up (see backend/.env), so there is no host to record.")

    line_up = LINE_UPS[args.host]
    name = args.name or line_up["name"]
    style = line_up["style"] if args.style is None else args.style
    voices = [v.strip() for v in (args.voices.split(",") if args.voices else line_up["voices"]) if v.strip()]
    manners = [m.strip() for m in args.manners.split(",") if m.strip()]
    for voice in voices:
        if voice not in settings_store.VOICES:
            sys.exit(f"Unknown voice '{voice}'. Choose from: {', '.join(settings_store.VOICES)}")
    for manner in manners:
        if manner not in host_service.MANNERS:
            sys.exit(f"Unknown manner '{manner}'. Choose from: {', '.join(host_service.MANNERS)}")

    wanted = [(voices[0], manner) for manner in manners] + [(voice, manners[0]) for voice in voices[1:]]
    gate = asyncio.Semaphore(4)

    async def one(voice: str, manner: str) -> dict | None:
        async with gate:
            try:
                take = await record(name=name, voice=voice, manner=manner, style=style, quality=args.quality, pace=args.pace)
            except Exception as exc:  # noqa: BLE001 - one candidate failing should not lose the others
                print(f"  {voice} / {manner}: could not be recorded ({exc})")
                return None
            print(f"  {voice} / {manner}: {len(take['audio']) / RATE:.0f} s, about {take['pace']:.1f} words a second")
            return take

    print(f"Recording {len(wanted)} clips for {name} ({args.quality} voice model, {args.pace} pace)...")
    takes = await asyncio.gather(*(one(voice, manner) for voice, manner in wanted))
    clips: dict[str, str] = {}
    sections = []
    acted = [(f"A{i + 1}", take) for i, take in enumerate(takes[:len(manners)]) if take]
    if acted:
        cards = []
        for code, take in acted:
            clips[code] = clip_url(take["audio"])
            cards.append(card(code, host_service.MANNERS[take["manner"]]["label"], host_service.MANNERS[take["manner"]]["blurb"], take))
        sections.append(f'<section><h2>Ways to play {html.escape(name)}</h2><p class="blurb">The same voice ({html.escape(voice_name(voices[0]))}), '
                        f'a different performance each time.</p>{"".join(cards)}</section>')
    voiced = [(f"V{i + 1}", take) for i, take in enumerate(takes[len(manners):]) if take]
    if voiced:
        cards = []
        for code, take in voiced:
            clips[code] = clip_url(take["audio"])
            cards.append(card(code, voice_name(take["voice"]), "", take))
        sections.append(f'<section><h2>Other voices for {html.escape(name)}</h2><p class="blurb">All playing the '
                        f'{html.escape(host_service.MANNERS[manners[0]]["label"])}.</p>{"".join(cards)}</section>')
    if not clips:
        sys.exit("Nothing could be recorded.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(PAGE.replace("__NAME__", html.escape(name)).replace("__SECTIONS__", "\n".join(sections))
                   .replace("__CLIPS__", json.dumps(clips)))
    print(f"\n{len(clips)} clips -> {out}  ({out.stat().st_size / 1e6:.1f} MB). Open it in a browser and listen.")


if __name__ == "__main__":
    asyncio.run(main())
