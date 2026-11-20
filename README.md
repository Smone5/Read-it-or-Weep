# Read It or Weep

A retro-arcade trivia game that finds out who actually read the report before the meeting.

Drop in a Word document. The game reads it, writes and checks its own questions (including
questions about the charts and pictures), and quizzes people on it with a wisecracking,
talking game-show host, music breaks from the decade they pick, and a leaderboard. The
organiser sees who is ready for the meeting and which parts of the report did not land.

No two games are the same: each one draws different questions, in a different order, with
different songs.

## What is in it

**For players** (phone, tablet or computer – no app, no password: a link, a name and a work email)

- Four styles of play: **Game Show** (themed rounds, picture round, lightning round, music
  breaks, a final wager), **Sudden Death** (three strikes, getting faster), **Speed Run**
  (beat the clock) and **Straight Quiz** (no jokes, every section, a pass mark).
- A talking host who reacts to every answer. His sarcasm and his manner (a grinning wiseguy, a
  deadpan, or a big showman) are set by the organiser, players can choose between host
  characters, and they can hold a button to talk back to him.
- Lifelines (50/50, a peek at the passage, a hint), streaks, badges, a readiness map of the
  report's sections, and high scores by player and by team.
- Music all the way through: hits from a chosen decade play quietly behind the questions (up in
  the gaps, down under the host), stop for the "name that tune" breaks, and carry on as a
  playlist behind the results. Players can switch the background music off for themselves.
- Pictures from the report can be pinched larger in place or opened full screen.

**For the room** – a live game on the big screen, everyone answering on their own phone, with
the host reading the questions out.

**For the organiser** – an admin area to upload reports, review / edit / add questions, choose
styles of play and their rules, set the host's characters, voices and sarcasm, brand the game,
run live games, and see and export results.

## How it works

```
Word report ─▶ sections, passages, tables, charts, pictures
            ─▶ a writer model drafts questions, each with the sentence that proves it
            ─▶ automatic checks, then a second model answers each question blind
            ─▶ only questions it gets right, for the right reason, go into the bank
Player      ─▶ the server deals each question, keeps the answer key and does all the scoring
Host        ─▶ a realtime voice model told only what has already happened – never an answer
```

Timing is deliberate: the host speaks in the gaps (the opening, the start of a round, after an
answer), and a question's clock starts only when the question is on screen. If anything fails –
the host's connection, a music clip, the network – the game carries on by itself.

## Run it on your computer

You need Docker, Python 3.12+, Node 22+ and an OpenAI API key.

```bash
docker compose up -d db                       # PostgreSQL on localhost:5544

cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                          # then set OPENAI_API_KEY, APP_SECRET, ADMIN_PASSWORD
.venv/bin/python -m uvicorn main:app --port 8100

cd ../web
npm install
npm run dev                                   # http://localhost:5173  (admin: /admin)
```

Sign in at `/admin` with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `backend/.env`, create a quiz
and upload a report. A sample report can be generated with
`backend/.venv/bin/python samples/make_sample_report.py`.

Running costs when testing locally are only the AI calls: a few cents to turn a report into a
question bank, and for the host's voice roughly 17 cents per game on the premium voice model
(the default: it is the one that follows direction about pace and timing) or 5 cents on the
standard one. The choice is on the admin's Host page.

## Let other people try it – no domain, no cloud

```bash
./scripts/share.sh
```

This builds the app, runs it on this computer and opens a secure public link to it with
[ngrok](https://ngrok.com) (free). Anyone with the link can play from their own phone,
microphone included. The computer has to stay on while they play. Before inviting people,
give the quiz an access code (admin ▸ quiz ▸ Overview) so only your testers can start games,
and clear out your own practice games so their results start clean:

```bash
cd backend && .venv/bin/python -m scripts.reset_play_data --all        # shows what would go; add --yes to do it
```

The link keeps its address for as long as it stays open. When you change something, leave it
running and, in another window:

```bash
./scripts/share.sh update
```

This builds your changes and swaps the game behind the same link. Anyone in the middle of a
game carries on after a pause of a few seconds; if the changes do not build, nothing is
swapped. `./scripts/share.sh status` shows the address, and `./scripts/share.sh stop` (or
Ctrl-C in its window) closes the link.

Closing the link and opening it again gives a new address, unless you give it a fixed one.
ngrok gives a free account one fixed address (see <https://dashboard.ngrok.com/domains>); put
it in `backend/.env` as `NGROK_DOMAIN=your-name.ngrok-free.app` and the link is the same every
time.

On your own Wi-Fi you can also start the server with `--host 0.0.0.0` and open
`http://<this-computer's-address>:8100` from a phone, but phones only allow the microphone over
a secure (`https`) link, so use the script for that.

## Run it anywhere

One container holds the API, the voice channel, the live-game stream and the web app. It needs
only a PostgreSQL database, so it runs on Azure, AWS, Google Cloud, or a single small server.

```bash
docker build -t read-it-or-weep .
docker run -p 8100:8100 --env-file backend/.env \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db read-it-or-weep

# or, with a database alongside it:
docker compose --profile app up --build
```

Most container services give you a secure address of their own (`*.run.app`,
`*.azurecontainerapps.io`, `*.awsapprunner.com`, `*.fly.dev`), so a pilot does not need a domain.

| Setting | What it is |
| --- | --- |
| `DATABASE_URL` | Any PostgreSQL 13+ (no extensions needed) |
| `APP_SECRET` | 32+ random characters; signs player and admin sessions |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | The first admin, created at start-up if there is none |
| `OPENAI_API_KEY` (or the `AZURE_OPENAI_*` settings with `LLM_PROVIDER=azure`) | The AI provider |
| `PUBLIC_BASE_URL` | The site's public address, used for share links and QR codes |
| `STORAGE_BACKEND` | Where reports and pictures are kept: `local`, `s3`, `azure` or `gcs` |
| `APPLE_*` | Optional: an Apple developer key fills the song catalogue faster |

Everything else is in [`backend/.env.example`](backend/.env.example). Uploaded files are kept
on local disk by default; use a bucket (`STORAGE_BACKEND`) when running more than one copy.

## How many people can play at once

Measured on one laptop, one server process, with `scripts/load_test.py`:

| Test | Result |
| --- | --- |
| 50 people each playing a whole game at the same moment, about 15× faster than a person can | every game finished; a typical request took 10 ms, the slowest 69 ms |
| 200 people doing the same | every game finished with no errors, but the single process was saturated (about 250 requests a second; typical request a quarter of a second) |
| One live game with 150 phones | answers took 10 ms; everyone joined within about a second |

A real player makes a request every several seconds, not several a second, so one process has
room for roughly a thousand people playing at once. Past that, run more copies – more
containers behind a load balancer, or `WEB_CONCURRENCY=4` inside one – since everything a game
needs to remember is in the database. The talking host is the exception: each player with the
voice on holds one realtime session with the AI provider, so the ceiling there is your account's
rate limit, not this server.

Security in brief: admin pages need an admin sign-in (passwords stored with Argon2, sign-in
attempts limited); every game request is checked against the player who owns the game; answers
never leave the server before a question is answered; pictures are reached through short-lived
signed links; sign-in tokens are never put in addresses; responses carry a content security
policy; and each game is capped in how much AI voice it can use. Players join with a name and
an email and no password, by design – use a quiz access code or an allowed email domain to
keep a quiz to the people it is meant for.

## Tests

```bash
cd backend
.venv/bin/python -m pytest                    # rules, scoring, question choice, safety nets (no database needed)
.venv/bin/python -m scripts.smoke_play        # plays whole games through the API against the real database
.venv/bin/python -m scripts.load_test --players 100   # many people at once, against a running server
cd ../web && npm run build                    # type-check and production build
```

The game's sequencing can be inspected in a browser: add `?trace` to the address and read
`window.__quizTrace` in the console – every host line, question, clip and sound, timestamped.

How the host *sounds* cannot be tested by a program – only by ear. To choose a voice, record
the candidates side by side and listen:

```bash
cd backend
.venv/bin/python -m scripts.host_audition              # Max: three manners, five voices
.venv/bin/python -m scripts.host_audition --host roxy  # the second character
```

It writes `samples/out/host-audition.html`, one page with every clip in it.

## Layout

```
backend/   FastAPI app: routers/ (HTTP + WebSocket), services/ (game engine, host, music, live,
           stats), ingest/ (reading reports, writing and checking questions), migrations/, tests/
web/       React + TypeScript + Tailwind: src/game (solo game), src/live (big screen and phones),
           src/admin (organiser), src/lib (audio, host voice, API)
samples/   a generator for a fictional sample report
scripts/   share.sh – a public test link from your own computer
```

## Status

Working and tested: the four styles of play, the question pipeline on Word documents, the host
(voice, sarcasm, characters), music breaks, live games, the admin area, recovery from dropped
connections, the container build.

Written but not yet tested against the real services: the S3 / Azure Blob / Google Cloud
Storage adapters, Azure OpenAI, and reading PDF / PowerPoint / plain-text files.

## Credits

Music breaks use Apple's 30-second preview clips. Fonts: Press Start 2P and Share Tech Mono
(SIL Open Font License). Sound effects: see [`web/public/sfx/CREDITS.md`](web/public/sfx/CREDITS.md).
