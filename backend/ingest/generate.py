"""Write quiz questions from a report and check them before they reach players.

Two independent passes per passage:

  1. a *writer* drafts multiple-choice questions and Fact-or-Fake statements, each with
     the exact supporting quote;
  2. a *checker* – which never sees the answer key – re-answers every question from the
     report (the passage plus the most related passages elsewhere) and flags anything
     ambiguous, arguable or guessable.

Only questions the checker independently answers the same way, with no flags, are
kept. Cheap deterministic checks run in between (grounding of the quote, numbers in
the answer really appearing in the passage, answer not leaked, no "the passage says").
Pictures get the same treatment with a vision model.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

import numpy as np
from pydantic import BaseModel, Field

import llm
from config import config
from ingest.model import Chunk, ImageItem, StructuredDocument

log = logging.getLogger("generate")

Skill = Literal["recall", "understanding"]
Difficulty = Literal["easy", "medium", "hard"]


# --------------------------------------------------------------------------- schemas

class MCQDraft(BaseModel):
    title: str = Field(description="Short punny title, 2-5 words, that does not give away the answer")
    question: str
    correct_answer: str
    distractors: list[str] = Field(description="Exactly three wrong but believable options")
    source_quote: str = Field(description="Verbatim sentence or table row that proves the answer")
    explanation: str = Field(description="One sentence shown to the player after answering")
    skill: Skill
    difficulty: Difficulty


class StatementDraft(BaseModel):
    statement: str
    is_true: bool
    source_quote: str
    explanation: str = Field(description="One sentence giving the true fact")


class PassageDrafts(BaseModel):
    questions: list[MCQDraft]
    statements: list[StatementDraft]


class MCQCheck(BaseModel):
    number: int = Field(description="The question number as given")
    answer_index: int = Field(description="0-based index of the option the report supports, or -1 if none")
    problems: list[Literal[
        "ambiguous", "multiple_correct", "not_answerable", "leaks_answer",
        "refers_to_passage", "guessable", "not_readable",
    ]]


class StatementCheck(BaseModel):
    number: int
    verdict: Literal["true", "false", "unclear"]


class CheckResult(BaseModel):
    questions: list[MCQCheck]
    statements: list[StatementCheck]


class ImageDrafts(BaseModel):
    kind: Literal["chart", "table", "diagram", "timeline", "map", "screenshot", "photo", "logo", "decorative", "other"]
    informative: bool
    description: str = Field(description="One or two sentences describing the picture (used as alt text)")
    questions: list[MCQDraft]


class SectionNames(BaseModel):
    names: list[str]


class Summary(BaseModel):
    summary: str = Field(description="Two or three sentences on what the report is about and why it matters")
    themes: list[str] = Field(description="Up to six short key themes")


# --------------------------------------------------------------------------- results

@dataclass
class ReadyQuestion:
    kind: Literal["mcq", "fact_fake"]
    source_type: Literal["text", "table", "chart", "image"]
    question_text: str
    chunk_index: int | None = None
    image_index: int | None = None
    section_index: int = 0
    title: str | None = None
    options: list[str] | None = None       # correct answer first; shuffled when served
    correct_index: int | None = None
    is_true: bool | None = None
    explanation: str = ""
    source_quote: str = ""
    skill: str = "recall"
    difficulty: str = "medium"
    quality: dict = field(default_factory=dict)
    embedding: np.ndarray | None = None


@dataclass
class GenerationReport:
    questions: list[ReadyQuestion] = field(default_factory=list)
    image_notes: dict[int, tuple[str, str, bool]] = field(default_factory=dict)  # index -> (kind, description, usable)
    drafted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


# --------------------------------------------------------------------------- prompts

WRITER_SYSTEM = """You write questions for a workplace trivia game. Colleagues play it before a meeting to show they read and understood a report. The game feels like a pub quiz with a sarcastic game-show host, but every question must be a fair, precise test of the report.

You are given the report's title and outline, and ONE passage. Write only about facts stated in that passage.

A good question
- Tests something that matters: a key figure, a decision, a cause, a risk, an owner, a date, a comparison, what depends on what, what the reader is being asked to do.
- Can be answered by someone who read the report carefully and cannot be answered by someone who did not: not by common sense, and not from the wording of the options.
- Has exactly one option that is correct according to the report. The three wrong options are wrong according to the report yet believable: the same kind of thing, the same format and unit, similar length and magnitude. For numbers use nearby values. For names use other names of the same kind. No joke options, no "all of the above", no "none of the above".
- Stands on its own. Name the specific thing you are asking about ("the Phoenix cross-dock", "Northstar Phase 2"). Never write "the passage", "this section", "the text", "the excerpt" or "mentioned above". You may say "the report".
- Does not contain or hint at its own answer.
- Prefer understanding over look-up: why something happened, what a number means, what follows from a decision, how two facts relate. Label each question honestly as "understanding" or "recall". Aim for at least half "understanding" when the passage allows it.
- For tables and chart data: compare rows, find the largest or smallest, or read a specific cell.
- Never ask about formatting, figure or table numbers, page layout, or who wrote the document.

title
Give each question a short punny or wry title of 2 to 5 words, the way a pub quiz names its questions. The title must not give away the answer. The question itself stays plain and precise; the title carries the humour.

source_quote
Copy, word for word, the sentence or table row from the passage that proves the correct answer (at most 300 characters).

explanation
One plain sentence the player sees after answering: state the correct fact, and the "so what" if the passage gives one. Do not write "according to".

difficulty
easy = a headline fact nobody who read it would miss; medium = a specific detail; hard = needs two facts combined or a careful reading.

Fact or Fake statements
Also write short statements for a rapid-fire round where players call each FACT or FAKE.
- A FACT restates something from the passage in fresh words.
- A FAKE changes exactly one important detail (a number, a name, a direction, a date, a cause) so it is plainly false according to the report. It must not be a trick of wording and must not be something that could also be true.
- One sentence, at most 140 characters, readable in three seconds.
- explanation gives the true fact in one sentence.
- Mix facts and fakes.

How many
Write up to the requested number of questions and statements, each about a different fact. Fewer is fine. If the passage has nothing worth asking (boilerplate, a cover note, a disclaimer), return empty lists."""

CHECKER_SYSTEM = """You are the quality checker for a quiz about a report. Players are colleagues who read the report; a question they could fairly argue about is not acceptable, so be strict.

You get the main passage, some related passages from elsewhere in the same report, and candidate questions WITHOUT the answer key.

For each multiple-choice question
- answer_index: the 0-based index of the option the report supports. Use -1 if no option is clearly supported.
- problems: list any that apply
  - ambiguous: the question can reasonably be read in two ways
  - multiple_correct: more than one option is defensible given ALL the passages
  - not_answerable: the passages do not settle it
  - leaks_answer: the wording of the question or options gives the answer away
  - refers_to_passage: it says "the passage", "this section", "above" or similar instead of standing on its own
  - guessable: someone who never read the report would very likely get it right from common sense alone
  Leave the list empty when the question is sound.

For each statement
- verdict: "true" if the report clearly makes it true, "false" if the report clearly makes it false, "unclear" if it cannot be decided or is only loosely worded.

Answer every numbered item."""

IMAGE_WRITER_SYSTEM = """You write picture-round questions for a workplace trivia game about a report. You are shown ONE picture from the report, with its caption and nearby text.

First decide what the picture is and whether it is informative. A chart, table, diagram, timeline, map, annotated screenshot or meaningful photo is informative. A logo, decorative photo, banner, icon, signature or cover image is not.

If it is informative, describe it in one or two sentences (this becomes its alt text) and write up to the requested number of multiple-choice questions that the player answers BY LOOKING AT THE PICTURE, which is shown on screen with the question.
- Ask about what the picture shows: the largest or smallest, a trend, a clearly labelled value, which item is in which state, what comes after what.
- The answer must be plainly readable in the picture. Never ask for a value that has to be estimated between gridlines or read from tiny text.
- Exactly one correct option and three believable wrong ones of the same kind, ideally other items or values that appear in the picture.
- The question must stand on its own and name what it asks about. Do not ask what the caption says or what the figure number is.
- title: a short punny title of 2 to 5 words that does not give away the answer.
- source_quote: say where in the picture the answer is read from, e.g. "The Southwest bar is labelled 52.6".
- explanation: one sentence stating the answer and what it means in the report.
- skill: "understanding" when it needs comparing or interpreting, otherwise "recall".

If it is not informative, set informative to false and return no questions."""

IMAGE_CHECKER_SYSTEM = """You are the quality checker for a picture round in a quiz about a report. You are shown the picture and candidate questions WITHOUT the answer key. Answer each from the picture alone.

- answer_index: the 0-based index of the option the picture supports, or -1 if none is clearly supported.
- problems: list any that apply
  - not_readable: the answer cannot be read clearly from the picture
  - ambiguous / multiple_correct / not_answerable / leaks_answer / guessable: as usual
  Leave the list empty when the question is sound.
Return an empty statements list. Be strict."""


# --------------------------------------------------------------------------- deterministic checks

_REFERS_TO_PASSAGE = re.compile(
    r"\b(the|this)\s+(passage|excerpt|paragraph|text)\b|\bthis section\b|\b(table|text|figure|chart)\s+above\b"
    r"|\bmentioned (above|in the)\b|\bas stated\b|\baccording to the (passage|text|excerpt)\b", re.I)
_WORD = re.compile(r"[a-z0-9]+(?:[.,]\d+)*")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text)}


def quote_is_grounded(quote: str, passage: str) -> bool:
    quote_norm, passage_norm = _norm(quote), _norm(passage)
    if not quote_norm:
        return False
    if quote_norm in passage_norm:
        return True
    tokens = [t for t in quote_norm.split() if len(t) >= 3]
    if len(tokens) < 3:
        return False
    passage_tokens = set(passage_norm.split())
    return sum(t in passage_tokens for t in tokens) / len(tokens) >= 0.8


def mcq_problem(draft: MCQDraft, passage: str | None) -> str | None:
    """Return a rejection reason, or None when the draft passes the mechanical checks."""
    question = draft.question.strip()
    answer = draft.correct_answer.strip()
    distractors = [d.strip() for d in draft.distractors if d and d.strip()]
    if len(distractors) != 3:
        return "wrong number of options"
    options = [answer, *distractors]
    if len({_norm(o) for o in options}) != 4:
        return "duplicate options"
    if not (12 <= len(question) <= 260) or not answer or any(len(o) > 110 for o in options):
        return "question or option too long/short"
    if _REFERS_TO_PASSAGE.search(question):
        return "refers to 'the passage'"
    answer_norm = _norm(answer)
    if len(answer_norm) >= 4 and f" {answer_norm} " in f" {_norm(question)} ":
        return "answer appears in the question"
    longest_wrong = max(len(d) for d in distractors)
    if len(answer) > 34 and len(answer) > 1.8 * longest_wrong:
        return "correct option is conspicuously longer"
    if any(re.search(r"\b(all|none) of the above\b", o, re.I) for o in options):
        return "all/none of the above"
    if passage is not None:
        if not quote_is_grounded(draft.source_quote, passage):
            return "supporting quote not found in the passage"
        missing = _numbers(answer) - _numbers(passage)
        if missing and draft.skill == "recall":
            return "number in the answer is not in the passage"
    return None


def statement_problem(draft: StatementDraft, passage: str) -> str | None:
    statement = draft.statement.strip()
    if not (15 <= len(statement) <= 170):
        return "statement too long/short"
    if _REFERS_TO_PASSAGE.search(statement):
        return "refers to 'the passage'"
    if not quote_is_grounded(draft.source_quote, passage):
        return "supporting quote not found in the passage"
    if draft.is_true and (_numbers(statement) - _numbers(passage)):
        return "a 'fact' uses a number that is not in the passage"
    return None


# --------------------------------------------------------------------------- writing + checking

def _quota(chunk: Chunk) -> tuple[int, int]:
    tokens = chunk.token_count
    if chunk.kind in ("table", "chart"):
        return (3 if tokens >= 70 else 2), (2 if tokens >= 70 else 1)
    if tokens < 45:
        return 0, 0
    if tokens < 100:
        return 1, 1
    if tokens < 230:
        return 2, 2
    return 3, 2


def _outline(document: StructuredDocument) -> str:
    return "\n".join(f"- {s.heading}" for s in document.sections)


async def _write_for_chunk(document: StructuredDocument, chunk: Chunk, previous: Chunk | None,
                           avoid: list[str], max_q: int, max_s: int) -> PassageDrafts:
    parts = [
        f"Report: {document.title}",
        f"Outline:\n{_outline(document)}",
        f"Where this passage sits: {chunk.heading_path}",
        f"Passage type: {chunk.kind}",
    ]
    if previous is not None and previous.section == chunk.section:
        parts.append("Just before it (context only – do not write questions about this):\n" + previous.text[-450:])
    if avoid:
        parts.append("Already in the quiz – write about DIFFERENT facts:\n" + "\n".join(f"- {a}" for a in avoid[:12]))
    parts.append(f"Write up to {max_q} questions and up to {max_s} statements.")
    parts.append(f"PASSAGE\n<<<\n{chunk.text}\n>>>")
    return await llm.parse(model=config.model_writer, system=WRITER_SYSTEM, user="\n\n".join(parts),
                           schema=PassageDrafts, effort="low", max_output_tokens=5000)


def _present(mcqs: list[tuple[MCQDraft, list[str], int]], statements: list[StatementDraft]) -> str:
    lines: list[str] = []
    if mcqs:
        lines.append("MULTIPLE-CHOICE QUESTIONS")
        for number, (draft, options, _) in enumerate(mcqs, start=1):
            lines.append(f"\nQuestion {number}: {draft.question}")
            lines += [f"  [{i}] {option}" for i, option in enumerate(options)]
    if statements:
        lines.append("\nSTATEMENTS")
        lines += [f"Statement {number}: {s.statement}" for number, s in enumerate(statements, start=1)]
    return "\n".join(lines)


def _shuffle(draft: MCQDraft) -> tuple[list[str], int]:
    options = [draft.correct_answer.strip(), *[d.strip() for d in draft.distractors]]
    order = list(range(4))
    random.shuffle(order)
    return [options[i] for i in order], order.index(0)


_FATAL = {"ambiguous", "multiple_correct", "not_answerable", "leaks_answer", "refers_to_passage", "guessable", "not_readable"}


def _accept_mcqs(mcqs: list[tuple[MCQDraft, list[str], int]], checks: list[MCQCheck], report: GenerationReport
                 ) -> list[tuple[MCQDraft, dict]]:
    by_number = {c.number: c for c in checks}
    accepted: list[tuple[MCQDraft, dict]] = []
    for number, (draft, _, correct_at) in enumerate(mcqs, start=1):
        check = by_number.get(number)
        if check is None:
            report.reject("checker skipped it")
        elif check.answer_index != correct_at:
            report.reject("checker chose a different answer")
        elif _FATAL.intersection(check.problems):
            report.reject("checker flagged: " + ", ".join(sorted(_FATAL.intersection(check.problems))))
        else:
            accepted.append((draft, {"checked": True}))
    return accepted


async def _questions_for_chunk(document: StructuredDocument, index: int, embeddings: np.ndarray,
                               avoid: list[str], report: GenerationReport) -> list[ReadyQuestion]:
    chunk = document.chunks[index]
    max_q, max_s = _quota(chunk)
    if not max_q and not max_s:
        return []
    previous = document.chunks[index - 1] if index > 0 else None
    drafts = await _write_for_chunk(document, chunk, previous, avoid, max_q, max_s)
    report.drafted += len(drafts.questions) + len(drafts.statements)

    mcqs: list[tuple[MCQDraft, list[str], int]] = []
    for draft in drafts.questions[:max_q + 1]:
        problem = mcq_problem(draft, chunk.text)
        if problem:
            report.reject(problem)
        else:
            mcqs.append((draft, *_shuffle(draft)))
    statements: list[StatementDraft] = []
    for statement in drafts.statements[:max_s + 1]:
        problem = statement_problem(statement, chunk.text)
        if problem:
            report.reject(problem)
        else:
            statements.append(statement)
    if not mcqs and not statements:
        return []

    related = [document.chunks[i] for i, score in llm.cosine_top(embeddings[index], embeddings, 3, exclude=index)
               if score >= 0.35]
    user = "\n\n".join([
        f"Report: {document.title}",
        f"MAIN PASSAGE ({chunk.heading_path})\n<<<\n{chunk.text}\n>>>",
        "RELATED PASSAGES FROM ELSEWHERE IN THE REPORT\n" + (
            "\n\n".join(f"({c.heading_path})\n{c.text[:1400]}" for c in related) or "(none)"),
        _present(mcqs, statements),
    ])
    check = await llm.parse(model=config.model_checker, system=CHECKER_SYSTEM, user=user,
                            schema=CheckResult, effort="low", max_output_tokens=3000)

    source_type = chunk.kind if chunk.kind in ("table", "chart") else "text"
    ready: list[ReadyQuestion] = []
    for draft, quality in _accept_mcqs(mcqs, check.questions, report):
        ready.append(ReadyQuestion(
            kind="mcq", source_type=source_type, chunk_index=index, section_index=chunk.section,
            title=draft.title.strip()[:60] or None, question_text=draft.question.strip(),
            options=[draft.correct_answer.strip(), *[d.strip() for d in draft.distractors]], correct_index=0,
            explanation=draft.explanation.strip(), source_quote=draft.source_quote.strip()[:400],
            skill=draft.skill, difficulty=draft.difficulty, quality=quality))
    verdicts = {c.number: c.verdict for c in check.statements}
    for number, statement in enumerate(statements, start=1):
        verdict = verdicts.get(number)
        if verdict != ("true" if statement.is_true else "false"):
            report.reject("checker disagreed on a fact/fake statement")
            continue
        ready.append(ReadyQuestion(
            kind="fact_fake", source_type=source_type, chunk_index=index, section_index=chunk.section,
            question_text=statement.statement.strip(), is_true=statement.is_true,
            explanation=statement.explanation.strip(), source_quote=statement.source_quote.strip()[:400],
            difficulty="medium", quality={"checked": True}))
    return ready


async def _questions_for_image(document: StructuredDocument, image: ImageItem, report: GenerationReport
                               ) -> tuple[list[ReadyQuestion], tuple[str, str, bool]]:
    picture = llm.image_part(image.data, image.mime)
    intro = f"Report: {document.title}\n{image.context}\n\nWrite up to 3 questions."
    drafts = await llm.parse(model=config.model_vision, system=IMAGE_WRITER_SYSTEM,
                             user=[llm.text_part(intro), picture], schema=ImageDrafts,
                             effort="low", max_output_tokens=4000)
    if not drafts.informative or not drafts.questions:
        return [], (drafts.kind, drafts.description.strip(), False)
    report.drafted += len(drafts.questions)
    mcqs: list[tuple[MCQDraft, list[str], int]] = []
    for draft in drafts.questions[:3]:
        problem = mcq_problem(draft, None)
        if problem:
            report.reject(problem)
        else:
            mcqs.append((draft, *_shuffle(draft)))
    if not mcqs:
        return [], (drafts.kind, drafts.description.strip(), False)
    check = await llm.parse(model=config.model_vision, system=IMAGE_CHECKER_SYSTEM,
                            user=[llm.text_part(_present(mcqs, [])), picture], schema=CheckResult,
                            effort="low", max_output_tokens=2000)
    ready = [ReadyQuestion(
        kind="mcq", source_type="image", image_index=image.ordinal, section_index=image.section,
        title=draft.title.strip()[:60] or None, question_text=draft.question.strip(),
        options=[draft.correct_answer.strip(), *[d.strip() for d in draft.distractors]], correct_index=0,
        explanation=draft.explanation.strip(), source_quote=draft.source_quote.strip()[:400],
        skill=draft.skill, difficulty=draft.difficulty, quality=quality)
        for draft, quality in _accept_mcqs(mcqs, check.questions, report)]
    return ready, (drafts.kind, drafts.description.strip(), bool(ready))


def _dedupe(questions: list[ReadyQuestion], existing: np.ndarray | None, report: GenerationReport,
            threshold: float = 0.88) -> list[ReadyQuestion]:
    kept: list[ReadyQuestion] = []
    pool = [row for row in existing] if existing is not None and existing.size else []
    for question in questions:
        vector = question.embedding
        if vector is not None and pool and float(np.max(np.stack(pool) @ vector)) >= threshold:
            report.reject("near-duplicate of another question")
            continue
        kept.append(question)
        if vector is not None:
            pool.append(vector)
    return kept


Progress = Callable[[str, int, int], Awaitable[None]]


async def generate_bank(document: StructuredDocument, chunk_embeddings: np.ndarray, *,
                        progress: Progress | None = None, avoid_by_chunk: dict[int, list[str]] | None = None,
                        existing_embeddings: np.ndarray | None = None, include_images: bool = True,
                        ) -> GenerationReport:
    """Write and check questions for every passage and picture of one document."""
    report = GenerationReport()
    avoid_by_chunk = avoid_by_chunk or {}
    jobs = len(document.chunks) + (len(document.images) if include_images else 0)
    done = 0
    lock = asyncio.Lock()

    async def tick() -> None:
        nonlocal done
        async with lock:
            done += 1
            if progress:
                await progress("Writing and checking questions", done, jobs)

    async def chunk_job(index: int) -> list[ReadyQuestion]:
        try:
            return await _questions_for_chunk(document, index, chunk_embeddings, avoid_by_chunk.get(index, []), report)
        except Exception as exc:  # one bad passage must not sink the whole report
            log.warning("passage %s failed: %s", index, exc)
            report.reject("passage failed (AI error)")
            return []
        finally:
            await tick()

    async def image_job(image: ImageItem) -> list[ReadyQuestion]:
        try:
            ready, note = await _questions_for_image(document, image, report)
            report.image_notes[image.ordinal] = note
            return ready
        except Exception as exc:
            log.warning("picture %s failed: %s", image.ordinal, exc)
            report.reject("picture failed (AI error)")
            report.image_notes[image.ordinal] = ("other", "", False)
            return []
        finally:
            await tick()

    tasks = [chunk_job(i) for i in range(len(document.chunks))]
    if include_images:
        tasks += [image_job(image) for image in document.images]
    batches = await asyncio.gather(*tasks)
    questions = [q for batch in batches for q in batch]

    if questions:
        if progress:
            await progress("Removing near-duplicates", jobs, jobs)
        texts = [f"{q.question_text} {q.options[0] if q.options else ''}" for q in questions]
        vectors = await llm.embed(texts)
        for question, vector in zip(questions, vectors):
            question.embedding = vector
        # Keep the picture-round and "understanding" questions when two overlap.
        questions.sort(key=lambda q: (q.source_type != "image", q.skill != "understanding"))
        questions = _dedupe(questions, existing_embeddings, report)
    report.questions = questions
    return report


async def name_sections(samples: list[str]) -> list[str]:
    """Give short titles to sections of a document that has no headings."""
    listing = "\n\n".join(f"PART {i}\n{text[:700]}" for i, text in enumerate(samples, start=1))
    result = await llm.parse(
        model=config.model_fast,
        system="Give each part of this report a plain, specific title of 2 to 5 words. Return the titles in order.",
        user=listing, schema=SectionNames, effort="low", max_output_tokens=600)
    return [n.strip()[:60] for n in result.names]


async def summarise(document: StructuredDocument) -> Summary:
    body = "\n\n".join(c.text for c in document.chunks)[:9000]
    return await llm.parse(
        model=config.model_fast,
        system="Summarise this report for someone about to host a quiz on it. Be concrete and neutral.",
        user=f"Report: {document.title}\nOutline:\n{_outline(document)}\n\n{body}",
        schema=Summary, effort="low", max_output_tokens=800)
