// A timeline of what the game did and when: every host line, question, clip and sound effect,
// stamped in milliseconds. It exists to check the *sequencing* – that the host never talks over
// a question, that music starts when it should – without relying on anyone's ears.
//
// Off unless asked for: add ?trace to the address, or set localStorage "quiz.trace" to "1".
// Read it back from the browser console as window.__quizTrace.

export type TraceEntry = { t: number; event: string; [detail: string]: unknown }

const enabled = (() => {
  try {
    return new URLSearchParams(window.location.search).has('trace') || window.localStorage.getItem('quiz.trace') === '1'
  } catch {
    return false
  }
})()

const entries: TraceEntry[] = []
if (enabled) (window as unknown as { __quizTrace: TraceEntry[] }).__quizTrace = entries

export function trace(event: string, detail?: Record<string, unknown>) {
  if (enabled) entries.push({ t: Math.round(performance.now()), event, ...detail })
}
