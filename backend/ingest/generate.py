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
    importance: int = Field(
        default=3, ge=1, le=5,
        description="1-5: 5=core takeaway/mechanism/decision everyone must understand, 4=important concept/finding/tradeoff, 3=solid supporting fact, 1-2=obscure minutiae/hyperparameter/citation trivia",
    )
    problems: list[Literal[
        "ambiguous", "multiple_correct", "not_answerable", "leaks_answer",
        "refers_to_passage", "guessable", "not_readable", "trivial",
    ]]
    critique: str = Field(
        default="",
        description="If there is a fixable flaw (weak distractor, slight ambiguity, answer leak), one crisp sentence explaining how to fix it; empty if sound or unfixable.",
    )


class LearningObjective(BaseModel):
    section_index: int = Field(description="0-based index of the primary section this objective belongs to")
    concept: str = Field(description="Specific core concept, mechanism, finding, decision, or tradeoff to test")
    why_it_matters: str = Field(description="One short sentence on why a careful reader must understand this")
    target_difficulty: Difficulty = "medium"
    target_skill: Skill = "understanding"
    search_query: str = Field(description="5-12 word search query using exact terms from the document to locate the best passages")


class ComprehensionBlueprint(BaseModel):
    core_thesis: str = Field(description="One or two sentences summarizing the central thesis/purpose of the whole document")
    objectives: list[LearningObjective]


class RevisedMCQs(BaseModel):
    questions: list[MCQDraft]


class BlindAttempt(BaseModel):
    number: int
    guess_index: int = Field(description="0-based index of the option you think is correct without having read the document")
    confidence: Literal["high", "medium", "low"]
    reason: Literal["common_sense", "option_pattern", "keyword_clue", "pure_guess"]
    clue: str = Field(
        default="",
        description="If confidence is high/medium, a short note on what wording clue or obvious distractor gave it away",
    )


class BlindTestResult(BaseModel):
    attempts: list[BlindAttempt]


class StatementCheck(BaseModel):
    number: int
    verdict: Literal["true", "false", "unclear"]
    importance: int = Field(
        default=3, ge=1, le=5,
        description="1-5: 5=core takeaway, 4=important finding/concept, 3=solid supporting fact, 1-2=trivial minutiae",
    )


class EditorialSelection(BaseModel):
    keep_numbers: list[int] = Field(
        description="1-based numbers of the strongest, most insightful, non-overlapping questions to keep."
    )


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
- Tests genuine comprehension of what ACTUALLY MATTERS in this document: a core thesis, key mechanism, major decision, primary finding, cause-and-effect relationship, tradeoff, risk, recommendation, or headline comparison. Ask yourself: "Would someone who truly understood this document know this?"
- NEVER wastes a question on trivial minutiae: skip obscure hyperparameters, minor ablation decimals, incidental author/citation names, passing examples, formula variable symbols, formatting, figure/table numbers, page layout, or appendix boilerplate.
- Can be answered by someone who read and understood the document carefully and cannot be answered by someone who did not: not by common sense, and not from the wording of the options.
- Has exactly one option that is correct according to the document. The three wrong options are wrong according to the document yet believable: the same kind of thing, the same format, precision, and unit, similar length and magnitude. For numbers use nearby values with matching decimal precision. For names use other names of the same kind. No joke options, no "all of the above", no "none of the above".
- Stands on its own. Name the specific thing you are asking about ("the Phoenix cross-dock", "Apollo 11's Eagle lander"). Never write "the passage", "this section", "the text", "the excerpt" or "mentioned above". Use the document's actual topic rather than calling a research paper, sports guide, or history piece "the report".
- Does not contain or hint at its own answer.
- Strongly prefer understanding over look-up: WHY something works or failed, WHAT problem a design solves, WHAT tradeoff or consequence follows, HOW two findings relate. Label each question honestly as "understanding" or "recall". Aim for at least 65% "understanding" when the passage allows it.
- For tables and chart data: test the headline pattern, biggest contrast, or takeaway — not a random buried cell.

title
Give each question a short punny or wry title of 2 to 5 words, the way a pub quiz names its questions. The title must NEVER contain or give away the answer. The question itself stays plain and precise; the title carries the humour.

source_quote
Copy, word for word, the sentence or table row from the passage that proves the correct answer (at most 300 characters).

explanation
One plain sentence the player sees after answering: state the correct fact, and the "so what" if the passage gives one. Do not write "according to".

difficulty (IMPORTANT — write a true easy → medium → hard ladder!)
Games start easy and ramp up so beginners build confidence while careful readers still get tested at the end. Across the questions you write for a passage, aim for roughly 35% easy, 45% medium, and 20% hard:
- easy: a clear, memorable headline takeaway, core goal, main rule, or signature finding anyone who read the document should immediately recognize. Plain wording, distinct believable choices — NO trick-number traps (like 75,125 vs 76,125) on easy questions.
- medium: a meaningful mechanism, comparison, cause-and-effect, or key result that rewards attentive reading.
- hard: synthesis of two ideas, a nuanced tradeoff/limitation, or an important quantitative comparison.

Fact or Fake statements
Also write short statements for a rapid-fire round where players call each FACT or FAKE.
- Test real takeaways and claims from the document, not trivial numbers or side notes.
- A FACT restates something important from the passage in fresh words.
- A FAKE changes exactly one important detail (a cause, direction, capability, limitation, or headline number) so it is plainly false according to the document. It must not be a trick of wording and must not be something that could also be true.
- One sentence, at most 140 characters, readable in three seconds.
- explanation gives the true fact in one sentence.
- Mix facts and fakes.

How many (QUALITY OVER QUANTITY)
Write AT MOST the requested number of questions and statements — fewer is BETTER when only 1 or 2 genuinely important ideas live in this passage. If the passage is appendix minutiae, hyperparameter tables, equation derivations, related-work citations, boilerplate, or low-value filler, return EMPTY lists."""

CHECKER_SYSTEM = """You are the strict quality and relevance editor for a trivia quiz designed to verify that players truly understood a document. Ambiguous questions and trivial minutiae are both unacceptable.

You get the main passage, some related passages from elsewhere in the same document, and candidate questions (including their punny titles) WITHOUT the answer key.

For each multiple-choice question
- answer_index: the 0-based index of the option the document supports. Use -1 if no option is clearly supported.
- importance (1 to 5):
  - 5: Core takeaway, central mechanism, major finding, or key decision everyone who read the document should understand.
  - 4: Important supporting concept, comparison, tradeoff, limitation, or headline metric.
  - 3: Solid, worthwhile detail that genuinely reflects reading the document.
  - 2: Minor, narrow, or forgettable detail (incidental table cell, minor sub-metric, passing example, narrow setup detail).
  - 1: Pure trivia / minutiae (obscure hyperparameter, formula symbol, citation name, formatting, or filler).
- problems: list any that apply
  - ambiguous: the question can reasonably be read in two ways
  - multiple_correct: more than one option is defensible given ALL the passages
  - not_answerable: the passages do not settle it
  - leaks_answer: the wording of the question, its title, or the options gives the answer away
  - refers_to_passage: it says "the passage", "this section", "above" or similar instead of standing on its own
  - guessable: someone who never read the document could easily deduce the answer from obvious wording clues, joke distractors, conspicuous precision differences, or universal common sense (do NOT flag a legitimate easy question about a real headline fact in the document as guessable just because it is approachable)
  - trivial: asks about obscure minutiae, hyperparameters, formula notation, citation names, or low-value trivia rather than genuine comprehension of the document
  Leave the list empty when the question is sound and worthwhile.
- critique: if the core concept is worthwhile (importance >= 3) but a flaw is fixable (e.g. one weak distractor, slight wording ambiguity, title/stem leak), give ONE crisp sentence explaining how to fix it; otherwise leave empty.

For each statement
- verdict: "true" if the document clearly makes it true, "false" if the document clearly makes it false, "unclear" if it cannot be decided or is only loosely worded.
- importance (1 to 5): same 1-5 scale (rate <=2 for trivial/obscure details).

Answer every numbered item."""

PLANNER_SYSTEM = """You are a senior curriculum designer planning a comprehension quiz for a reading document (a report, research paper, guide, briefing, or handbook).

Given the document's title, numbered sections with excerpts, and key terms, design a Comprehension Blueprint of the MOST IMPORTANT concepts someone who truly read and understood this document must grasp.

Rules
- Cover EVERY substantive core section (skip appendices, related work citations, hyperparameter tables, and boilerplate unless the whole document is short).
- Each learning objective should target a distinct, high-value idea: a central thesis, key mechanism, major decision, primary result, cause-and-effect relationship, limitation/tradeoff, or cross-section connection.
- Mix difficulties cleanly across the blueprint: ~30% easy (headline takeaways), ~50% medium (mechanisms, comparisons, causes), ~20% hard (synthesis, tradeoffs, implications).
- Aim for at least 70% "understanding" objectives rather than bare memorization.
- Write each search_query using concrete domain terms from the document so semantic search retrieves the exact right supporting passages (including cross-section connections when relevant).
- Return between 6 and 16 objectives (scaled to document size; fewer for short docs, up to 16 for long multi-chapter docs)."""

REVISER_SYSTEM = """You are an expert quiz editor repairing specific flaws in multiple-choice questions while keeping their high-value comprehension concept intact.

You are given the source passages and one or more flawed questions, each with a specific critique (from either a grounded accuracy checker or a closed-book guessability test).

For each question, rewrite it so that:
- The exact flaw in the critique is completely eliminated (e.g. replace obvious/weak distractors with plausible wrong options drawn from real concepts/numbers/terms in the document, remove ambiguity, eliminate keyword or length tells, remove references to "the passage").
- The question remains 100% grounded in the provided passages, with exactly one unambiguous correct answer and a verbatim source_quote from the passages.
- Options stay matched in format, precision, and similar length, with a witty non-leaking 2-5 word title.
- Keep the same number of questions in order."""

CLOSED_BOOK_SYSTEM = """You are a clever quiz player taking a multiple-choice test WITHOUT having read the source document.

Your job is to stress-test whether questions can be cracked by general common sense, test-taking tricks, or weak distractors alone.
For each numbered question:
- guess_index: the 0-based index of the option you would pick.
- confidence:
  - "high" ONLY if you are genuinely confident you cracked it without reading the document (because of universal common sense, an obvious giveaway option, a wording clue, or joke/implausible distractors).
  - "medium" if you have a noticeable hunch from phrasing/patterns.
  - "low" if all four options look equally plausible to an outsider and you are basically guessing.
- reason: "common_sense" | "option_pattern" | "keyword_clue" | "pure_guess".
- clue: if confidence is "high" or "medium", one short sentence stating exactly what gave it away so an editor can harden the distractors.

Be honest — do NOT claim "high" confidence on a genuinely document-specific question where you are really guessing."""

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

MAX_CHUNKS_PER_PASS = 28     # cap writer/checker jobs so huge PDFs focus on core ideas, not every appendix paragraph
MAX_IMAGES_PER_PASS = 12     # cap vision jobs so screenshot-heavy PDFs only examine top charts/figures
DEFAULT_MAX_MCQS = 45        # ~4-5 full games of high-signal comprehension MCQs (instead of hundreds of trivia fillers)
DEFAULT_MAX_STATEMENTS = 18  # ~3 full Lightning Rounds of high-signal Fact-or-Fake statements
MAX_IMAGE_MCQS = 8           # keep picture rounds focused on the very clearest charts/diagrams
MIN_IMPORTANCE = 3           # checker 1-5 importance floor (1-2 = obscure minutiae/trivia)

_LOW_VALUE_HEADING = re.compile(
    r"\b(appendix|related work|prior work|background literature|hyperparameter|implementation detail|"
    r"notation|experimental setup|training detail|ablation detail|prompt template|qualitative example|"
    r"supplementary|ethics statement|broader impact|reproducibility|author contribution|acknowledg)\b",
    re.I,
)
_CORE_HEADING = re.compile(
    r"\b(abstract|executive summary|summary|overview|introduction|key finding|result|conclusion|"
    r"discussion|method|architecture|approach|design|framework|strategy|recommendation|finding|"
    r"takeaway|evaluation|analysis|scaling|impact|policy|rule|guideline)\b",
    re.I,
)
_INFORMATIVE_VISUAL_HINT = re.compile(
    r"\b(figure|fig\.|chart|graph|diagram|architecture|pipeline|overview|comparison|scaling|results|table|map|timeline)\b",
    re.I,
)


def _quota(chunk: Chunk, *, compact: bool = False) -> tuple[int, int]:
    tokens = chunk.token_count
    if chunk.kind in ("table", "chart"):
        if compact:
            return (2 if tokens >= 90 else 1), (1 if tokens >= 90 else 0)
        return (2 if tokens >= 70 else 1), (1 if tokens >= 70 else 1)
    if tokens < 45:
        return 0, 0
    if compact:
        if tokens < 110:
            return 1, 0
        return 2, 1
    if tokens < 100:
        return 1, 1
    if tokens < 230:
        return 2, 1
    return 3, 2


def _chunk_priority(chunk: Chunk, section_heading: str, existing_count: int) -> float:
    """Rank passages by how likely they carry core document ideas rather than appendix/equation minutiae."""
    if chunk.kind == "text" and chunk.token_count < 45:
        return -100.0
    heading_hay = f"{section_heading} {chunk.heading_path}"
    score = 10.0
    if _CORE_HEADING.search(heading_hay):
        score += 4.5
    if _LOW_VALUE_HEADING.search(heading_hay):
        score -= 7.5
    # Sweet-spot prose length (rich concepts, not stub fragments)
    if chunk.kind == "text":
        if 140 <= chunk.token_count <= 460:
            score += 2.0
        elif chunk.token_count < 85:
            score -= 2.0
    elif chunk.kind == "chart":
        score += 2.5
    elif chunk.kind == "table":
        score += 0.5 if chunk.token_count <= 450 else -1.5
    # Down-rank symbol/equation/number walls (common in research appendices & hyperparameter dumps)
    text = chunk.text or ""
    if text:
        alpha_ratio = sum(ch.isalpha() for ch in text) / max(1, len(text))
        if alpha_ratio < 0.50:
            score -= 5.0
        elif alpha_ratio < 0.62:
            score -= 2.0
    # Prefer passages with fewer existing questions when topping up
    score -= existing_count * 4.0
    return score


def select_chunks(
    document: StructuredDocument,
    avoid_by_chunk: dict[int, list[str]] | None = None,
    limit: int = MAX_CHUNKS_PER_PASS,
) -> list[int]:
    """Pick the highest-signal passages evenly across sections so every core topic is tested without bloat."""
    avoid_by_chunk = avoid_by_chunk or {}
    eligible: list[tuple[int, int, float]] = []  # (chunk_idx, section_idx, score)
    for idx, chunk in enumerate(document.chunks):
        if chunk.kind == "text" and chunk.token_count < 45:
            continue
        sec_heading = document.sections[chunk.section].heading if 0 <= chunk.section < len(document.sections) else ""
        score = _chunk_priority(chunk, sec_heading, len(avoid_by_chunk.get(idx, [])))
        eligible.append((idx, chunk.section, score))
    if len(eligible) <= limit:
        return [idx for idx, _, _ in eligible]

    # Group by section, sort within each section by signal score, and prioritize core sections ahead of appendices.
    by_section: dict[int, list[tuple[int, float]]] = {}
    for idx, sec, score in eligible:
        by_section.setdefault(sec, []).append((idx, score))
    for items in by_section.values():
        items.sort(key=lambda pair: (-pair[1], pair[0]))

    def _sec_rank(sec: int) -> tuple[int, int]:
        heading = document.sections[sec].heading if 0 <= sec < len(document.sections) else ""
        return (1 if _LOW_VALUE_HEADING.search(heading) else 0, sec)

    section_order = sorted(by_section, key=_sec_rank)
    picked: list[int] = []
    # Round-robin across core sections first so every main chapter gets coverage before any section takes a 3rd chunk.
    core_sections = [s for s in section_order if _sec_rank(s)[0] == 0]
    other_sections = [s for s in section_order if _sec_rank(s)[0] == 1]
    for group in (core_sections, other_sections):
        while len(picked) < limit and any(by_section[s] for s in group):
            for s in group:
                if by_section[s] and len(picked) < limit:
                    chunk_idx, _ = by_section[s].pop(0)
                    picked.append(chunk_idx)
    return sorted(picked)


def select_images(document: StructuredDocument, limit: int = MAX_IMAGES_PER_PASS) -> list[ImageItem]:
    """Pick the most informative charts/diagrams spread across sections instead of running vision on hundreds of frames."""
    if len(document.images) <= limit:
        return list(document.images)

    def _img_score(img: ImageItem) -> float:
        sec_heading = document.sections[img.section].heading if 0 <= img.section < len(document.sections) else ""
        score = 0.0
        if img.origin == "chart":
            score += 8.0
        if img.caption:
            score += 5.0
        if _INFORMATIVE_VISUAL_HINT.search(f"{img.caption or ''} {img.context}"):
            score += 3.5
        if _LOW_VALUE_HEADING.search(sec_heading):
            score -= 6.0
        if _CORE_HEADING.search(sec_heading):
            score += 2.0
        area = (img.width or 0) * (img.height or 0)
        if area >= 350_000:
            score += 2.0
        elif area < 120_000:
            score -= 2.0
        return score

    scored: list[tuple[ImageItem, float]] = [(img, _img_score(img)) for img in document.images]
    picked: list[ImageItem] = []
    per_section: dict[int, int] = {}
    remaining = list(scored)
    while remaining and len(picked) < limit:
        # Greedily pick the highest-value visual, applying a gentle per-section diversity penalty so real charts/figures
        # always beat uncaptioned screenshots while equally strong visuals still spread across sections.
        best_idx = max(
            range(len(remaining)),
            key=lambda i: (remaining[i][1] - per_section.get(remaining[i][0].section, 0) * 2.5, -remaining[i][0].ordinal),
        )
        img, _ = remaining.pop(best_idx)
        picked.append(img)
        per_section[img.section] = per_section.get(img.section, 0) + 1
    picked.sort(key=lambda img: img.ordinal)
    return picked


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


_FATAL = {"ambiguous", "multiple_correct", "not_answerable", "leaks_answer", "refers_to_passage", "guessable", "not_readable", "trivial"}
_FIXABLE_CHECK_PROBLEMS = {"ambiguous", "multiple_correct", "leaks_answer", "refers_to_passage", "guessable"}
_FIXABLE_MECH_PROBLEMS = {
    "correct option is conspicuously longer",
    "correct option is conspicuously more precise",
    "title gives away the answer",
    "refers to 'the passage'",
    "answer appears in the question",
}


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
        elif check.importance < MIN_IMPORTANCE:
            report.reject("too minor / low importance")
        else:
            accepted.append((draft, {"checked": True, "importance": check.importance}))
    return accepted


def _split_checked_mcqs(
    mcqs: list[tuple[MCQDraft, list[str], int]],
    checks: list[MCQCheck],
    report: GenerationReport,
) -> tuple[list[tuple[MCQDraft, dict]], list[tuple[MCQDraft, str]]]:
    """Partition checked MCQs into (accepted, fixable_for_reviser), rejecting unfixable/trivial ones immediately."""
    by_number = {c.number: c for c in checks}
    accepted: list[tuple[MCQDraft, dict]] = []
    fixable: list[tuple[MCQDraft, str]] = []
    for number, (draft, _, correct_at) in enumerate(mcqs, start=1):
        check = by_number.get(number)
        if check is None:
            report.reject("checker skipped it")
            continue
        fatal = _FATAL.intersection(check.problems)
        if check.answer_index == correct_at and not fatal and check.importance >= MIN_IMPORTANCE:
            accepted.append((draft, {"checked": True, "importance": check.importance}))
            continue
        # Only revise worthwhile concepts with fixable flaws (never rescue trivial or unanswerable questions)
        unfixable = {"trivial", "not_answerable", "not_readable"}.intersection(fatal)
        if check.importance >= MIN_IMPORTANCE and not unfixable:
            if fatal and fatal.issubset(_FIXABLE_CHECK_PROBLEMS):
                reason = check.critique.strip() or f"Fix flagged issue(s): {', '.join(sorted(fatal))}."
                fixable.append((draft, reason))
                continue
            if check.answer_index != correct_at and check.critique.strip():
                fixable.append((draft, f"Independent checker picked a different option: {check.critique.strip()}"))
                continue
        if check.answer_index != correct_at:
            report.reject("checker chose a different answer")
        elif fatal:
            report.reject("checker flagged: " + ", ".join(sorted(fatal)))
        else:
            report.reject("too minor / low importance")
    return accepted, fixable


async def _revise_mcqs(
    document: StructuredDocument,
    source_block: str,
    fixable: list[tuple[MCQDraft, str]],
) -> list[MCQDraft]:
    """Ask the Reviser agent to repair specific flaws (weak distractors, ambiguity, tells) in-place."""
    if not fixable:
        return []
    items: list[str] = []
    for idx, (draft, critique) in enumerate(fixable, start=1):
        items.append(
            f"ITEM {idx}\n"
            f"Title: {draft.title}\n"
            f"Question: {draft.question}\n"
            f"Correct answer: {draft.correct_answer}\n"
            f"Distractors: {' | '.join(draft.distractors)}\n"
            f"Quote: {draft.source_quote}\n"
            f"Explanation: {draft.explanation}\n"
            f"Difficulty: {draft.difficulty} | Skill: {draft.skill}\n"
            f"CRITIQUE TO FIX: {critique}"
        )
    try:
        revised = await llm.parse(
            model=config.model_writer,
            system=REVISER_SYSTEM,
            user=(
                f"Document: {document.title}\n\n"
                f"SOURCE PASSAGES\n<<<\n{source_block[:5500]}\n>>>\n\n"
                f"QUESTIONS TO REVISE ({len(fixable)} total — return {len(fixable)} improved questions):\n\n"
                + "\n\n".join(items)
            ),
            schema=RevisedMCQs,
            effort="low",
            max_output_tokens=3500,
        )
        return revised.questions[:len(fixable)]
    except Exception as exc:  # noqa: BLE001
        log.warning("reviser agent failed: %s", exc)
        return []


async def _verify_and_revise(
    document: StructuredDocument,
    primary_chunk: Chunk,
    supporting_chunks: list[Chunk],
    raw_mcqs: list[MCQDraft],
    raw_statements: list[StatementDraft],
    report: GenerationReport,
    *,
    primary_index: int,
    allow_revise: bool = True,
) -> list[ReadyQuestion]:
    """Run mechanical guardrails -> Grounded Checker -> 1-shot Reviser -> Re-Verify loop."""
    combined_grounding = "\n\n".join([primary_chunk.text, *(c.text for c in supporting_chunks)])

    mcqs: list[tuple[MCQDraft, list[str], int]] = []
    mech_fixable: list[tuple[MCQDraft, str]] = []
    for draft in raw_mcqs:
        problem = mcq_problem(draft, combined_grounding)
        if problem:
            if allow_revise and problem in _FIXABLE_MECH_PROBLEMS:
                mech_fixable.append((draft, f"Mechanical check failed: {problem}."))
            else:
                report.reject(problem)
        else:
            mcqs.append((draft, *_shuffle(draft)))

    statements: list[StatementDraft] = []
    for statement in raw_statements:
        problem = statement_problem(statement, combined_grounding)
        if problem:
            report.reject(problem)
        else:
            statements.append(statement)

    if not mcqs and not statements and not mech_fixable:
        return []

    related_block = "\n\n".join(f"({c.heading_path})\n{c.text[:1400]}" for c in supporting_chunks) or "(none)"
    source_block = (
        f"MAIN PASSAGE ({primary_chunk.heading_path})\n{primary_chunk.text}\n\n"
        f"SUPPORTING PASSAGES\n{related_block}"
    )

    accepted_mcqs: list[tuple[MCQDraft, dict]] = []
    fixable: list[tuple[MCQDraft, str]] = list(mech_fixable)
    stmt_checks: dict[int, StatementCheck] = {}

    if mcqs or statements:
        user = "\n\n".join([
            f"Report: {document.title}",
            f"MAIN PASSAGE ({primary_chunk.heading_path})\n<<<\n{primary_chunk.text}\n>>>",
            "RELATED PASSAGES FROM ELSEWHERE IN THE REPORT\n" + related_block,
            _present(mcqs, statements),
        ])
        check = await llm.parse(model=config.model_checker, system=CHECKER_SYSTEM, user=user,
                                schema=CheckResult, effort="low", max_output_tokens=3000)
        if allow_revise:
            ok_first, check_fixable = _split_checked_mcqs(mcqs, check.questions, report)
            accepted_mcqs.extend(ok_first)
            fixable.extend(check_fixable)
        else:
            accepted_mcqs.extend(_accept_mcqs(mcqs, check.questions, report))
        stmt_checks = {c.number: c for c in check.statements}

    # Agentic critique -> revise -> re-verify pass (at most 3 items per job, 1 round)
    if allow_revise and fixable:
        to_fix = fixable[:3]
        for _ in range(len(fixable) - len(to_fix)):
            report.reject("skipped excess revision")
        revised_drafts = await _revise_mcqs(document, source_block, to_fix)
        rev_mcqs: list[tuple[MCQDraft, list[str], int]] = []
        for draft in revised_drafts:
            problem = mcq_problem(draft, combined_grounding)
            if problem:
                report.reject(f"revision still failed: {problem}")
            else:
                rev_mcqs.append((draft, *_shuffle(draft)))
        if rev_mcqs:
            rev_user = "\n\n".join([
                f"Report: {document.title}",
                f"MAIN PASSAGE ({primary_chunk.heading_path})\n<<<\n{primary_chunk.text}\n>>>",
                "RELATED PASSAGES FROM ELSEWHERE IN THE REPORT\n" + related_block,
                _present(rev_mcqs, []),
            ])
            try:
                rev_check = await llm.parse(model=config.model_checker, system=CHECKER_SYSTEM, user=rev_user,
                                            schema=CheckResult, effort="low", max_output_tokens=2000)
                for draft, quality in _accept_mcqs(rev_mcqs, rev_check.questions, report):
                    accepted_mcqs.append((draft, {**quality, "revised": True}))
            except Exception as exc:  # noqa: BLE001
                log.warning("revision re-check failed: %s", exc)
                for _ in rev_mcqs:
                    report.reject("revision check failed (AI error)")
        elif not revised_drafts:
            for _ in to_fix:
                report.reject("revision produced no replacement")

    source_type = primary_chunk.kind if primary_chunk.kind in ("table", "chart") else "text"
    ready: list[ReadyQuestion] = []
    for draft, quality in accepted_mcqs:
        ready.append(ReadyQuestion(
            kind="mcq", source_type=source_type, chunk_index=primary_index, section_index=primary_chunk.section,
            title=draft.title.strip()[:60] or None, question_text=draft.question.strip(),
            options=[draft.correct_answer.strip(), *[d.strip() for d in draft.distractors]], correct_index=0,
            explanation=draft.explanation.strip(), source_quote=draft.source_quote.strip()[:400],
            skill=draft.skill, difficulty=draft.difficulty, quality=quality))
    for number, statement in enumerate(statements, start=1):
        sc = stmt_checks.get(number)
        verdict = sc.verdict if sc else None
        if verdict != ("true" if statement.is_true else "false"):
            report.reject("checker disagreed on a fact/fake statement")
            continue
        if sc and sc.importance < MIN_IMPORTANCE:
            report.reject("too minor / low importance")
            continue
        ready.append(ReadyQuestion(
            kind="fact_fake", source_type=source_type, chunk_index=primary_index, section_index=primary_chunk.section,
            question_text=statement.statement.strip(), is_true=statement.is_true,
            explanation=statement.explanation.strip(), source_quote=statement.source_quote.strip()[:400],
            difficulty=statement.difficulty, quality={"checked": True, "importance": sc.importance if sc else 3}))
    return ready


async def _plan_blueprint(
    document: StructuredDocument,
    glossary: str,
    avoid_questions: list[str],
    *,
    max_objectives: int = 16,
) -> ComprehensionBlueprint | None:
    """Planner Agent: read section digests across the whole document and produce core learning objectives."""
    if len(document.chunks) <= 4 or not document.sections:
        return None
    core_sections = [s for s in document.sections if not _LOW_VALUE_HEADING.search(s.heading)]
    active_sections = core_sections or list(document.sections)
    target_count = min(max_objectives, max(6, len(active_sections) * 2))
    if avoid_questions:
        target_count = min(8, max(4, len(active_sections)))

    section_digests: list[str] = []
    for sec in document.sections:
        sec_chunks = [c for c in document.chunks if c.section == sec.ordinal and c.token_count >= 40]
        if not sec_chunks:
            continue
        # Show the opening chunk plus the highest-priority chunk in this section so the Planner sees the true arc.
        ranked = sorted(sec_chunks, key=lambda c: -_chunk_priority(c, sec.heading, 0))
        picked_excerpts: list[Chunk] = [sec_chunks[0]]
        if ranked[0] is not sec_chunks[0]:
            picked_excerpts.append(ranked[0])
        excerpt_text = "\n...\n".join(c.text[:480] for c in picked_excerpts[:2])
        tag = " [appendix/supporting]" if _LOW_VALUE_HEADING.search(sec.heading) else ""
        section_digests.append(f"[Section {sec.ordinal}] {sec.heading}{tag}\n{excerpt_text}")

    parts = [
        f"Document: {document.title}",
        f"Target number of learning objectives: {target_count}",
    ]
    if glossary:
        parts.append(f"Key terms/entities: {glossary}")
    if avoid_questions:
        parts.append(
            "Existing quiz questions (plan NEW, uncovered angles and concepts):\n"
            + "\n".join(f"- {q}" for q in avoid_questions[:25])
        )
    parts.append("SECTIONS & EXCERPTS\n\n" + "\n\n".join(section_digests)[:14000])
    try:
        blueprint = await llm.parse(
            model=config.model_writer,
            system=PLANNER_SYSTEM,
            user="\n\n".join(parts),
            schema=ComprehensionBlueprint,
            effort="low",
            max_output_tokens=2800,
        )
        valid = [
            obj for obj in blueprint.objectives
            if 0 <= obj.section_index < len(document.sections) and obj.concept.strip()
        ]
        if not valid:
            return None
        blueprint.objectives = valid[:max_objectives]
        return blueprint
    except Exception as exc:  # noqa: BLE001
        log.warning("comprehension planner unavailable (%s); falling back to passage selection", exc)
        return None


def _retrieve_for_objective(
    document: StructuredDocument,
    chunk_embeddings: np.ndarray,
    objective: LearningObjective,
    query_vector: np.ndarray,
    avoid_by_chunk: dict[int, list[str]],
) -> tuple[int, list[int]] | None:
    """Find the best primary chunk in the objective's section plus up to 2 cross-section supporting chunks."""
    if not document.chunks or chunk_embeddings.size == 0:
        return None
    scores = chunk_embeddings @ query_vector
    sec_candidates = [
        i for i, c in enumerate(document.chunks)
        if c.section == objective.section_index and (c.kind != "text" or c.token_count >= 45)
    ]
    if sec_candidates:
        primary_idx = max(sec_candidates, key=lambda i: float(scores[i]) - 0.04 * len(avoid_by_chunk.get(i, [])))
    else:
        primary_idx = int(np.argmax(scores))

    # Pick up to 2 highest-similarity supporting passages (can span other sections for synthesis!)
    order = np.argsort(-scores)
    supporting: list[int] = []
    for raw_i in order:
        i = int(raw_i)
        if i == primary_idx:
            continue
        if float(scores[i]) < 0.30:
            break
        if document.chunks[i].kind == "text" and document.chunks[i].token_count < 45:
            continue
        supporting.append(i)
        if len(supporting) >= 2:
            break
    return primary_idx, supporting


async def _questions_for_objective(
    document: StructuredDocument,
    blueprint: ComprehensionBlueprint,
    objective: LearningObjective,
    primary_idx: int,
    supporting_indices: list[int],
    avoid: list[str],
    report: GenerationReport,
    glossary: str = "",
    max_q: int = 3,
    max_s: int = 1,
) -> list[ReadyQuestion]:
    """Targeted Writer -> Grounded Checker -> Reviser loop for one Learning Objective."""
    primary_chunk = document.chunks[primary_idx]
    supporting_chunks = [document.chunks[i] for i in supporting_indices]

    parts = [
        f"Document: {document.title}",
        f"Core document thesis: {blueprint.core_thesis}",
        f"Outline:\n{_outline(document)}",
        (
            f"ASSIGNED LEARNING OBJECTIVE\n"
            f"- Concept to test: {objective.concept}\n"
            f"- Why it matters: {objective.why_it_matters}\n"
            f"- Preferred skill: {objective.target_skill} | Preferred difficulty: {objective.target_difficulty}"
        ),
    ]
    if glossary:
        parts.append(f"Key document entities/terms: {glossary}")
    if avoid:
        parts.append("Already in the quiz – write about DIFFERENT facts:\n" + "\n".join(f"- {a}" for a in avoid[:12]))
    parts.append(
        f"Write up to {max_q} multiple-choice questions and up to {max_s} Fact-or-Fake statement(s) that directly test "
        f"this learning objective (connecting the primary and supporting passages when helpful). "
        f"Every source_quote MUST appear verbatim in one of the passages below."
    )
    parts.append(f"PRIMARY PASSAGE ({primary_chunk.heading_path})\n<<<\n{primary_chunk.text}\n>>>")
    if supporting_chunks:
        parts.append(
            "SUPPORTING PASSAGES (from elsewhere in the document — you may synthesize across these)\n"
            + "\n\n".join(f"({c.heading_path})\n<<<\n{c.text[:1400]}\n>>>" for c in supporting_chunks)
        )
    drafts = await llm.parse(
        model=config.model_writer,
        system=WRITER_SYSTEM,
        user="\n\n".join(parts),
        schema=PassageDrafts,
        effort="low",
        max_output_tokens=5000,
    )
    report.drafted += len(drafts.questions) + len(drafts.statements)
    return await _verify_and_revise(
        document,
        primary_chunk,
        supporting_chunks,
        drafts.questions[:max_q],
        drafts.statements[:max_s],
        report,
        primary_index=primary_idx,
        allow_revise=True,
    )


async def _questions_for_chunk(document: StructuredDocument, index: int, embeddings: np.ndarray,
                               avoid: list[str], report: GenerationReport, glossary: str = "",
                               compact: bool = False) -> list[ReadyQuestion]:
    chunk = document.chunks[index]
    max_q, max_s = _quota(chunk, compact=compact)
    if not max_q and not max_s:
        return []
    previous = document.chunks[index - 1] if index > 0 else None
    drafts = await _write_for_chunk(document, chunk, previous, avoid, max_q, max_s, glossary=glossary)
    report.drafted += len(drafts.questions) + len(drafts.statements)
    related = [document.chunks[i] for i, score in llm.cosine_top(embeddings[index], embeddings, 3, exclude=index)
               if score >= 0.35]
    return await _verify_and_revise(
        document,
        chunk,
        related,
        drafts.questions[:max_q],
        drafts.statements[:max_s],
        report,
        primary_index=index,
        allow_revise=True,
    )


async def _closed_book_stress_test(
    document: StructuredDocument,
    chunk_embeddings: np.ndarray,
    questions: list[ReadyQuestion],
    report: GenerationReport,
) -> list[ReadyQuestion]:
    """Closed-Book Blind Test-Taker: try answering MCQs WITHOUT the document.
    Any question cracked with high confidence by common sense or option patterns is sent to the Reviser
    to harden its distractors and re-verified — or dropped if it still cannot be hardened."""
    non_image_mcqs = [q for q in questions if q.kind == "mcq" and q.source_type != "image" and q.options]
    if not non_image_mcqs:
        return questions

    vulnerable: list[tuple[ReadyQuestion, str]] = []
    batch_size = 20
    for start in range(0, len(non_image_mcqs), batch_size):
        batch = non_image_mcqs[start:start + batch_size]
        shuffled_map: list[tuple[ReadyQuestion, list[str], int]] = []
        lines: list[str] = []
        for num, q in enumerate(batch, start=1):
            opts = list(q.options or [])
            order = list(range(len(opts)))
            random.shuffle(order)
            shuffled_opts = [opts[i] for i in order]
            correct_at = order.index(0)
            shuffled_map.append((q, shuffled_opts, correct_at))
            lines.append(f"\nQuestion {num}: {q.question_text}")
            lines += [f"  [{i}] {opt}" for i, opt in enumerate(shuffled_opts)]
        try:
            result = await llm.parse(
                model=config.model_fast,
                system=CLOSED_BOOK_SYSTEM,
                user="Answer each question WITHOUT any source document:\n" + "\n".join(lines),
                schema=BlindTestResult,
                effort="low",
                max_output_tokens=2000,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("closed-book stress test skipped for batch: %s", exc)
            continue

        by_num = {a.number: a for a in result.attempts}
        for num, (q, _, correct_at) in enumerate(shuffled_map, start=1):
            attempt = by_num.get(num)
            if (
                attempt is not None
                and attempt.guess_index == correct_at
                and attempt.confidence == "high"
                and attempt.reason != "pure_guess"
            ):
                clue = attempt.clue.strip() or f"Cracked without reading via {attempt.reason}."
                vulnerable.append((q, f"Closed-book test-taker guessed this without reading the document ({attempt.reason}): {clue} Strengthen the three distractors using real terms/concepts from the document so an outsider cannot guess it."))

    if not vulnerable:
        return questions

    # Attempt targeted distractor hardening on up to 12 vulnerable questions in parallel
    vulnerable_ids = {id(q) for q, _ in vulnerable}
    replacements: dict[int, ReadyQuestion | None] = {id(q): None for q, _ in vulnerable}

    async def harden_one(q: ReadyQuestion, critique: str) -> tuple[int, ReadyQuestion | None]:
        if q.chunk_index is None or not (0 <= q.chunk_index < len(document.chunks)):
            return id(q), None
        primary = document.chunks[q.chunk_index]
        related = [
            document.chunks[i]
            for i, score in llm.cosine_top(chunk_embeddings[q.chunk_index], chunk_embeddings, 2, exclude=q.chunk_index)
            if score >= 0.35
        ]
        draft = MCQDraft(
            title=q.title or "Check this",
            question=q.question_text,
            correct_answer=(q.options or [""])[0],
            distractors=list((q.options or [])[1:4]),
            source_quote=q.source_quote,
            explanation=q.explanation,
            skill=q.skill if q.skill in ("recall", "understanding") else "understanding",
            difficulty=q.difficulty if q.difficulty in ("easy", "medium", "hard") else "medium",
        )
        source_block = "\n\n".join([primary.text, *(c.text[:1400] for c in related)])
        revised = await _revise_mcqs(document, source_block, [(draft, critique)])
        if not revised:
            return id(q), None
        verified = await _verify_and_revise(
            document, primary, related, revised[:1], [], report,
            primary_index=q.chunk_index, allow_revise=False,
        )
        if not verified:
            return id(q), None
        hardened = verified[0]
        hardened.quality = {**(hardened.quality or {}), "hardened": True}
        return id(q), hardened

    results = await asyncio.gather(*(harden_one(q, c) for q, c in vulnerable[:12]), return_exceptions=True)
    for res in results:
        if isinstance(res, tuple):
            replacements[res[0]] = res[1]

    out: list[ReadyQuestion] = []
    for q in questions:
        qid = id(q)
        if qid not in vulnerable_ids:
            out.append(q)
            continue
        replacement = replacements.get(qid)
        if replacement is not None:
            out.append(replacement)
        else:
            report.reject("guessable without reading the document")
    return out


async def _questions_for_image(document: StructuredDocument, image: ImageItem, report: GenerationReport,
                               max_questions: int = 2) -> tuple[list[ReadyQuestion], tuple[str, str, bool]]:
    picture = llm.image_part(image.data, image.mime)
    intro = f"Report: {document.title}\n{image.context}\n\nWrite up to {max_questions} questions."
    drafts = await llm.parse(model=config.model_vision, system=IMAGE_WRITER_SYSTEM,
                             user=[llm.text_part(intro), picture], schema=ImageDrafts,
                             effort="low", max_output_tokens=4000)
    if not drafts.informative:
        return [], (drafts.kind, drafts.description.strip(), False)
    if not drafts.questions:
        return [], (drafts.kind, drafts.description.strip(), True)
    report.drafted += len(drafts.questions)
    mcqs: list[tuple[MCQDraft, list[str], int]] = []
    for draft in drafts.questions[:max_questions]:
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
            threshold: float = 0.84) -> list[ReadyQuestion]:
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


def _question_rank(q: ReadyQuestion) -> tuple:
    """Sort key putting highest-importance comprehension questions first."""
    importance = int((q.quality or {}).get("importance", 3))
    return (-importance, q.skill != "understanding", q.source_type == "table")


def _select_balanced(
    candidates: list[ReadyQuestion],
    limit: int,
    *,
    max_image: int | None = None,
) -> list[ReadyQuestion]:
    """Keep up to ``limit`` top questions while guaranteeing balanced coverage across sections."""
    if len(candidates) <= limit and (max_image is None or sum(q.source_type == "image" for q in candidates) <= max_image):
        return list(candidates)

    by_section: dict[int, list[ReadyQuestion]] = {}
    for q in candidates:
        by_section.setdefault(q.section_index, []).append(q)
    for items in by_section.values():
        items.sort(key=_question_rank)

    # Order sections by their strongest question's importance, then original document order.
    section_order = sorted(by_section, key=lambda s: (_question_rank(by_section[s][0]), s))
    kept: list[ReadyQuestion] = []
    img_count = 0
    while len(kept) < limit and any(by_section[s] for s in section_order):
        progressed = False
        for s in section_order:
            while by_section[s] and len(kept) < limit:
                cand = by_section[s].pop(0)
                if max_image is not None and cand.source_type == "image":
                    if img_count >= max_image:
                        continue
                    img_count += 1
                kept.append(cand)
                progressed = True
                break
        if not progressed:
            break
    return kept


_EDITOR_SYSTEM = """You are the chief editor curating the final question bank for a comprehension quiz on a document.

Given the document title, outline, and numbered candidate questions, pick ONLY the strongest, most insightful questions that truly verify whether someone read and understood the document.
Prioritize:
- Core ideas, key mechanisms, main findings, causes, tradeoffs, recommendations, and big-picture takeaways.
- Broad coverage across the document's main sections (avoid clustering 6 questions on one narrow detail).
- A healthy mix of easy (approachable headline takeaways), medium (solid comprehension), and hard (synthesis/tradeoffs) questions.
Drop:
- Questions that overlap in topic or test nearly the same concept.
- Narrow trivia, obscure sub-metrics, minor implementation/appendix details, or overly nitpicky recall.

Return the 1-based numbers of the questions to KEEP, up to the requested count."""


async def _editorial_select(
    document: StructuredDocument,
    candidates: list[ReadyQuestion],
    target: int,
) -> list[ReadyQuestion]:
    """Use a single fast editorial pass to pick the strongest non-overlapping questions across the whole document."""
    if len(candidates) <= target:
        return candidates
    # Pre-trim huge candidate pools to at most 2x target (sorted by importance/section) so the editorial prompt stays crisp.
    shortlist = _select_balanced(candidates, min(len(candidates), max(target * 2, target + 25)))
    lines: list[str] = []
    for idx, q in enumerate(shortlist, start=1):
        sec = document.sections[q.section_index].heading if 0 <= q.section_index < len(document.sections) else ""
        ans = q.options[0] if q.options else ("FACT" if q.is_true else "FAKE")
        imp = (q.quality or {}).get("importance", 3)
        lines.append(f"{idx}. [{sec[:40]} | {q.difficulty} | {q.skill} | imp={imp}] {q.question_text} -> {ans[:80]}")
    try:
        result = await llm.parse(
            model=config.model_fast,
            system=_EDITOR_SYSTEM,
            user=(
                f"Document: {document.title}\nOutline:\n{_outline(document)}\n\n"
                f"Pick the best {target} questions (at most {target}) from the {len(shortlist)} candidates below:\n"
                + "\n".join(lines)
            ),
            schema=EditorialSelection,
            effort="low",
            max_output_tokens=1500,
        )
        chosen: list[ReadyQuestion] = []
        seen: set[int] = set()
        for num in result.keep_numbers:
            if 1 <= num <= len(shortlist) and num not in seen:
                seen.add(num)
                chosen.append(shortlist[num - 1])
                if len(chosen) >= target:
                    break
        if len(chosen) >= max(5, target // 2):
            return chosen
    except Exception as exc:  # noqa: BLE001
        log.warning("editorial selection skipped (%s); using balanced ranking", exc)
    return _select_balanced(shortlist, target)


async def curate_bank(
    document: StructuredDocument,
    questions: list[ReadyQuestion],
    report: GenerationReport,
    *,
    max_mcqs: int = DEFAULT_MAX_MCQS,
    max_statements: int = DEFAULT_MAX_STATEMENTS,
    max_image_mcqs: int = MAX_IMAGE_MCQS,
    use_editor: bool = True,
) -> list[ReadyQuestion]:
    """Trim a large candidate pool down to a crisp, balanced, comprehension-first question bank."""
    mcqs = [q for q in questions if q.kind == "mcq"]
    statements = [q for q in questions if q.kind == "fact_fake"]

    # Enforce picture-round cap first so charts/diagrams stay selective
    image_mcqs = [q for q in mcqs if q.source_type == "image"]
    non_image_mcqs = [q for q in mcqs if q.source_type != "image"]
    if len(image_mcqs) > max_image_mcqs:
        kept_imgs = _select_balanced(image_mcqs, max_image_mcqs)
        for _ in range(len(image_mcqs) - len(kept_imgs)):
            report.reject("trimmed to keep only the strongest picture questions")
        image_mcqs = kept_imgs
    mcqs = non_image_mcqs + image_mcqs

    if len(mcqs) > max_mcqs:
        kept_mcqs = (
            await _editorial_select(document, mcqs, max_mcqs)
            if use_editor else _select_balanced(mcqs, max_mcqs, max_image=max_image_mcqs)
        )
        for _ in range(len(mcqs) - len(kept_mcqs)):
            report.reject("trimmed to keep only the strongest questions")
        mcqs = kept_mcqs

    if len(statements) > max_statements:
        kept_stmts = _select_balanced(statements, max_statements)
        for _ in range(len(statements) - len(kept_stmts)):
            report.reject("trimmed to keep only the strongest statements")
        statements = kept_stmts

    return mcqs + statements


Progress = Callable[[str, int, int], Awaitable[None]]


async def generate_bank(document: StructuredDocument, chunk_embeddings: np.ndarray, *,
                        progress: Progress | None = None, avoid_by_chunk: dict[int, list[str]] | None = None,
                        existing_embeddings: np.ndarray | None = None, include_images: bool = True,
                        max_mcqs: int = DEFAULT_MAX_MCQS, max_statements: int = DEFAULT_MAX_STATEMENTS,
                        ) -> GenerationReport:
    """Agentic 5-stage workflow:
    1. Planner Agent creates a Comprehension Blueprint of core learning objectives across the document.
    2. Targeted Writers draft questions per objective using cross-section semantic retrieval (plus top visuals).
    3. Grounded Checker + Reviser Agent critique, repair fixable flaws, and re-verify.
    4. Closed-Book Blind Test-Taker stress-tests guessability and hardens weak distractors.
    5. Semantic Dedupe + Chief Editor curates a crisp, balanced comprehension bank.
    """
    report = GenerationReport()
    avoid_by_chunk = avoid_by_chunk or {}
    all_avoid = [q for qs in avoid_by_chunk.values() for q in qs]
    glossary = _entity_glossary(document)

    if progress:
        await progress("Planning core learning objectives", 0, 1)
    blueprint = await _plan_blueprint(document, glossary, all_avoid)

    objective_jobs: list[tuple[LearningObjective, int, list[int]]] = []
    fallback_chunk_indices: list[int] = []
    if blueprint is not None and chunk_embeddings.size > 0:
        try:
            q_vecs = await llm.embed([obj.search_query or obj.concept for obj in blueprint.objectives])
            covered_sections: set[int] = set()
            for obj, q_vec in zip(blueprint.objectives, q_vecs):
                retrieved = _retrieve_for_objective(document, chunk_embeddings, obj, q_vec, avoid_by_chunk)
                if retrieved is not None:
                    primary_idx, supporting_indices = retrieved
                    objective_jobs.append((obj, primary_idx, supporting_indices))
                    covered_sections.add(document.chunks[primary_idx].section)
            # Ensure every substantive core section gets at least one passage job for full readiness coverage.
            for sec in document.sections:
                if sec.ordinal in covered_sections or _LOW_VALUE_HEADING.search(sec.heading):
                    continue
                sec_chunks = [
                    i for i, c in enumerate(document.chunks)
                    if c.section == sec.ordinal and (c.kind != "text" or c.token_count >= 45)
                ]
                if sec_chunks:
                    best_idx = max(
                        sec_chunks,
                        key=lambda i: _chunk_priority(document.chunks[i], sec.heading, len(avoid_by_chunk.get(i, []))),
                    )
                    fallback_chunk_indices.append(best_idx)
        except Exception as exc:  # noqa: BLE001
            log.warning("objective retrieval failed (%s); falling back to passage selection", exc)
            objective_jobs = []
            fallback_chunk_indices = []

    if not objective_jobs:
        fallback_chunk_indices = select_chunks(document, avoid_by_chunk, limit=MAX_CHUNKS_PER_PASS)

    chosen_images = select_images(document, limit=MAX_IMAGES_PER_PASS) if include_images else []
    compact_mode = (len(objective_jobs) + len(fallback_chunk_indices)) >= 14
    max_per_image = 2 if len(chosen_images) <= 6 else 1
    obj_max_q = 3 if len(objective_jobs) <= 10 else 2
    obj_max_s = 2 if len(objective_jobs) <= 10 else 1

    jobs = len(objective_jobs) + len(fallback_chunk_indices) + len(chosen_images)
    done = 0
    attempted = 0
    failed = 0
    lock = asyncio.Lock()

    async def tick() -> None:
        nonlocal done
        async with lock:
            done += 1
            if progress:
                await progress("Drafting, checking & revising questions", done, jobs)

    async def objective_job(obj: LearningObjective, primary_idx: int, supporting_indices: list[int]) -> list[ReadyQuestion]:
        nonlocal attempted, failed
        attempted += 1
        try:
            local_avoid = list(avoid_by_chunk.get(primary_idx, []))
            for s_idx in supporting_indices:
                local_avoid.extend(avoid_by_chunk.get(s_idx, []))
            return await _questions_for_objective(
                document, blueprint, obj, primary_idx, supporting_indices, local_avoid, report,
                glossary=glossary, max_q=obj_max_q, max_s=obj_max_s,
            )
        except Exception as exc:  # one bad objective must not sink the whole document
            failed += 1
            log.warning("objective '%s' failed: %s", obj.concept[:60], exc)
            report.reject("objective failed (AI error)")
            return []
        finally:
            await tick()

    async def chunk_job(index: int) -> list[ReadyQuestion]:
        nonlocal attempted, failed
        chunk = document.chunks[index]
        max_q, max_s = _quota(chunk, compact=compact_mode)
        if not max_q and not max_s:
            await tick()
            return []
        attempted += 1
        try:
            return await _questions_for_chunk(
                document, index, chunk_embeddings, avoid_by_chunk.get(index, []), report,
                glossary=glossary, compact=compact_mode,
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
            ready, note = await _questions_for_image(document, image, report, max_questions=max_per_image)
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

    tasks = [objective_job(obj, p_idx, s_idxs) for obj, p_idx, s_idxs in objective_jobs]
    tasks += [chunk_job(i) for i in fallback_chunk_indices]
    tasks += [image_job(image) for image in chosen_images]
    batches = await asyncio.gather(*tasks)
    if attempted > 0 and failed == attempted and report.drafted == 0:
        raise llm.AIUnavailable("Every AI question-writing request failed.")
    questions = [q for batch in batches for q in batch]

    if questions:
        if progress:
            await progress("Stress-testing questions without the document", jobs, jobs)
        questions = await _closed_book_stress_test(document, chunk_embeddings, questions, report)

    if questions:
        if progress:
            await progress("Curating the strongest questions", jobs, jobs)
        texts = [f"{q.question_text} {q.options[0] if q.options else ''}" for q in questions]
        vectors = await llm.embed(texts)
        for question, vector in zip(questions, vectors):
            question.embedding = vector
        # Keep highest-importance, understanding, and picture questions when two overlap.
        questions.sort(key=lambda q: (
            -int((q.quality or {}).get("importance", 3)),
            q.source_type != "image",
            q.skill != "understanding",
        ))
        questions = _dedupe(questions, existing_embeddings, report, threshold=0.84)
        questions = await curate_bank(
            document, questions, report, max_mcqs=max_mcqs, max_statements=max_statements,
        )
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
