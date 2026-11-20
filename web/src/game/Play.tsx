// The game loop: show an item, take one answer, reveal, move on.
//
// The server decides everything that matters (what is asked, what is correct, the score).
// This screen never holds a correct answer until the server has accepted the player's own.
//
// Who has the stage, and when
// ---------------------------
// Only one thing happens at a time, in this order:
//
//   title card   the host opens the show or introduces a round. The question is not on screen and
//                its clock has not started; the card stays until he has finished (or the player
//                skips, or he takes too long).
//   question     the clock runs and the host keeps quiet, unless the player asks him for a hint or
//                talks to him. A music clip starts here, and its clock starts when it is heard.
//   reveal       the result, then the host's reaction. "Next" is there from the start: tapping it
//                while he is talking cuts him off cleanly rather than letting him run on.
//
// Behind all of it the jukebox plays hits from the game's decade – a quiet bed under the questions
// and between them, swelling for the big moments (the opening, a round's title card, a streak), down
// under the host – and goes silent for a "name that tune" clip, which has the stage to itself.
//
// To make that possible the server deals the first item of a round *sealed*: we learn which round
// is coming, show its card, and only then ask for the question to be opened.

import { useCallback, useEffect, useRef, useState } from 'react'
import { MusicToggle } from '../components/NowPlaying'
import { ErrorNote, Modal, SoundToggle } from '../components/ui'
import { api, ApiError, onConnectionTrouble } from '../lib/api'
import type { HostVoice } from '../lib/hostVoice'
import { onJukeboxChange, setJukeboxLevel } from '../lib/jukebox'
import { fadeOutMusic, preloadClip, stopMusic } from '../lib/music'
import { blip, buzz, play } from '../lib/sound'
import { trace } from '../lib/trace'
import type { Item, ModeId, Reveal, RunState, Summary, Turn } from '../lib/types'
import { AnswerList, FactFakeButtons, HostBox, Hud, MusicPlayer, PictureView, TimerBar, useDeadline, type TalkControl } from './parts'

type Props = {
  firstTurn: Turn
  /** True when picking a game back up after a reload: skip the welcome. */
  resumed?: boolean
  hostName: string
  hostLine: string
  hostSpeaking: boolean
  host: HostVoice | null
  talk?: TalkControl | null
  onDone: (summary: Summary) => void
}

type Phase = 'card' | 'wager' | 'question' | 'reveal'
type Card = { kicker: string; title: string; blurb: string }

// Every step of a game is safe to repeat (asking again for the next question, or sending the same
// answer twice, changes nothing), so each request is retried by itself across a dropped connection.
const RETRY = 4

const MIN_CARD_MS = 1400 // a title card stays at least long enough to be read
const MAX_INTRO_MS = 11000 // ...and the host never holds the game up longer than this
// The show's opening is the exception: the host introduces the show and the contestant and explains how it
// works, so it may run longer - and it can always be skipped.
const MAX_OPENING_MS = 45000
// The show opens with the music on its own, loud, before the host's first word (his line is written meanwhile).
// The organiser: "have the music play a little bit longer before the host starts." His first word comes this long
// after the tap - and at least OPENING_THEME_MS after the theme's first note, since on a slow phone the first song
// takes a while to arrive - but never later than OPENING_LATEST_MS. The music starts gliding down 1.2 s before he
// speaks (see hostVoice), so it plays on its own, loud, for about six seconds.
const OPENING_MUSIC_MS = 7000
const OPENING_THEME_MS = 7000
const OPENING_LATEST_MS = 9500
const NO_MUSIC_AFTER_MS = 2500 // no song by now (music off, or it will not load): he need not wait for one
const NO_HOST_OPENING_MS = 4000 // with no host to open the show, the theme still gets a few seconds
// A round's title card: the music drops as the player taps Next, the sting plays in that moment of quiet, then the
// music comes in under the title. ("A TV title card goes quiet, sting, theme, then the host over it.")
const STING_GAP_MS = 400
// When the host finishes a title ("...the Deep End!") the music answers it for a beat before the question appears.
const BUTTON_MS = 600
const OPENING_BUTTON_MS = 800
const CLIP_GAP_MS = 400 // before a "name that tune" clip: the music stops dead, a beat of silence, then the mystery song
// After an answer the music comes up while the player decides when to tap Next - never in the first moments, under the
// answer's sting - and eases back down if they stay. ("...go up while waiting, then fade away, but be abrupt when they
// click next.")
const WAIT_NOT_BEFORE_MS = 1200
const LINGER_AFTER_MS = 6000
const HOST_LATE_MS = 3200 // if he has not started speaking by now, the game goes on without the introduction
// The opening allows for him still connecting, and for a long line to write (measured up to 7.4 s through the tester
// link). The music is up during the wait, so it plays as the show's intro rather than dead air.
const HOST_LATE_OPENING_MS = 12000
const CLIP_WAIT_MS = 2500 // a music clip that has not started by now: start the clock anyway
const STREAKS_WORTH_SHOUTING = [3, 5, 7, 10, 15, 20] // right answers in a row that get a fanfare

const ROUND_BLURB: Record<string, string> = {
  'Warm-Up': 'Easy ones first',
  'Music Break': 'Name that tune',
  'Picture Round': 'Charts and pictures',
  'The Deep End': 'Now it gets harder',
  'Lightning Round': 'Fact or fake – be quick',
  'Second Chance': 'The ones you missed · half points',
  'Final Wager': 'Bet your points',
  'Last Question': 'Double points',
  'Speed Run': 'Beat the clock',
  'The Quiz': 'Take your time',
}

const MODE_BLURB: Record<ModeId, string> = {
  show: 'Rounds, music and a final wager',
  sudden_death: 'Three strikes and you are out',
  speed: 'Beat the clock',
  straight: 'Take your time',
}

function openingCard(state: RunState, first?: Item): Card {
  const round = first && state.mode === 'show' ? `First up: ${first.round.name}` : ''
  return { kicker: 'Get ready', title: state.modeName, blurb: round || MODE_BLURB[state.mode] }
}

function roundCard(next: Item, mode: ModeId): Card {
  if (next.slot === 'music' && mode !== 'show') return { kicker: 'Bonus', title: 'Music Break', blurb: 'Name that tune · win back a life' }
  const kicker = mode === 'show' ? `Round ${next.round.number}` : 'Get ready'
  return { kicker, title: next.round.name, blurb: ROUND_BLURB[next.round.name] ?? '' }
}

const LIFELINES: { kind: 'fifty' | 'peek' | 'hint'; label: string; help: string }[] = [
  { kind: 'fifty', label: '50/50', help: 'Remove two wrong answers' },
  { kind: 'peek', label: 'Peek', help: 'See the passage (half points)' },
  { kind: 'hint', label: 'Hint', help: 'Ask the host' },
]

const FLAG_REASONS = ['The marked answer is wrong', 'More than one answer fits', 'The question is unclear', 'Something else']

export default function Play({ firstTurn, resumed, hostName, hostLine, hostSpeaking, host, talk, onDone }: Props) {
  const [state, setState] = useState<RunState>(firstTurn.state)
  const [item, setItem] = useState<Item | null>(null)
  const [phase, setPhase] = useState<Phase>('card')
  const [card, setCard] = useState<Card>(() => openingCard(firstTurn.state))
  const [canSkip, setCanSkip] = useState(false)
  const [reveal, setReveal] = useState<Reveal | null>(null)
  const [picked, setPicked] = useState<number | null>(null)
  const [busy, setBusy] = useState(false) // a request about the question on screen is in flight
  const [moving, setMoving] = useState(false) // on the way to the next item
  const [clipStarted, setClipStarted] = useState(false)
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null) // for the full-screen picture view
  const [stranded, setStranded] = useState(false) // the game is over but its results would not load
  const [error, setError] = useState('')
  const [reconnecting, setReconnecting] = useState(false)
  const [quitting, setQuitting] = useState(false)
  const [flagging, setFlagging] = useState(false)
  const [flagged, setFlagged] = useState(false)
  const [banner, setBanner] = useState('')
  const roundRef = useRef('')
  const doneRef = useRef(false)
  const aliveRef = useRef(true)
  const startedRef = useRef(false)
  const movingRef = useRef(false)
  const skipRef = useRef<(() => void) | null>(null)
  const welcomeRef = useRef(0) // the welcome line's number (0: no host to open the show)
  const stopOpeningWatch = useRef<() => void>(() => undefined)
  const revealRef = useRef<HTMLElement>(null)

  const runId = state.runId
  const mode = state.mode
  const quick = mode === 'speed' || item?.slot === 'fact_fake'
  // The host reacts to answers in the two "show" styles. In a Speed Run he opens and signs off
  // but stays out of the way of the race; the Straight Quiz has no host at all.
  const hostReacts = mode === 'show' || mode === 'sudden_death'

  useEffect(() => onConnectionTrouble(setReconnecting), [])

  // The soundtrack follows the game. Each change of scene is one move of the music, made on purpose:
  //   opening card   loud, on its own, then down under the host (and back up for a beat after his last word)
  //   round card     quiet for the sting, then up under the title, down under the host, up for a beat after him
  //   question       out of the way ("think"); in the quick-fire rounds a steady bed instead ("gap")
  //   reveal         "gap" under the answer's sting and the host, "wait" (up) once he is done, easing back to
  //                  "linger" if the player stays; down at once when they tap Next
  //   wager screen   a bed: no clock is running, and the bet is the drama
  //   music break    the soundtrack plays under the host's introduction, then stops dead before the clip
  const [bigMoment, setBigMoment] = useState(false)
  const [opening, setOpening] = useState(!resumed) // the show's opening card: the loudest the music gets
  // A round card begins with a moment of quiet for its sting; then the music comes up under the title.
  const [titleUp, setTitleUp] = useState(false)
  const titleTimer = useRef(0)
  // The card is over: 'button' while the music answers the host's last word, 'bed' once it has gone down to whatever
  // comes next (the question's level, or silence before a clip).
  const [handover, setHandover] = useState<'button' | 'bed' | null>(null)
  // After an answer the host reacts; once he is done the player may linger before tapping Next, and the music
  // comes up to fill that wait - then eases off if they stay.
  const [afterHost, setAfterHost] = useState(false)
  const [lingering, setLingering] = useState(false)
  // Next was tapped: the music drops at once, without waiting for the next question to arrive.
  const [leaving, setLeaving] = useState(false)
  useEffect(() => {
    setAfterHost(false)
    setLingering(false)
    setLeaving(false)
    if (phase !== 'reveal') return
    let current = true
    const settle = new Promise((resolve) => window.setTimeout(resolve, WAIT_NOT_BEFORE_MS))
    void Promise.all([host ? host.whenQuiet() : Promise.resolve(), settle]).then(() => {
      if (current) setAfterHost(true)
    })
    return () => {
      current = false
    }
  }, [phase, host])
  useEffect(() => {
    if (!afterHost) return
    const linger = () => setLingering(true)
    const timer = window.setTimeout(linger, LINGER_AFTER_MS)
    const onHide = () => document.hidden && linger() // nobody is looking: no need for it to be up
    document.addEventListener('visibilitychange', onHide)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onHide)
    }
  }, [afterHost])
  useEffect(() => {
    const music = item?.slot === 'music'
    // What is under the question (or the betting screen) this card leads to.
    const bed = music ? 'out' : quick || item?.needsWager ? 'gap' : 'think'
    if (phase === 'card') {
      if (handover === 'bed') setJukeboxLevel(bed)
      else if (opening) setJukeboxLevel('intro')
      else setJukeboxLevel(titleUp ? 'title' : 'think')
    } else if (music) setJukeboxLevel('out')
    else if (phase === 'wager') setJukeboxLevel('gap')
    else if (phase === 'question') setJukeboxLevel(bed)
    else if (leaving) setJukeboxLevel(bed)
    else if (bigMoment && !quick) setJukeboxLevel('swell')
    else if (afterHost && !quick) setJukeboxLevel(lingering ? 'linger' : 'wait')
    else setJukeboxLevel('gap')
  }, [phase, item?.slot, item?.needsWager, quick, bigMoment, opening, titleUp, handover, afterHost, lingering, leaving])

  // On a phone the explanation lands below the answers: bring it into view, and return to the
  // top for each new question.
  useEffect(() => {
    if (phase === 'reveal') revealRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    else window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [phase, item?.id])

  const finish = useCallback(async () => {
    if (doneRef.current) return
    doneRef.current = true
    host?.skip()
    fadeOutMusic()
    trace('finish')
    try {
      let result: { summary: Summary }
      try {
        result = await api<{ summary: Summary }>(`/api/runs/${runId}/summary`, { retry: RETRY })
      } catch (caught) {
        // "Still going" means the server has not closed the game yet (the clock ran out here first): close it.
        if (!(caught instanceof ApiError) || caught.status !== 409) throw caught
        result = await api<{ summary: Summary }>(`/api/runs/${runId}/quit`, { method: 'POST', retry: RETRY })
      }
      onDone(result.summary)
    } catch (caught) {
      // Never a dead end: say so, and let the player ask again.
      doneRef.current = false
      if (aliveRef.current) {
        setError(`Your results could not be loaded (${(caught as Error).message}) Tap to try again.`)
        setStranded(true)
      }
    }
  }, [runId, host, onDone])

  /** Put a question (or the wager screen) in front of the player. Its clock is already running. */
  const show = useCallback((next: Item) => {
    setHandover(null)
    setItem(next)
    setReveal(null)
    setPicked(null)
    setFlagged(false)
    setError('')
    setClipStarted(false)
    setSecondsLeft(null)
    if (next.round.name !== roundRef.current) {
      roundRef.current = next.round.name
      setBanner(next.round.name) // a new level that did not get a title card
      window.setTimeout(() => setBanner(''), 1800)
    }
    setPhase(next.needsWager ? 'wager' : 'question')
    if (next.slot !== 'music') blip('question') // a clip is its own arrival
    trace('show', { slot: next.slot, round: next.round.name, wager: Boolean(next.needsWager) })
  }, [])

  /** Hold a title card for as long as the host needs: at least long enough to read, never too long, always skippable. */
  const hostBeat = useCallback(
    (lateMs: number, capMs: number = MAX_INTRO_MS, floorMs: number = MIN_CARD_MS) =>
      new Promise<string>((resolve) => {
        let over = false
        const end = (why: string) => {
          if (over) return
          over = true
          window.clearTimeout(floor)
          window.clearTimeout(late)
          window.clearTimeout(cap)
          skipRef.current = null
          setCanSkip(false)
          trace('card.end', { why })
          resolve(why)
        }
        const floor = window.setTimeout(() => {
          if (host) void host.whenQuiet().then(() => end('host finished'))
          else end('no host')
        }, floorMs)
        // Dead air on a title card feels broken: a host who is late to start loses his turn.
        const late = window.setTimeout(() => {
          if (!host?.stalled) return
          host.tooLate()
          end('host late')
        }, lateMs)
        const cap = window.setTimeout(() => {
          host?.skip()
          end('too long')
        }, capMs)
        skipRef.current = () => {
          host?.skip()
          end('skipped')
        }
        setCanSkip(true)
      }),
    [host],
  )

  /** A title card: the host introduces what is coming, and only then does the question (and its clock) appear. */
  const introduce = useCallback(
    async (next: Item, opening: boolean) => {
      roundRef.current = next.round.name
      setItem(next)
      setReveal(null)
      setPicked(null)
      setError('')
      setCard(opening ? openingCard(state, next) : roundCard(next, mode))
      setOpening(opening)
      setHandover(null)
      setTitleUp(false)
      window.clearTimeout(titleTimer.current)
      setPhase('card')
      trace('card', { round: next.round.name, slot: next.slot, opening })
      if (next.music) preloadClip(next.music.previewUrl) // so the clip can start the instant the host is done
      if (!opening) {
        play('levelup') // in a moment of quiet: the music comes up under the title just after it
        titleTimer.current = window.setTimeout(() => setTitleUp(true), STING_GAP_MS)
        if (hostReacts) host?.cue('round', next.id)
      }
      const why = await hostBeat(
        opening ? HOST_LATE_OPENING_MS : HOST_LATE_MS,
        opening ? MAX_OPENING_MS : MAX_INTRO_MS,
        opening && !welcomeRef.current ? NO_HOST_OPENING_MS : MIN_CARD_MS,
      )
      if (!aliveRef.current || doneRef.current) return
      if (next.slot === 'music') {
        // The soundtrack stops dead on his last word ("Roll it!"), and the mystery song starts out of the silence.
        setHandover('bed')
        await new Promise((resolve) => window.setTimeout(resolve, CLIP_GAP_MS))
      } else if (why === 'host finished' && mode !== 'speed') {
        // The music answers the title, then gets out of the way as the question appears. (Not when the player
        // skipped the card, or the host never came: then they want the question.)
        setHandover('button')
        trace('card.button')
        await new Promise((resolve) => window.setTimeout(resolve, opening ? OPENING_BUTTON_MS : BUTTON_MS))
        setHandover('bed')
      } else setHandover('bed')
      if (!aliveRef.current || doneRef.current) return
      if (next.needsWager) {
        show(next) // the wager screen; placing the bet is what opens the question
        return
      }
      const opened = await api<{ item: Item; state: RunState }>(`/api/runs/${runId}/open`, { body: { itemId: next.id }, retry: RETRY })
      if (!aliveRef.current || doneRef.current) return
      setState(opened.state)
      show(opened.item)
    },
    [state, mode, hostReacts, host, hostBeat, show, runId],
  )

  /** Deal the next item and put it on stage – behind a title card when it starts a round. */
  const pull = useCallback(
    async (opening = false) => {
      if (movingRef.current || doneRef.current) return
      movingRef.current = true
      setMoving(true)
      setError('')
      try {
        const turn = await api<Turn>(`/api/runs/${runId}/next`, { method: 'POST', retry: RETRY })
        if (!aliveRef.current) return
        setState(turn.state)
        if (turn.finished || !turn.item) await finish()
        else if (turn.item.sealed || turn.item.needsWager) await introduce(turn.item, opening)
        else show(turn.item)
      } catch (caught) {
        if (aliveRef.current) setError((caught as Error).message)
      } finally {
        movingRef.current = false
        setMoving(false)
      }
    },
    [runId, finish, introduce, show],
  )

  // The opening. A new game starts on its title card with the host's welcome; the first question is
  // dealt behind it and opens when he is done. A game picked back up goes straight to where it was.
  useEffect(() => {
    aliveRef.current = true
    if (!startedRef.current) {
      startedRef.current = true
      const waiting = firstTurn.item
      if (firstTurn.finished) void finish()
      else if (waiting && !waiting.sealed) {
        roundRef.current = waiting.round.name
        show(waiting) // mid-question, or at the wager screen
      } else if (waiting) {
        movingRef.current = true
        void introduce(waiting, false)
          .catch((caught) => setError((caught as Error).message))
          .finally(() => {
            movingRef.current = false
          })
      } else {
        if (!resumed) {
          play('levelup')
          const tapped = Date.now()
          const welcome = host?.cue('welcome', undefined, { notBefore: tapped + OPENING_MUSIC_MS }) ?? 0
          welcomeRef.current = welcome
          trace('opening')
          if (welcome && host) {
            // His first word waits for the theme: OPENING_THEME_MS after its first note, if that is later than
            // planned. With no music coming, he need not wait at all.
            let anchored = false
            const anchor = (at: number) => {
              if (anchored) return
              anchored = true
              stopOpeningWatch.current()
              host.holdUntil(at)
            }
            const unsubscribe = onJukeboxChange((jukebox) => {
              if (jukebox.playing) anchor(Math.min(tapped + OPENING_LATEST_MS, Math.max(tapped + OPENING_MUSIC_MS, Date.now() + OPENING_THEME_MS)))
            })
            const noMusic = window.setTimeout(() => anchor(0), NO_MUSIC_AFTER_MS)
            stopOpeningWatch.current = () => {
              unsubscribe()
              window.clearTimeout(noMusic)
            }
          }
        }
        void pull(!resumed)
      }
    }
    return () => {
      // (The opening's watch on the music is not stopped here: in development React runs this cleanup once straight
      // after mounting. It stops itself once the theme starts, or after NO_MUSIC_AFTER_MS at the latest.)
      aliveRef.current = false
      window.clearTimeout(titleTimer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const advance = useCallback(() => {
    if (!reveal || movingRef.current) return
    host?.skip() // the player has moved on, so the host stops – cleanly – rather than talk over what comes next
    fadeOutMusic()
    if (!quick || reveal.finished) setLeaving(true) // the quick-fire rounds keep their bed steady
    trace('advance')
    if (reveal.finished) void finish()
    else void pull()
  }, [reveal, host, finish, pull, quick])

  const submit = useCallback(
    async (choice: number | null) => {
      if (!item || busy || phase !== 'question') return
      setBusy(true)
      setPicked(choice)
      try {
        const result = await api<Reveal>(`/api/runs/${runId}/answer`, { body: { itemId: item.id, choiceIndex: choice }, retry: RETRY })
        // A hint or a reply about this question is out of date once it has been answered, and would hold up his
        // reaction: stop it, so the answer's sting plays at full.
        if (host?.busy) host.skip()
        setReveal(result)
        setState(result.state)
        setPhase('reveal')
        trace('reveal', { correct: result.correct, timedOut: result.timedOut })
        play(result.correct ? 'correct' : 'wrong')
        buzz(result.correct ? 30 : [60, 40, 60])
        if (result.strikeRestored) play('cheer')
        if (result.correct && STREAKS_WORTH_SHOUTING.includes(result.state.streak)) {
          const streak = result.state.streak
          setBanner(`${streak} in a row!`)
          setBigMoment(true) // the music swells with the fanfare
          window.setTimeout(() => setBigMoment(false), 2600)
          window.setTimeout(() => blip(streak >= 5 ? 'bigstreak' : 'streak'), 300) // just after the "correct" sound
          window.setTimeout(() => setBanner((current) => (current === `${streak} in a row!` ? '' : current)), 2400)
        }
        if (hostReacts && item.slot !== 'fact_fake') host?.cue('result', item.id)
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 409) await finish()
        else {
          // The answer did not get through. The buttons are live again, so one more tap sends it.
          setError(`${(caught as Error).message}${choice === null ? ' Tap any answer to carry on.' : ''}`)
          setPicked(null)
        }
      } finally {
        setBusy(false)
      }
    },
    [item, busy, phase, runId, host, finish, hostReacts],
  )

  // Lightning statements and speed runs move on by themselves. (The timer reads `advance` through a
  // ref: the function is rebuilt on every render, and a re-render must not restart the countdown.)
  const advanceRef = useRef(advance)
  advanceRef.current = advance
  useEffect(() => {
    if (phase !== 'reveal' || !reveal || !quick) return
    const timer = window.setTimeout(() => advanceRef.current(), reveal.finished ? 1400 : mode === 'speed' ? 650 : 1150)
    return () => window.clearTimeout(timer)
  }, [phase, reveal, quick, mode])

  // A music question's clock waits for its clip to be heard – but not for ever.
  useEffect(() => {
    if (phase !== 'question' || item?.slot !== 'music' || clipStarted) return
    const timer = window.setTimeout(() => setClipStarted(true), CLIP_WAIT_MS)
    return () => window.clearTimeout(timer)
  }, [phase, item?.id, item?.slot, clipStarted])

  const clockMs = useDeadline(mode === 'speed' ? state.deadlineAt : undefined, () => {
    if (phase === 'question' && !busy) void pull()
  })

  // Keyboard: A-D or 1-4 to answer, Enter / Space to continue or to skip a title card.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
      const go = event.key === 'Enter' || event.key === ' '
      if (phase === 'question' && item?.options) {
        const index = 'abcd'.indexOf(event.key.toLowerCase())
        const number = '1234'.indexOf(event.key)
        const choice = index >= 0 ? index : number
        if (choice >= 0 && choice < item.options.length && !item.removed.includes(choice)) void submit(choice)
      } else if (phase === 'reveal' && !quick && go) {
        event.preventDefault()
        advance()
      } else if (phase === 'card' && go && skipRef.current && !(event.target instanceof HTMLButtonElement)) {
        event.preventDefault()
        skipRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phase, item, submit, advance, quick])

  const useLifeline = async (kind: string) => {
    if (!item || busy) return
    setBusy(true)
    try {
      const result = await api<{ item: Item; state: RunState }>(`/api/runs/${runId}/lifeline`, { body: { itemId: item.id, kind }, retry: 2 })
      setItem(result.item)
      setState(result.state)
      play('hint')
      if (kind === 'hint') host?.cue('hint', item.id) // the one time he speaks during a question: the player asked
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const placeWager = async (amount: number) => {
    if (!item || busy) return
    setBusy(true)
    try {
      const result = await api<{ item: Item; state: RunState }>(`/api/runs/${runId}/wager`, { body: { itemId: item.id, amount }, retry: RETRY })
      play('click')
      host?.skip() // nothing he was saying about the bet should run into the question
      setItem(result.item)
      setPhase('question')
      trace('show', { slot: result.item.slot, round: result.item.round.name, wager: false })
    } catch (caught) {
      setError((caught as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const brokenClip = useRef('') // a clip is reported once, however many ways it fails
  const musicBroken = async () => {
    if (!item || phase !== 'question' || brokenClip.current === item.id) return
    brokenClip.current = item.id
    try {
      const turn = await api<Turn>(`/api/runs/${runId}/media-error`, { body: { itemId: item.id }, retry: 2 })
      setState(turn.state)
      if (turn.finished || !turn.item) {
        await finish()
        return
      }
      // The break has already been introduced: whatever comes instead (another clip, or the next
      // question if music will not play on this device) opens straight away.
      let next = turn.item
      if (next.sealed) next = (await api<{ item: Item }>(`/api/runs/${runId}/open`, { body: { itemId: next.id }, retry: RETRY })).item
      show(next)
    } catch {
      /* keep the question; the player can still guess */
    }
  }

  const quit = async () => {
    doneRef.current = true
    skipRef.current = null
    stopMusic()
    host?.skip()
    try {
      const { summary } = await api<{ summary: Summary }>(`/api/runs/${runId}/quit`, { method: 'POST', retry: RETRY })
      onDone(summary)
    } catch (caught) {
      doneRef.current = false
      setQuitting(false)
      setError((caught as Error).message)
    }
  }

  const flag = async (reason: string) => {
    if (!item) return
    setFlagging(false)
    setFlagged(true)
    await api('/api/questions/flag', { body: { itemId: item.id, reason } }).catch(() => undefined)
  }

  // "Question 3/19": answered so far, plus the one on screen, out of everything still queued.
  const showsCounter = mode === 'show' || mode === 'straight'
  const onScreen = item && !reveal && item.position >= state.answered ? 1 : 0
  const total = Math.max(1, state.answered + onScreen + (state.remaining ?? 0))
  const position = showsCounter ? `${Math.min(state.answered + onScreen, total) || 1}/${total}` : String(state.answered)
  const canUseLifelines = phase === 'question' && item && (item.slot === 'report' || item.slot === 'image') && Object.keys(state.lifelines).length > 0
  // Until a Speed Run's first question opens, its clock stands at the full time.
  const speedClock = mode === 'speed' ? (state.deadlineAt ? clockMs : (state.clockSec ?? 0) * 1000) : undefined
  const last = Boolean(reveal?.finished) || (showsCounter && state.remaining === 0)

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-28px)] w-full max-w-2xl flex-col gap-3">
      <header className="flex items-start gap-3">
        <div className="flex-1">
          <Hud state={state} clockMs={speedClock} position={position} />
        </div>
        <MusicToggle />
        <SoundToggle />
        <button className="btn btn-ghost btn-sm" onClick={() => setQuitting(true)} aria-label="Quit this game">✕</button>
      </header>

      <HostBox name={hostName} line={hostLine} speaking={hostSpeaking} talk={talk} />

      {banner && <p className="pixel neon-accent flicker-in text-center text-xs">{banner}</p>}
      {reconnecting && <p className="pixel blink text-center text-[10px]" style={{ color: 'var(--accent)' }} role="status">Reconnecting…</p>}

      <main className="flex flex-1 flex-col gap-3">
        {phase === 'card' && (
          <div className="grid flex-1 place-items-center py-10 text-center">
            <div className="flicker-in" key={card.title}>
              <p className="pixel blink text-[10px] text-muted">{card.kicker}</p>
              <h1 className="neon mt-4 text-xl sm:text-3xl">{card.title}</h1>
              <p className="mt-4 min-h-7 text-lg text-muted">{card.blurb}</p>
              {error ? (
                <button className="btn btn-primary mt-8" onClick={() => void pull()}>Try again ▶</button>
              ) : (
                <button className="btn btn-ghost btn-sm mt-8" style={{ visibility: canSkip ? 'visible' : 'hidden' }} onClick={() => skipRef.current?.()}>
                  Skip ▶
                </button>
              )}
            </div>
          </div>
        )}

        {phase === 'wager' && item && (
          <div className="card rise flex flex-1 flex-col items-center justify-center gap-5 p-6 text-center">
            <p className="chip chip-accent">Final wager</p>
            <h2 className="neon text-base sm:text-xl">How sure are you?</h2>
            <p className="text-muted">
              One last question{item.section ? <> from <b className="text-ink">{item.section.heading}</b></> : null}. Bet before you see it: right wins the bet, wrong loses it.
            </p>
            <div className="grid w-full max-w-sm gap-3">
              {(item.wagerOptions ?? []).map((amount, index, all) => (
                <button key={amount} className={`btn ${index === all.length - 1 ? 'btn-accent' : 'btn-ghost'} btn-lg justify-between`} disabled={busy} onClick={() => void placeWager(amount)}>
                  <span>{all.length === 3 ? ['Safe', 'Bold', 'Go big'][index] : 'Bet'}</span>
                  <span>{amount}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {(phase === 'question' || phase === 'reveal') && item && item.options && (
          <>
            <section className="card rise p-4 sm:p-5" key={item.id}>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <span className="chip">{item.slot === 'music' ? 'Music Break' : item.round.name}</span>
                {item.section && item.slot !== 'music' && <span className="chip chip-accent">{item.section.heading}</span>}
                {item.wager ? <span className="chip chip-accent">Bet {item.wager}</span> : null}
                {item.multiplier !== 1 && !item.wager && <span className="chip">x{item.multiplier} points</span>}
              </div>
              {item.title && item.slot !== 'fact_fake' && item.slot !== 'music' && (
                <p className="pixel neon-accent mb-2 text-[10px] sm:text-xs">“{item.title}”</p>
              )}
              <h2 className={`!normal-case ${item.slot === 'fact_fake' ? 'text-[22px] sm:text-2xl' : 'text-[19px] sm:text-[22px]'}`} style={{ fontFamily: 'var(--font-sans)', lineHeight: 1.35, letterSpacing: 0 }}>
                {item.text}
              </h2>
              {item.peek && phase === 'question' && (
                <blockquote className="mt-3 border-l-4 pl-3 text-[15px] text-muted" style={{ borderColor: 'var(--accent)' }}>
                  <span className="pixel mb-1 block text-[8px]" style={{ color: 'var(--accent)' }}>From the report</span>
                  {item.peek}
                </blockquote>
              )}
              {item.hint && phase === 'question' && (
                <p className="mt-3 text-[15px]" style={{ color: 'var(--accent)' }}><span className="pixel text-[8px]">Hint · </span>{item.hint}</p>
              )}
            </section>

            {(reveal?.image ?? item.image) && (
              <PictureView
                key={`${item.id}:${phase}`}
                image={(reveal?.image ?? item.image)!}
                secondsLeft={phase === 'question' && item.timeLimitSec > 0 ? secondsLeft : null}
                caption={phase === 'question' ? item.text : null}
              />
            )}

            {/* The clip keeps playing through the reveal (dipping under the host) and fades out on Next. */}
            {item.music && (
              <MusicPlayer key={`clip:${item.id}`} src={item.music.previewUrl} active={phase === 'question'} onBroken={() => void musicBroken()} onPlaying={() => setClipStarted(true)} />
            )}

            {phase === 'question' && item.timeLimitSec > 0 && (
              <TimerBar
                // Re-read the clock from the server after a wager or a lifeline (writing a hint stops it).
                timerKey={`${item.id}:${item.wager ?? ''}:${item.lifelinesUsed.length}`}
                totalMs={item.timeLimitSec * 1000}
                remainingMs={item.remainingMs ?? item.timeLimitSec * 1000}
                running={!busy}
                hold={item.slot === 'music' && !clipStarted}
                onExpire={() => void submit(null)}
                onSecond={setSecondsLeft}
                quiet={item.slot === 'music'}
              />
            )}

            {item.slot === 'fact_fake' ? (
              <FactFakeButtons picked={picked} correctIndex={reveal ? reveal.correctIndex : null} disabled={busy || phase !== 'question'} onPick={(i) => void submit(i)} />
            ) : (
              <AnswerList
                options={item.options}
                removed={item.removed}
                picked={picked}
                correctIndex={reveal ? reveal.correctIndex : null}
                disabled={busy || phase !== 'question'}
                onPick={(i) => void submit(i)}
              />
            )}

            {canUseLifelines && (
              <div className="flex flex-wrap gap-2" aria-label="Lifelines">
                {LIFELINES.filter((l) => l.kind in state.lifelines).map((lifeline) => {
                  const left = state.lifelines[lifeline.kind] ?? 0
                  const used = item.lifelinesUsed.includes(lifeline.kind)
                  return (
                    <button key={lifeline.kind} className="btn btn-ghost btn-sm" disabled={busy || left <= 0 || used} onClick={() => void useLifeline(lifeline.kind)} title={lifeline.help}>
                      {lifeline.label} <span className="text-muted">×{left}</span>
                    </button>
                  )
                })}
              </div>
            )}

            {phase === 'reveal' && reveal && (
              <section ref={revealRef} className="card rise scroll-mb-28 p-4 sm:p-5" aria-live="polite">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm sm:text-base" style={{ color: reveal.correct ? 'var(--good)' : 'var(--bad)' }}>
                    {reveal.correct ? 'Correct!' : reveal.timedOut ? 'Out of time' : 'Wrong'}
                  </h3>
                  <span className="pixel text-xs" style={{ color: reveal.points > 0 ? 'var(--good)' : reveal.points < 0 ? 'var(--bad)' : 'var(--muted)' }}>
                    {reveal.points > 0 ? '+' : ''}{reveal.points}
                  </span>
                </div>
                {reveal.strikeRestored && <p className="pixel mt-2 text-[10px]" style={{ color: 'var(--good)' }}>Extra life!</p>}
                {!quick && (
                  <>
                    {!reveal.correct && <p className="mt-2">The answer: <b>{reveal.correctAnswer}</b></p>}
                    {reveal.music && (
                      <div className="mt-3 flex items-center gap-3">
                        {reveal.music.artworkUrl && <img src={reveal.music.artworkUrl} alt="" className="h-16 w-16 rounded-sm border-2 border-black" />}
                        <div className="min-w-0">
                          <p className="truncate font-bold">{reveal.music.title}</p>
                          <p className="truncate text-muted">{reveal.music.artist}</p>
                          {reveal.music.trackUrl && <a className="text-sm underline" href={reveal.music.trackUrl} target="_blank" rel="noreferrer">Listen on Apple Music</a>}
                        </div>
                      </div>
                    )}
                    {reveal.explanation && <p className="mt-2 text-[16px]">{reveal.explanation}</p>}
                    {reveal.sourceQuote && (
                      <blockquote className="mt-3 border-l-4 pl-3 text-[15px] text-muted" style={{ borderColor: 'var(--brand)' }}>
                        <span className="pixel mb-1 block text-[8px]">From the report{reveal.section ? ` · ${reveal.section.heading}` : ''}</span>
                        {reveal.sourceQuote}
                      </blockquote>
                    )}
                  </>
                )}
              </section>
            )}
          </>
        )}

        <ErrorNote>{error}</ErrorNote>
      </main>

      {/* Lightning statements and speed runs move on by themselves – unless that failed, when the button is the way out. */}
      {phase === 'reveal' && reveal && (!quick || Boolean(error)) && (
        <footer className="sticky bottom-0 z-[45] -mx-1 flex items-center gap-2 px-1 pb-1 pt-2" style={{ background: 'linear-gradient(transparent, var(--page) 38%)' }}>
          {item?.slot !== 'music' && (
            <button className="btn btn-ghost btn-sm" disabled={flagged} onClick={() => setFlagging(true)} title="Tell the organiser this question is not right">
              {flagged ? 'Flagged' : '⚑ Flag'}
            </button>
          )}
          <button className="btn btn-primary btn-lg flex-1" disabled={moving} onClick={advance} autoFocus>
            {error ? 'Try again ▶' : last ? 'See results ▶' : 'Next ▶'}
          </button>
        </footer>
      )}
      {stranded && phase !== 'reveal' && (
        <footer className="sticky bottom-0 z-[45] pb-1 pt-2">
          <button className="btn btn-primary btn-lg w-full" onClick={() => { setStranded(false); setError(''); void finish() }}>See results ▶</button>
        </footer>
      )}

      <Modal open={quitting} onClose={() => setQuitting(false)}>
        <div className="arcade">
          <h2 className="neon text-sm">Quit this game?</h2>
          <p className="mt-3 text-muted">Your answers so far still count towards your readiness.</p>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <button className="btn btn-ghost" onClick={() => setQuitting(false)}>Keep playing</button>
            <button className="btn btn-primary" onClick={() => void quit()}>Quit</button>
          </div>
        </div>
      </Modal>

      <Modal open={flagging} onClose={() => setFlagging(false)}>
        <div className="arcade">
          <h2 className="neon text-sm">What is wrong with it?</h2>
          <p className="mt-3 text-muted">The organiser will take a look. Thanks for keeping the quiz honest.</p>
          <div className="mt-4 grid gap-2">
            {FLAG_REASONS.map((reason) => (
              <button key={reason} className="answer" onClick={() => void flag(reason)}>{reason}</button>
            ))}
          </div>
        </div>
      </Modal>
    </div>
  )
}
