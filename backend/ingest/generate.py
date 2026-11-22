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
    difficulty: Difficulty = "medium"


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

WRITER_SYSTEM = """You write questions for a lively trivia game based on a reading document (such as a report, guide, briefing, lesson, or handbook). Players play it to prove they read and understood the document. The game feels like a pub quiz with a witty game-show host, but every question must be a fair, precise test of the document.

You are given the document's title, outline, key entities/terms, and ONE passage. Write only about facts stated in that passage.

A good question
- Tests something that matters: a key figure, a decision, a cause, a risk, an owner, a date, a comparison, what depends on what, what the reader needs to know.
- Can be answered by someone who read the document carefully and cannot be answered by someone who did not: not by common sense, and not from the wording of the options.
- Has exactly one option that is correct according to the document. The three wrong options are wrong according to the document yet believable: the same kind of thing, the same format, precision, and unit, similar length and magnitude. For numbers use nearby values with matching decimal precision. For names use other names of the same kind. No joke options, no "all of the above", no "none of the above".
- Stands on its own. Name the specific thing you are asking about ("the Phoenix cross-dock", "Apollo 11's Eagle lander"). Never write "the passage", "this section", "the text", "the excerpt" or "mentioned above". Use the document's actual topic rather than calling a sports guide or history piece "the report".
- Does not contain or hint at its own answer.
- Prefer understanding over look-up: why something happened, what a number means, what follows from a decision, how two facts relate. Label each question honestly as "understanding" or "recall". Aim for at least half "understanding" when the passage allows it.
- For tables and chart data: compare rows, find the largest or smallest, or read a specific cell.
- Never ask about formatting, figure or table numbers, page layout, or who wrote the document.

title
Give each question a short punny or wry title of 2 to 5 words, the way a pub quiz names its questions. The title must NEVER contain or give away the answer. The question itself stays plain and precise; the title carries the humour.

source_quote
Copy, word for word, the sentence or table row from the passage that proves the correct answer (at most 300 characters).

explanation
One plain sentence the player sees after answering: state the correct fact, and the "so what" if the passage gives one. Do not write "according to".

difficulty (IMPORTANT — write a true easy → medium → hard ladder!)
Games start easy and ramp up so beginners and younger players build confidence while experts still get tested at the end. Across the questions you write for a passage, aim for roughly 35% easy, 45% medium, and 20% hard:
- easy: a clear, memorable, fun headline fact, landmark, mascot, main rule, or big takeaway anyone who read the document (or is learning from it) can enjoy. Plain wording, distinct believable choices — NO trick-number traps (like 75,125 vs 76,125) on easy questions.
- medium: a specific detail, section, comparison, or cause that rewards attentive reading.
- hard: needs two facts combined, careful arithmetic, or a fine-grained operational number/section.

Fact or Fake statements
Also write short statements for a rapid-fire round where players call each FACT or FAKE.
- A FACT restates something from the passage in fresh words.
- A FAKE changes exactly one important detail (a number, a name, a direction, a date, a cause) so it is plainly false according to the document. It must not be a trick of wording and must not be something that could also be true.
- One sentence, at most 140 characters, readable in three seconds.
- explanation gives the true fact in one sentence.
- Mix facts and fakes.

How many
Write up to the requested number of questions and statements, each about a different fact. Fewer is fine. If the passage has nothing worth asking (boilerplate, a cover note, a disclaimer), return empty lists."""

CHECKER_SYSTEM = """You are the quality checker for a trivia quiz based on a reading document. Players read the document; a question they could fairly argue about is not acceptable, so be strict.

You get the main passage, some related passages from elsewhere in the same document, and candidate questions (including their punny titles) WITHOUT the answer key.

For each multiple-choice question
- answer_index: the 0-based index of the option the document supports. Use -1 if no option is clearly supported.
- problems: list any that apply
  - ambiguous: the question can reasonably be read in two ways
  - multiple_correct: more than one option is defensible given ALL the passages
  - not_answerable: the passages do not settle it
  - leaks_answer: the wording of the question, its title, or the options gives the answer away
  - refers_to_passage: it says "the passage", "this section", "above" or similar instead of standing on its own
  - guessable: someone who never read the document could easily deduce the answer from obvious wording clues, joke distractors, conspicuous precision differences, or universal common sense (do NOT flag a legitimate easy warm-up question about a real headline fact in the document as guessable just because it is approachable)
  Leave the list empty when the question is sound.

For each statement
- verdict: "true" if the document clearly makes it true, "false" if the document clearly makes it false, "unclear" if it cannot be decided or is only loosely worded.

Answer every numbered item."""

IMAGE_WRITER_SYSTEM = """You write picture-round questions for a trivia game based on a reading document. You are shown ONE picture from the document, with its caption and nearby context.

First decide what the picture is and whether it is informative. A chart, table, diagram, timeline, map, annotated screenshot or meaningful photo is informative. A logo, decorative photo, banner, icon, signature or cover image is not.

If it is informative, describe it in one or two sentences (this becomes its alt text) and write up to the requested number of multiple-choice questions that the player answers BY LOOKING AT THE PICTURE, which is shown on screen with the question.
- Ask about what the picture shows: the largest or smallest, a trend, a clearly labelled value, which item is in which state, what comes after what.
- The answer must be plainly readable in the picture. Never ask for a value that has to be estimated between gridlines or read from tiny text.
- Exactly one correct option and three believable wrong ones of the same kind and precision, ideally other items or values that appear in the picture.
- The question must stand on its own and name what it asks about. Do not ask what the caption says or what the figure number is.
- title: a short punny title of 2 to 5 words that does not give away the answer.
- source_quote: say where in the picture the answer is read from, e.g. "The Southwest bar is labelled 52.6".
- explanation: one sentence stating the answer and what it means in the document.
- skill: "understanding" when it needs comparing or interpreting, otherwise "recall".
- difficulty: "easy" for the obvious headline/largest/smallest bar or single prominent label, "medium" for a clear comparison or trend, "hard" for multi-series synthesis or subtle contrast.

If it is not informative, set informative to false and return no questions."""

IMAGE_CHECKER_SYSTEM = """You are the quality checker for a picture round in a trivia quiz. You are shown the picture (plus its caption and nearby context when available) and candidate questions (with their titles) WITHOUT the answer key. Answer each from the picture and context alone.

- answer_index: the 0-based index of the option the picture supports, or -1 if none is clearly supported.
- problems: list any that apply
  - not_readable: the answer cannot be read clearly from the picture
  - ambiguous / multiple_correct / not_answerable / leaks_answer / guessable: as usual (including if the title leaks the answer)
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
    out: set[str] = set()
    for n in _NUMBER.findall(text):
        cleaned = n.replace(",", "").rstrip(".")
        if "." in cleaned:
            cleaned = cleaned.rstrip("0").rstrip(".")
        if cleaned:
            out.add(cleaned)
    return out


def _has_decimal(text: str) -> bool:
    return bool(re.search(r"\d+\.\d+", text))


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
    title = (draft.title or "").strip()
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
    if title and len(answer_norm) >= 4 and f" {answer_norm} " in f" {_norm(title)} ":
        return "title gives away the answer"
    wrong_lengths = sorted(len(d) for d in distractors)
    median_wrong = wrong_lengths[len(wrong_lengths) // 2]
    if len(answer) > 34 and len(answer) > 1.35 * median_wrong:
        return "correct option is conspicuously longer"
    # Precision tell: correct option has a decimal (e.g. 14.7%) while all three distractors are round integers.
    if _has_decimal(answer) and all(_numbers(d) and not _has_decimal(d) for d in distractors):
        return "correct option is conspicuously more precise"
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


_ENTITY_RE = re.compile(r"\b(?:[A-Z]{2,6}\d*|[A-Z][a-z]+(?:\s+[A-Z][a-z0-9]+){1,2})\b")
_COMMON_CAPS = {"The", "This", "That", "These", "Those", "Table", "Figure", "Chart", "Section", "Part", "Overview"}


def _entity_glossary(document: StructuredDocument, limit: int = 18) -> str:
    """Compact list of recurring proper nouns / acronyms across the document to keep terminology consistent."""
    from collections import Counter

    counts: Counter[str] = Counter()
    for chunk in document.chunks[:40]:
        for match in _ENTITY_RE.findall(chunk.text):
            if match in _COMMON_CAPS or len(match) < 2:
                continue
            counts[match] += 1
    top = [term for term, cnt in counts.most_common(limit) if cnt >= 2]
    return ", ".join(top)


async def _write_for_chunk(document: StructuredDocument, chunk: Chunk, previous: Chunk | None,
                           avoid: list[str], max_q: int, max_s: int, glossary: str = "") -> PassageDrafts:
    parts = [
        f"Document: {document.title}",
        f"Outline:\n{_outline(document)}",
        f"Where this passage sits: {chunk.heading_path}",
        f"Passage type: {chunk.kind}",
    ]
    if glossary:
        parts.append(f"Key document entities/terms (for consistent naming): {glossary}")
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
            title_part = f' [Title: "{draft.title.strip()}"]' if draft.title and draft.title.strip() else ""
            lines.append(f"\nQuestion {number}{title_part}: {draft.question}")
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
                               avoid: list[str], report: GenerationReport, glossary: str = "") -> list[ReadyQuestion]:
    chunk = document.chunks[index]
    max_q, max_s = _quota(chunk)
    if not max_q and not max_s:
        return []
    previous = document.chunks[index - 1] if index > 0 else None
    drafts = await _write_for_chunk(document, chunk, previous, avoid, max_q, max_s, glossary=glossary)
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
            difficulty=statement.difficulty, quality={"checked": True}))
    return ready


async def _questions_for_image(document: StructuredDocument, image: ImageItem, report: GenerationReport
                               ) -> tuple[list[ReadyQuestion], tuple[str, str, bool]]:
    picture = llm.image_part(image.data, image.mime)
    intro = f"Report: {document.title}\n{image.context}\n\nWrite up to 3 questions."
    drafts = await llm.parse(model=config.model_vision, system=IMAGE_WRITER_SYSTEM,
                             user=[llm.text_part(intro), picture], schema=ImageDrafts,
                             effort="low", max_output_tokens=4000)
    if not drafts.informative:
        return [], (drafts.kind, drafts.description.strip(), False)
    if not drafts.questions:
        return [], (drafts.kind, drafts.description.strip(), True)
    report.drafted += len(drafts.questions)
    mcqs: list[tuple[MCQDraft, list[str], int]] = []
    for draft in drafts.questions[:3]:
        problem = mcq_problem(draft, None)
        if problem:
            report.reject(problem)
        else:
            mcqs.append((draft, *_shuffle(draft)))
    if not mcqs:
        # Still mark informative visuals usable=True so Answer Reveal matching can use them.
        return [], (drafts.kind, drafts.description.strip(), True)
    ctx_parts = []
    if image.caption:
        ctx_parts.append(f"Caption: {image.caption}")
    if image.context:
        ctx_parts.append(f"Context:\n{image.context[:450]}")
    context_prefix = ("\n".join(ctx_parts) + "\n\n") if ctx_parts else ""
    check = await llm.parse(model=config.model_vision, system=IMAGE_CHECKER_SYSTEM,
                            user=[llm.text_part(context_prefix + _present(mcqs, [])), picture], schema=CheckResult,
                            effort="low", max_output_tokens=2000)
    ready = [ReadyQuestion(
        kind="mcq", source_type="image", image_index=image.ordinal, section_index=image.section,
        title=draft.title.strip()[:60] or None, question_text=draft.question.strip(),
        options=[draft.correct_answer.strip(), *[d.strip() for d in draft.distractors]], correct_index=0,
        explanation=draft.explanation.strip(), source_quote=draft.source_quote.strip()[:400],
        skill=draft.skill, difficulty=draft.difficulty, quality=quality)
        for draft, quality in _accept_mcqs(mcqs, check.questions, report)]
    return ready, (drafts.kind, drafts.description.strip(), True)


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
    glossary = _entity_glossary(document)
    jobs = len(document.chunks) + (len(document.images) if include_images else 0)
    done = 0
    attempted = 0
    failed = 0
    lock = asyncio.Lock()

    async def tick() -> None:
        nonlocal done
        async with lock:
            done += 1
            if progress:
                await progress("Writing and checking questions", done, jobs)

    async def chunk_job(index: int) -> list[ReadyQuestion]:
        nonlocal attempted, failed
        chunk = document.chunks[index]
        max_q, max_s = _quota(chunk)
        if not max_q and not max_s:
            await tick()
            return []
        attempted += 1
        try:
            return await _questions_for_chunk(
                document, index, chunk_embeddings, avoid_by_chunk.get(index, []), report, glossary=glossary,
            )
        except Exception as exc:  # one bad passage must not sink the whole document
            failed += 1
            log.warning("passage %s failed: %s", index, exc)
            report.reject("passage failed (AI error)")
            return []
        finally:
            await tick()

    async def image_job(image: ImageItem) -> list[ReadyQuestion]:
        nonlocal attempted, failed
        attempted += 1
        try:
            ready, note = await _questions_for_image(document, image, report)
            report.image_notes[image.ordinal] = note
            return ready
        except Exception as exc:
            failed += 1
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
    if attempted > 0 and failed == attempted and report.drafted == 0:
        raise llm.AIUnavailable("Every AI question-writing request failed.")
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
        system="Give each part of this document a plain, specific title of 2 to 5 words. Return the titles in order.",
        user=listing, schema=SectionNames, effort="low", max_output_tokens=600)
    return [n.strip()[:60] for n in result.names]


async def summarise(document: StructuredDocument) -> Summary:
    body = "\n\n".join(c.text for c in document.chunks)[:9000]
    return await llm.parse(
        model=config.model_fast,
        system="Summarise this document for someone about to host a trivia quiz on it. Be concrete and neutral.",
        user=f"Document: {document.title}\nOutline:\n{_outline(document)}\n\n{body}",
        schema=Summary, effort="low", max_output_tokens=800)


class RevealImagePick(BaseModel):
    question_number: int
    image_number: int = Field(description="1-based image number that directly illustrates the answer, or 0 for NONE.")


class RevealImageBatch(BaseModel):
    picks: list[RevealImagePick]


_REVEAL_MATCH_SYSTEM = """You decide whether a visual (chart, map, or diagram) should appear on the Answer Reveal screen after a trivia question is answered, to help the player visually see the answer.

Be VERY strict: showing a loosely related chart is confusing and much worse than showing NO image at all (0).
Return an image_number (1..N) ONLY if that image DIRECTLY depicts, plots, labels, or proves the EXACT fact or concept asked in the question and its answer.
Return 0 (NONE) if the image only shares a person's name, entity, section, or broad topic without actually showing the answer.

Examples:
- Question: "What nickname did the CEO use for the annual retreat?" Answer: "Camp Northstar" | Image: "Bar chart of annual revenue by region" -> 0 (the chart plots revenue, NOT the retreat nickname).
- Question: "Which safety rule applies near the loading dock?" Answer: "Wear high-visibility vests" | Image: "Timeline of factory openings" -> 0.
- Question: "Which region led Q3 revenue?" Answer: "Southwest at $52.6M" | Image: "Bar chart of Q3 revenue by region showing Southwest: $52.6M" -> MATCH.
- Question: "Where did the mission enter lunar orbit?" Answer: "Far side of the Moon" | Image: "Diagram of Earth-to-Moon trajectory labeling Lunar Orbit Insertion on the far side" -> MATCH.

Evaluate every numbered question."""


async def match_reveal_images(
    questions: list[dict],
    images: list[dict],
    *,
    batch_size: int = 18,
    max_per_image: int = 2,
) -> dict[str, str]:
    """Return {question_id: image_id} for text/table MCQs whose answer is directly illustrated by an image."""
    if not questions or not images:
        return {}
    img_lines = []
    for idx, img in enumerate(images, start=1):
        label = " — ".join(filter(None, [img.get("caption"), img.get("description")]))
        ctx = (img.get("context_text") or "").strip().replace("\n", " ")[:180]
        extra = f" | Context: {ctx}" if ctx else ""
        img_lines.append(f"IMAGE {idx}: {label[:320]}{extra}")
    visuals_block = "AVAILABLE VISUALS:\n" + "\n".join(img_lines)

    matched: dict[str, str] = {}
    uses_by_img: dict[str, int] = {}
    for start in range(0, len(questions), batch_size):
        batch = questions[start:start + batch_size]
        q_lines = []
        for idx, q in enumerate(batch, start=1):
            ans = q["options"][q["correct_index"]] if q.get("options") and q.get("correct_index") is not None else ""
            parts = [f"Q{idx}: {q['question_text']} | Answer: {ans}"]
            if q.get("heading"):
                parts.append(f"Section: {q['heading']}")
            if q.get("explanation"):
                parts.append(f"Note: {str(q['explanation'])[:160]}")
            elif q.get("source_quote"):
                parts.append(f"Quote: {str(q['source_quote'])[:160]}")
            q_lines.append(" | ".join(parts))

        user_prompt = visuals_block + "\n\nQUESTIONS:\n" + "\n".join(q_lines)
        try:
            result = await llm.parse(
                model=config.model_fast,
                system=_REVEAL_MATCH_SYSTEM,
                user=user_prompt,
                schema=RevealImageBatch,
                effort="low",
                max_output_tokens=2000,
            )
        except Exception as exc:
            log.warning("could not match reveal images (batch %d): %s", start // batch_size + 1, exc)
            continue

        for pick in result.picks:
            if 1 <= pick.question_number <= len(batch) and 1 <= pick.image_number <= len(images):
                img_id = images[pick.image_number - 1]["id"]
                if uses_by_img.get(img_id, 0) >= max_per_image:
                    continue
                matched[batch[pick.question_number - 1]["id"]] = img_id
                uses_by_img[img_id] = uses_by_img.get(img_id, 0) + 1
    return matched


class VideoMatchPick(BaseModel):
    clip_number: int
    question_numbers: list[int] = Field(description="Up to 2 1-based question numbers that best match this video clip (empty list if none fit).")


class VideoMatchBatch(BaseModel):
    picks: list[VideoMatchPick]


_VIDEO_MATCH_SYSTEM = """You match YouTube video clips found in a document section to the 1 or 2 multiple-choice questions from that section that the video clip best illustrates as an Answer Reveal bonus.

Rules:
- Prefer questions whose exact event, moment, person, or concept matches the video title.
- Assign at most 2 questions per clip, and do not assign the same question to multiple clips.
- If a clip does not match any question well, return an empty list for that clip."""


async def match_section_videos(questions: list[dict], clips: list[dict]) -> dict[str, dict]:
    """Return {question_id: clip_dict} pairing each section's YouTube clips to its most relevant MCQs."""
    if not questions or not clips:
        return {}
    clip_lines = [f"CLIP {i}: {c['title']}" for i, c in enumerate(clips, start=1)]
    q_lines = []
    for i, q in enumerate(questions, start=1):
        ans = q["options"][q["correct_index"]] if q.get("options") and q.get("correct_index") is not None else ""
        note = f" | Note: {str(q['explanation'])[:140]}" if q.get("explanation") else ""
        q_lines.append(f"Q{i}: {q['question_text']} | Answer: {ans}{note}")
    try:
        result = await llm.parse(
            model=config.model_fast,
            system=_VIDEO_MATCH_SYSTEM,
            user="CLIPS:\n" + "\n".join(clip_lines) + "\n\nQUESTIONS:\n" + "\n".join(q_lines),
            schema=VideoMatchBatch,
            effort="low",
            max_output_tokens=1000,
        )
    except Exception as exc:
        log.warning("video semantic matching failed (%s); falling back to order", exc)
        out: dict[str, dict] = {}
        idx = 0
        for clip in clips:
            for q in questions[idx:idx + 2]:
                out[q["id"]] = clip
            idx += 2
        return out

    matched: dict[str, dict] = {}
    for pick in result.picks:
        if not (1 <= pick.clip_number <= len(clips)):
            continue
        clip = clips[pick.clip_number - 1]
        assigned = 0
        for q_num in pick.question_numbers:
            if 1 <= q_num <= len(questions) and assigned < 2:
                qid = questions[q_num - 1]["id"]
                if qid not in matched:
                    matched[qid] = clip
                    assigned += 1
    return matched
