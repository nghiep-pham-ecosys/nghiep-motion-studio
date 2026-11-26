"""One daily English lesson -> one long video, an audio file and Shorts (``motion-studio lesson``).

A lesson file (Markdown, public-safe, copied from the study book) holds five
sections, each a ``### (a)`` ... ``### (e)`` heading:

* (a) words: a table ``| word | Vietnamese meaning | example |``;
* (b) a script in a blockquote, marked with ``/`` (short pause), ``//`` (long
  pause) and ``**bold**`` (stress), plus notes such as the hard sounds;
* (c) a model interview answer: the question (``*"..."*``), the frame
  (``**step → step → ...**``), the answer as ``**Label:** text`` paragraphs and
  the follow-up question with a hint after ``→``;
* (d) IELTS Speaking: the band 7 notes, Part 1 and Part 3 as ``**Q: ...**``
  followed by the answer, the Part 2 cue card (an italic paragraph) and its
  answer, and the useful phrases (``a · b · c``);
* (e) IELTS Writing Task 2: the prompt (bold), the essay paragraphs, and the
  "why band 7" bullets ``- **Label:** note``.

``build_scenes`` turns it into one scenes.json of ten parts, played by two
original characters (``samples/characters``): Lan (her: teacher, interviewer,
examiner; Kokoro ``af_heart``) and Minh (him: learner, candidate; Kokoro
``am_michael``). Both are preset AI voices, never the owner's clone. Slow lines
use the same voice at speed 0.8 (``af_heart@0.8``). Repeat silences come from
the ``pause`` of each dialogue visual: ``scale`` x the line's length +
``add_s``.

``write_source`` writes the scenes, a topic file, a voices config, the fact
check and the character files into a folder; ``run`` hands that topic to the
``video`` pipeline (voice, visuals, render, subtitles, gate, Shorts) and then
writes the audio-only file (m4a with chapters), the description with chapter
timestamps and a bilingual SRT where a Vietnamese line exists.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .gate import narration_hash
from .narrate import split_lines

HER, HIM = "her", "him"
VOICES = {HER: "af_heart", HIM: "am_michael"}
STAND_IN = "bm_george"  # required by the voices schema; never read: the narrator needs no reference clip
SLOW = 0.8
NAMES = {HER: "Lan", HIM: "Minh"}
AI_LABEL = "AI voice"
GATE_WINDOW = (1200.0, 1800.0)  # 20-30 minutes
CHARACTER_FILES = ("her.svg", "him.svg", "classroom.svg", "interview.svg", "exam.svg", "desk.svg")
PARTS = (  # (key, chapter, scenery)
    ("intro", "Intro", "classroom"),
    ("words", "Words", "classroom"),
    ("listen", "Listen", "desk"),
    ("shadowing", "Shadowing", "desk"),
    ("listen-again", "Listen again", "desk"),
    ("answer", "Model answer", "interview"),
    ("follow-up", "Follow-up", "interview"),
    ("speaking", "IELTS Speaking (band 7)", "exam"),
    ("writing", "IELTS Writing (band 7)", "desk"),
    ("recap", "Recap", "classroom"),
)
# Pause after a line: scale x its length + add_s. "/" is shorter than "//"; repeat lines always get a pause.
PAUSES: dict[str, dict[str, Any]] = {
    "word": {"scale": 1.0, "add_s": 1.2, "cue": "Your turn"},
    "example": {"scale": 1.0, "add_s": 0.8, "cue": "Your turn"},
    "/": {"scale": 1.0, "add_s": 0.5, "cue": "Your turn"},
    "//": {"scale": 1.2, "add_s": 1.2, "cue": "Your turn"},
    "sentence": {"scale": 1.0, "add_s": 0.7, "cue": "Your turn"},
    "phrase": {"scale": 1.0, "add_s": 0.8, "cue": "Your turn"},
    "answer": {"scale": 0.0, "add_s": 20.0, "cue": "Answer aloud"},
    "prepare": {"scale": 0.0, "add_s": 60.0, "cue": "Prepare: 1 minute"},
    "read": {"scale": 0.0, "add_s": 3.0},
    "hints": {"scale": 0.0, "add_s": 8.0},
}
NUMBERS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
           "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")


class LessonError(ValueError):
    """The lesson file is missing a part or a part cannot be read; the message says which."""


# --- the lesson file ---------------------------------------------------------------------------------------------------


@dataclass
class Word:
    term: str
    gloss: str
    example: str


@dataclass
class Phrase:
    text: str  # with **bold** marks
    mark: str  # "/" short pause, "//" long pause


@dataclass
class QA:
    question: str
    answer: str


@dataclass
class Lesson:
    date: str
    title: str
    source: str  # the post the lesson comes from ("" when the header names none)
    words: list[Word]
    script: list[list[Phrase]]  # paragraphs of phrases
    script_notes: list[tuple[str, str]]  # (label, text), e.g. the hard sounds
    question: str
    frame: list[str]  # the answer's frame in Vietnamese, one item per step
    answer: list[tuple[str, str]]  # (label, paragraph)
    follow_up: str
    hint: str  # Vietnamese hint after the arrow
    speaking_notes: str
    part1: list[QA]
    cue_card: str
    part2: list[str]
    part3: list[QA]
    phrases: list[str]
    prompt: str
    essay: list[str]
    essay_notes: list[tuple[str, str]]
    follow_up_answer: str = ""  # optional English hint answer (``→ answer: "..."``)
    translations: dict[str, str] = field(default_factory=dict)  # English sentence -> Vietnamese line

    def script_text(self) -> list[str]:
        """The script's paragraphs as plain text with ``**bold**`` kept and the pause marks removed."""
        return [" ".join(p.text for p in para) for para in self.script]


def plain(text: str) -> str:
    """Text for the voice and the subtitles: no ``**`` marks, single spaces."""
    return " ".join(text.replace("**", "").split())


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^###\s*\(([a-e])\)", line)
        if m:
            current = m.group(1)
            out[current] = ""
            continue
        if current:
            out[current] += line + "\n"
    return out


def _blockquote_paragraphs(text: str) -> list[str]:
    """Paragraphs of the blockquotes in ``text`` (a blank ``>`` line or a non-quote line ends one)."""
    paras, current = [], []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            body = line.lstrip()[1:].strip()
            if body:
                current.append(body)
                continue
        if current:
            paras.append(" ".join(current))
            current = []
    if current:
        paras.append(" ".join(current))
    return paras


def _blockquote_lines(text: str) -> list[str]:
    return [line.lstrip()[1:].strip() for line in text.splitlines()
            if line.lstrip().startswith(">") and line.lstrip()[1:].strip()]


def parse_words(text: str) -> list[Word]:
    words = []
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    for row in rows[1:]:  # the first row is the header
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):
            continue
        if len(cells) != 3 or not all(cells):
            raise LessonError(f"words table: a row needs 3 cells (word, meaning, example): {row.strip()}")
        words.append(Word(*cells))
    if not words:
        raise LessonError("words table: no rows under the header")
    return words


_MARK = re.compile(r"\s+(//|/)(?=\s|$)")


def parse_phrases(paragraph: str) -> list[Phrase]:
    """``A / B // C`` -> phrases with the mark that follows each; the last one ends with ``//``."""
    pieces = _MARK.split(" " + paragraph.strip())
    out = []
    for k in range(0, len(pieces), 2):
        text = pieces[k].strip()
        mark = pieces[k + 1] if k + 1 < len(pieces) else "//"
        if text:
            out.append(Phrase(text, mark))
        elif out and k + 1 < len(pieces):
            out[-1].mark = mark
    return out


def _labelled(line: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*(?:[-*]\s+)?\*\*([^*]+?):?\*\*:?\s*(.*)$", line)
    return (m.group(1).strip().rstrip(":"), m.group(2).strip()) if m else None


def _quoted(text: str) -> str:
    m = re.search(r"\*\"(.+?)\"\*", text) or re.search(r"\"(.+?)\"", text)
    return m.group(1).strip() if m else ""


def _qa(lines: list[str]) -> list[QA]:
    out: list[QA] = []
    for line in lines:
        m = re.match(r"^\*\*Q:\s*(.+?)\*\*\s*(.*)$", line)
        if m:
            out.append(QA(m.group(1).strip(), m.group(2).strip()))
        elif out:
            out[-1].answer = (out[-1].answer + " " + line).strip()
    for qa in out:
        if not qa.answer:
            raise LessonError(f"IELTS speaking: the question {qa.question!r} has no answer")
    return out


def parse_lesson(text: str, translations: dict[str, str] | None = None) -> Lesson:
    lines = text.splitlines()
    head = next((ln for ln in lines if ln.startswith("# ")), "")
    m = re.match(r"^#\s+Lesson\s+(\d{4}-\d{2}-\d{2})\s*:?\s*(.*)$", head)
    if not m:
        raise LessonError("the first heading must be '# Lesson YYYY-MM-DD: <title>'")
    date, title = m.group(1), m.group(2).strip()
    source = _quoted(title) if "post" in title.lower() else ""
    sec = _sections(text)
    missing = [k for k in "abcde" if k not in sec]
    if missing:
        raise LessonError(f"missing section(s) {', '.join(f'(' + k + ')' for k in missing)}")

    words = parse_words(sec["a"])

    script = [parse_phrases(p) for p in _blockquote_paragraphs(sec["b"])]
    script = [p for p in script if p]
    if not script:
        raise LessonError("script (b): no blockquote")
    script_notes = []
    for line in sec["b"].splitlines():
        if line.lstrip().startswith(">"):
            continue
        lab = _labelled(line)
        if lab and lab[1]:
            script_notes.append(lab)

    c = sec["c"]
    question_line = next((ln for ln in c.splitlines() if "*\"" in ln and "follow" not in ln.lower()), "")
    question = _quoted(question_line)
    if not question:
        raise LessonError("model answer (c): no question in *\"...\"*")
    fm = re.search(r"\*\*([^*]*→[^*]*)\*\*", c)
    frame = [x.strip() for x in fm.group(1).split("→")] if fm else []
    answer = []
    for para in _blockquote_paragraphs(c):
        lab = _labelled(para)
        if not lab:
            raise LessonError(f"model answer (c): a paragraph must start with **Label:** ({para[:40]!r})")
        answer.append(lab)
    if not answer:
        raise LessonError("model answer (c): no answer paragraphs")
    follow_line = next((ln for ln in c.splitlines() if "follow-up" in ln.lower() or "follow up" in ln.lower()), "")
    follow_up = _quoted(follow_line)
    if not follow_up:
        raise LessonError("model answer (c): no follow-up question in *\"...\"*")
    after = follow_line.split("→", 1)[1].strip() if "→" in follow_line else ""
    am = re.search(r"answer:\s*\"(.+?)\"", after)
    follow_up_answer = am.group(1).strip() if am else ""
    hint = re.sub(r"^(gợi ý|hint)\s*:\s*", "", re.sub(r"answer:\s*\".+?\"\s*;?", "", after), flags=re.I).strip()

    d = sec["d"]
    blocks = re.split(r"^\*\*Part\s+([123])\*\*.*$", d, flags=re.M)
    speaking_notes = " ".join(ln.strip() for ln in blocks[0].splitlines()
                              if ln.strip() and not ln.lstrip().startswith(">"))
    parts = {blocks[k]: blocks[k + 1] for k in range(1, len(blocks) - 1, 2)}
    if set(parts) != {"1", "2", "3"}:
        raise LessonError("IELTS speaking (d): needs **Part 1**, **Part 2** and **Part 3**")
    part1 = _qa(_blockquote_lines(parts["1"]))
    cue = next((ln.strip() for ln in parts["2"].splitlines()
                if ln.strip().startswith("*") and not ln.strip().startswith("**")), "")
    cue_card = cue.strip("*").strip()
    if not cue_card:
        raise LessonError("IELTS speaking (d): Part 2 needs the cue card as an italic paragraph")
    part2 = _blockquote_paragraphs(parts["2"])
    p3_text = parts["3"]
    part3 = _qa(_blockquote_lines(p3_text))
    phrases = []
    for line in p3_text.splitlines():
        lab = _labelled(line)
        if lab and "·" in lab[1]:
            phrases = [x.strip().rstrip(".") for x in lab[1].split("·") if x.strip()]
    if not (part1 and part2 and part3):
        raise LessonError("IELTS speaking (d): every part needs questions and answers")

    e = sec["e"]
    pm = None
    for line in e.splitlines():
        if not line.lstrip().startswith(">") and (pm := re.search(r"\*\*([^*]{20,})\*\*", line)):
            break
    if not pm:
        raise LessonError("IELTS writing (e): no prompt in **bold**")
    prompt = pm.group(1).strip()
    essay = _blockquote_paragraphs(e)
    essay_notes = []
    for line in e.splitlines():
        if re.match(r"^\s*[-*]\s+", line):
            lab = _labelled(line)
            essay_notes.append(lab if lab else ("", re.sub(r"^\s*[-*]\s+", "", line).strip()))
    if not essay:
        raise LessonError("IELTS writing (e): no essay paragraphs")
    return Lesson(date=date, title=title, source=source, words=words, script=script, script_notes=script_notes,
                  question=question, frame=frame, answer=answer, follow_up=follow_up, hint=hint,
                  speaking_notes=speaking_notes, part1=part1, cue_card=cue_card, part2=part2, part3=part3,
                  phrases=phrases, prompt=prompt, essay=essay, essay_notes=essay_notes,
                  follow_up_answer=follow_up_answer, translations=dict(translations or {}))


def read_translations(path: Path) -> dict[str, str]:
    """Optional ``<lesson>.vi.tsv``: ``English sentence<TAB>Vietnamese line`` per row (``#`` starts a comment)."""
    out = {}
    if not path.is_file():
        return out
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        en, sep, vi = line.partition("\t")
        if not sep or not en.strip() or not vi.strip():
            raise LessonError(f"{path.name}:{n}: write 'English<TAB>Vietnamese'")
        out[plain(en)] = vi.strip()
    return out


def load_lesson(path: str | Path) -> Lesson:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LessonError(f"cannot read {path}: {exc}") from exc
    return parse_lesson(text, read_translations(path.with_name(path.name.replace(".lesson.md", "") + ".vi.tsv")))


# --- scenes ------------------------------------------------------------------------------------------------------------


def sentences(text: str) -> list[str]:
    """Sentences of a paragraph, ``**bold**`` marks kept."""
    return split_lines(text)


def number(n: int) -> str:
    return NUMBERS[n] if 0 <= n < len(NUMBERS) else str(n)


def _ordinal(n: int) -> str:
    return ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")[n - 1] if 1 <= n <= 10 \
        else str(n)


def _bold_part(paragraph: str, part: str) -> str:
    """``paragraph`` with ``part`` (a sentence or phrase in it) in bold and its own bold marks dropped."""
    flat, target = plain(paragraph), plain(part)
    i = flat.find(target)
    if i < 0:
        return flat
    return f"{flat[:i]}**{target}**{flat[i + len(target):]}"


class Builder:
    """Collects scenes part by part; every scene is one line of one character."""

    def __init__(self, lesson: Lesson, characters: str = "characters") -> None:
        self.lesson = lesson
        self.characters = characters
        self.scenes: list[dict[str, Any]] = []
        self.part_index = 0
        self.part_key = ""
        self.counter: dict[str, int] = {}
        self._chapter_pending = ""

    def part(self, index: int) -> None:
        self.part_index = index
        self.part_key = PARTS[index][0]
        self._chapter_pending = PARTS[index][1]

    def say(self, who: str, text: str, *, slow: bool = False, board: dict[str, Any] | None = None,
            sub: str | None = None, pause: str | None = None, tag: str = "line") -> dict[str, Any]:
        key, chapter, scenery = PARTS[self.part_index]
        n = self.counter.get(f"{key}-{tag}", 0) + 1
        self.counter[f"{key}-{tag}"] = n
        caption: dict[str, str] = {"text": " ".join(text.split())}
        vi = sub or self.lesson.translations.get(plain(text))
        if vi:
            caption["sub"] = vi
        visual: dict[str, Any] = {
            "kind": "dialogue",
            "scenery": f"{self.characters}/{scenery}.svg",
            "cast": {HER: f"{self.characters}/her.svg", HIM: f"{self.characters}/him.svg"},
            "talk": who,
            "part": f"{self.part_index + 1} · {chapter}",
            "label": AI_LABEL,
            "caption": caption,
        }
        if board:
            visual["board"] = board
        if pause:
            visual["pause"] = dict(PAUSES[pause])
        scene: dict[str, Any] = {
            "id": f"{key}-{tag}-{n}",
            "speaker": f"{who}-slow" if slow else who,
            "narration": {"en": plain(text)},
            "duration_s": 1,  # a floor only: the narration (and the pause) set the real length
            "visual": visual,
        }
        if self._chapter_pending:
            scene["chapter"] = {"en": self._chapter_pending}
            self._chapter_pending = ""
        self.scenes.append(scene)
        return scene


def _board(title: str, *lines: tuple[str, str]) -> dict[str, Any]:
    return {"title": title, "lines": [{"style": style, "text": text} for style, text in lines if text]}


def _word_card(lesson: Lesson, i: int, highlight_example: bool = False) -> dict[str, Any]:
    w = lesson.words[i]
    example = f"**{w.example}**" if highlight_example else w.example
    return _board(f"Word {i + 1} of {len(lesson.words)}", ("big", w.term), ("vi", w.gloss), ("body", example))


def _paragraph_board(title: str, paragraph: str, current: str) -> dict[str, Any]:
    return _board(title, ("body", _bold_part(paragraph, current)))


def build_scenes(lesson: Lesson, characters: str = "characters") -> dict[str, Any]:
    """The scenes.json of the whole lesson: ten parts, two characters, one line per scene."""
    b = Builder(lesson, characters)
    n_words = len(lesson.words)
    her_name, his_name = NAMES[HER], NAMES[HIM]
    outline = _board("Today", *(("bullet", f"{k + 1}. {PARTS[k][1]}") for k in range(len(PARTS))))

    # 1. Intro
    b.part(0)
    b.say(HER, f"Hello, and welcome to English for engineers. I'm {her_name}.", board=outline)
    b.say(HIM, f"And I'm {his_name}. I'm learning with you.", board=outline)
    if lesson.source:
        b.say(HER, f"Today's lesson comes from the post: {lesson.source}.", board=outline)
    else:
        b.say(HER, f"Today's topic: {plain(lesson.title)}.", board=outline)
    b.say(HER, f"You will learn {number(n_words)} words, shadow a short story, and practise an interview answer.",
          board=outline)
    b.say(HIM, "Then we do IELTS speaking and writing, with band 7 samples.", board=outline)
    b.say(HER, "Both of our voices are AI voices. Let's start.", board=outline)

    # 2. Words: she teaches, he repeats
    b.part(1)
    b.say(HER, f"Part one: {number(n_words)} words. Listen, then repeat after {his_name}.",
          board=_board("Words", *(("bullet", w.term) for w in lesson.words[:8]),
                       ("small", "..." if n_words > 8 else "")))
    for i, w in enumerate(lesson.words):
        term = w.term.rstrip(".")
        b.say(HER, f"{term}.", board=_word_card(lesson, i), sub=w.gloss, tag="word")
        b.say(HIM, f"{term}.", board=_word_card(lesson, i), sub=w.gloss, pause="word", tag="repeat")
        b.say(HER, w.example, board=_word_card(lesson, i, True), tag="example")
        b.say(HIM, w.example, slow=True, board=_word_card(lesson, i, True), pause="example", tag="slow")

    # 3. Listen: he reads his story at normal speed
    paragraphs = lesson.script_text()
    b.part(2)
    b.say(HER, f"Now listen to {his_name}'s story. Just listen, there are no pauses.",
          board=_board("Listen", *(("dim", plain(p)) for p in paragraphs[:3])))
    for k, para in enumerate(paragraphs):
        for s in sentences(para):
            b.say(HIM, s, board=_paragraph_board(f"Story, paragraph {k + 1} of {len(paragraphs)}", para, s),
                  tag="sentence")

    # 4. Shadowing: he reads slowly phrase by phrase, she gives the cue in the pauses
    b.part(3)
    notes = [("bullet", f"**{label}:** {text}") for label, text in lesson.script_notes]
    b.say(HER, "Now shadow the story. Repeat each phrase in the pause: short pauses inside a sentence, "
               "longer ones after it.", board=_board("Shadowing", *notes) if notes else None)
    for k, para in enumerate(lesson.script):
        whole = " ".join(p.text for p in para)
        for phrase in para:
            b.say(HIM, phrase.text, slow=True, pause=phrase.mark,
                  board=_paragraph_board(f"Shadowing, paragraph {k + 1} of {len(lesson.script)}", whole, phrase.text),
                  tag="phrase")

    # 5. Listen again
    b.part(4)
    b.say(HER, "Listen once more, at normal speed.")
    for k, para in enumerate(paragraphs):
        for s in sentences(para):
            b.say(HIM, s, board=_paragraph_board(f"Story, paragraph {k + 1} of {len(paragraphs)}", para, s),
                  tag="sentence")

    # 6. Model answer: she interviews, he answers
    b.part(5)
    labels = [label for label, _ in lesson.answer]
    frame_vi = lesson.frame if len(lesson.frame) == len(labels) else [""] * len(labels)
    b.say(HER, f"Now a job interview. I ask, and {his_name} answers.",
          board=_board("Interview", ("body", f"Frame: {' → '.join(labels)}"), ("vi", " → ".join(lesson.frame))))
    question_board = _board("Interview question", ("body", f"“{lesson.question}”"))
    b.say(HER, lesson.question, board=question_board, tag="question")
    for label, para in lesson.answer:
        for s in sentences(para):
            b.say(HIM, s, board=_paragraph_board(f"Model answer · {label}", para, s), tag="answer")
    b.say(HER, f"Let's look at the frame of this answer. It has {number(len(labels))} steps.",
          board=_board("The frame", *(("bullet", f"**{lab}** {vi}".strip()) for lab, vi in zip(labels, frame_vi))))
    for n, ((label, para), vi) in enumerate(zip(lesson.answer, frame_vi), 1):
        b.say(HER, f"Step {_ordinal(n)}: {label}.", sub=vi or None, pause="read", tag="step",
              board=_board(f"Step {n} of {len(labels)}", ("big", label), ("vi", vi), ("dim", para)))
    b.say(HER, f"Now repeat the answer after {his_name}, sentence by sentence.", board=question_board)
    for label, para in lesson.answer:
        for s in sentences(para):
            b.say(HIM, s, pause="sentence", board=_paragraph_board(f"Repeat · {label}", para, s), tag="repeat")

    # 7. Follow-up
    b.part(6)
    follow_board = _board("Follow-up question", ("body", f"“{lesson.follow_up}”"))
    b.say(HER, lesson.follow_up, board=follow_board, pause="answer", tag="question")
    hints = [h.strip() for h in re.split(r"[;；]", lesson.hint) if h.strip()]
    hint_board = _board("Hints", ("body", f"“{lesson.follow_up}”"), *(("bullet", h) for h in hints))
    if lesson.follow_up_answer:
        for s in sentences(lesson.follow_up_answer):
            b.say(HIM, s, board=hint_board, tag="answer")
    else:
        b.say(HIM, "These are the points I would make.", board=hint_board, pause="hints", tag="answer")

    # 8. IELTS Speaking: she is the examiner, he the candidate
    b.part(7)
    b.say(HER, f"Now IELTS Speaking. I am the examiner, and {his_name} is the candidate. "
               "This is a band 7 sample.", board=_board("IELTS Speaking · band 7", ("vi", lesson.speaking_notes)))
    b.say(HER, "Let's begin with Part 1.", board=_board("Part 1", *(("bullet", qa.question) for qa in lesson.part1)))
    for qa in lesson.part1:
        board = _board("Part 1", ("body", f"“{qa.question}”"))
        b.say(HER, qa.question, board=board, tag="question")
        for s in sentences(qa.answer):
            b.say(HIM, s, board=_paragraph_board(f"Part 1 · {plain(qa.question)}", qa.answer, s), tag="answer")
    cue_board = _board("Part 2 · cue card", ("body", lesson.cue_card))
    b.say(HER, "Now Part 2. Here is your cue card.", board=cue_board)
    for s in sentences(lesson.cue_card):
        b.say(HER, s, board=cue_board, tag="cue")
    b.say(HER, "You have one minute to prepare. You can make notes.", board=cue_board, pause="prepare")
    b.say(HER, "Please start speaking now.", board=cue_board)
    for k, para in enumerate(lesson.part2):
        for s in sentences(para):
            b.say(HIM, s, board=_paragraph_board(f"Part 2 · paragraph {k + 1} of {len(lesson.part2)}", para, s),
                  tag="answer")
    b.say(HER, "Thank you. Now Part 3.", board=_board("Part 3", *(("bullet", qa.question) for qa in lesson.part3)))
    for qa in lesson.part3:
        b.say(HER, qa.question, board=_board("Part 3", ("body", f"“{qa.question}”")), tag="question")
        for s in sentences(qa.answer):
            b.say(HIM, s, board=_paragraph_board(f"Part 3 · {plain(qa.question)}", qa.answer, s), tag="answer")
    if lesson.phrases:
        b.say(HER, "Useful phrases from these answers. Repeat after me.",
              board=_board("Useful phrases", *(("bullet", p) for p in lesson.phrases)))
        for p in lesson.phrases:
            b.say(HER, p, pause="phrase", tag="phrase",
                  board=_board("Useful phrases", *(("bullet", f"**{q}**" if q == p else q) for q in lesson.phrases)))
    b.say(HER, f"Now repeat the Part 2 answer after {his_name}, sentence by sentence.", board=cue_board)
    for k, para in enumerate(lesson.part2):
        for s in sentences(para):
            b.say(HIM, s, pause="sentence", tag="repeat",
                  board=_paragraph_board(f"Repeat Part 2 · paragraph {k + 1} of {len(lesson.part2)}", para, s))

    # 9. IELTS Writing: he reads the essay, she explains the band 7 notes
    b.part(8)
    prompt_board = _board("IELTS Writing Task 2", ("body", lesson.prompt))
    b.say(HER, "Now IELTS Writing, Task 2. Here is the question.", board=prompt_board)
    for s in sentences(lesson.prompt):
        b.say(HER, s, board=prompt_board, tag="prompt")
    b.say(HIM, "Here is my essay, paragraph by paragraph.", board=prompt_board)
    for k, para in enumerate(lesson.essay):
        for s in sentences(para):
            b.say(HIM, s, board=_paragraph_board(f"Essay · paragraph {k + 1} of {len(lesson.essay)}", para, s),
                  tag="essay")
    b.say(HER, "Why is this a band 7 essay? Here are the notes.",
          board=_board("Why band 7", *(("bullet", label or text[:60]) for label, text in lesson.essay_notes)))
    for n, (label, text) in enumerate(lesson.essay_notes, 1):
        line = f"{label}." if label else "One more tip."
        b.say(HER, line, pause="read", tag="note",
              board=_board(f"Why band 7 · {n} of {len(lesson.essay_notes)}", ("big", label), ("vi", text)))

    # 10. Recap: she says each word, he repeats
    b.part(9)
    b.say(HER, f"Let's recap the {number(n_words)} words.")
    for i, w in enumerate(lesson.words):
        term = w.term.rstrip(".")
        b.say(HER, f"{term}.", board=_word_card(lesson, i), sub=w.gloss, tag="word")
        b.say(HIM, f"{term}.", board=_word_card(lesson, i), sub=w.gloss, tag="repeat")
    b.say(HER, "That's all for today. See you in the next lesson.")
    b.say(HIM, "Bye, and keep practising.")

    voice = lambda who, slow: {"en": {"engine": "kokoro", "voice": VOICES[who] + (f"@{SLOW:g}" if slow else "")}}  # noqa: E731
    return {
        "version": 1,
        "title": {"en": video_title(lesson)},
        "languages": ["en"],
        "output": {"width": 1920, "height": 1080, "fps": 30},
        "theme": "light",
        "speakers": {HER: voice(HER, False), HIM: voice(HIM, False),
                     f"{HER}-slow": voice(HER, True), f"{HIM}-slow": voice(HIM, True)},
        "scenes": b.scenes,
    }


def video_title(lesson: Lesson) -> str:
    topic = lesson.source or plain(lesson.title)
    title = f"English for engineers: {topic}"
    return title if len(title) <= 100 else title[:97].rstrip() + "..."


def job_name(path: Path, lesson: Lesson) -> str:
    stem = path.name.replace(".lesson.md", "").replace(".md", "")
    slug = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-") or "lesson"
    return f"en-{lesson.date}-{slug}"[:64].rstrip("-")


# --- the job source folder -------------------------------------------------------------------------------------------


def voices_config() -> dict[str, Any]:
    """Both characters are preset voices: the narrator needs no reference clip, so no stand-in is ever read."""
    return {
        "version": 1,
        "narrator": {"en": {"engine": "kokoro", "voice": VOICES[HER]}},
        "presets": {"lesson": {
            "description": "English lesson: Lan (her) and Minh (him), preset Kokoro voices, never the clone.",
            "guest": {"en": [{"engine": "kokoro", "voice": VOICES[HIM]}]},
            "stand_in": {"en": {"engine": "kokoro", "voice": STAND_IN}},
        }},
    }


def topic_file(job: str, lesson: Lesson, shorts: int = 3) -> dict[str, Any]:
    return {
        "version": 1,
        "job": job,
        "topic": f"English lesson {lesson.date}: {plain(lesson.title)}"[:200],
        "scenes": "scenes.json",
        "factcheck": "factcheck.md",
        "languages": ["en"],
        "cast": {"en": {"preset": "lesson"}},
        "voices": "voices.json",
        "shorts": shorts,
        "gate": {"min_s": GATE_WINDOW[0], "max_s": GATE_WINDOW[1]},
    }


def draft_factcheck(lesson: Lesson, doc: dict[str, Any]) -> str:
    """A fact check to fill in: every claim unchecked, so the gate stops until a person checks them."""
    out = [f"# Fact check: {doc['title']['en']}", "", "Reviewed-by:", f"Date: {lesson.date}",
           f"Narration: {narration_hash(doc)[:12]}", "",
           "Check each claim, add a source (a dictionary or ielts.org link) and tick it.", ""]
    out += [f"- [ ] {w.term} means: {w.gloss}" for w in lesson.words]
    out += ["- [ ] IELTS Speaking Part 2: one minute to prepare, then one to two minutes of speaking",
            "- [ ] IELTS Writing Task 2: at least 250 words in about 40 minutes"]
    return "\n".join(out) + "\n"


def find_characters(start: Path) -> Path | None:
    for folder in [start, *start.parents]:
        candidate = folder / "characters"
        if all((candidate / name).is_file() for name in CHARACTER_FILES):
            return candidate
    return None


@dataclass
class Source:
    folder: Path
    topic: Path
    scenes: Path
    factcheck_ready: bool
    narration: str  # the 12-character narration hash the fact check must name


def write_source(lesson_path: Path, out: Path, characters: Path | None = None, shorts: int = 3) -> Source:
    """Write scenes.json, topic.json, voices.json, factcheck.md and the character files into ``out``."""
    lesson_path = Path(lesson_path)
    lesson = load_lesson(lesson_path)
    characters = characters or find_characters(lesson_path.resolve().parent)
    if characters is None:
        raise LessonError(f"no characters folder with {', '.join(CHARACTER_FILES)} next to {lesson_path} "
                          "or above it; pass --characters")
    out.mkdir(parents=True, exist_ok=True)
    (out / "characters").mkdir(exist_ok=True)
    for name in CHARACTER_FILES:
        shutil.copy2(characters / name, out / "characters" / name)
    doc = build_scenes(lesson)
    job = job_name(lesson_path, lesson)
    _write_json(out / "scenes.json", doc)
    _write_json(out / "topic.json", topic_file(job, lesson, shorts))
    _write_json(out / "voices.json", voices_config())
    reviewed = lesson_path.with_name(lesson_path.name.replace(".lesson.md", "") + ".factcheck.md")
    if reviewed.is_file():
        shutil.copy2(reviewed, out / "factcheck.md")
    else:
        (out / "factcheck.md").write_text(draft_factcheck(lesson, doc), encoding="utf-8")
    return Source(folder=out, topic=out / "topic.json", scenes=out / "scenes.json",
                  factcheck_ready=reviewed.is_file(), narration=narration_hash(doc)[:12])


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- after the render: description, audio file, bilingual subtitles -------------------------------------------------


def description_intro(lesson: Lesson) -> str:
    n = len(lesson.words)
    lines = [
        f"A full English lesson for engineers, played as a conversation between two animated characters, "
        f"{NAMES[HER]} and {NAMES[HIM]}: {n} words with Vietnamese meanings, a short story to listen to and shadow, "
        "a model interview answer, and IELTS band 7 samples for speaking and writing. Repeat in the pauses.",
    ]
    if lesson.source:
        lines.append(f"Material: the post \"{lesson.source}\".")
    lines.append(f"{AI_LABEL}: both voices are synthetic (Kokoro {VOICES[HER]} and {VOICES[HIM]}); the characters "
                 "are original drawings. An audio-only version of this lesson is made for listening without a screen.")
    return "\n\n".join(lines)


def chapter_marks(doc: dict[str, Any], timeline: dict[str, Any]) -> list[tuple[int, str]]:
    from .publish import chapters

    return chapters(doc, timeline, "en", doc["title"]["en"])


def description(lesson: Lesson, doc: dict[str, Any], timeline: dict[str, Any]) -> str:
    from .publish import describe

    return describe(description_intro(lesson), chapter_marks(doc, timeline))


def ffmetadata(title: str, marks: list[tuple[int, str]], duration_s: float, comment: str) -> str:
    """An FFMETADATA1 file with one chapter per part (for the audio-only file)."""
    esc = lambda s: re.sub(r"([=;#\\\n])", r"\\\1", s)  # noqa: E731
    out = [";FFMETADATA1", f"title={esc(title)}", f"comment={esc(comment)}"]
    ends = [t for t, _ in marks[1:]] + [duration_s]
    for (start, name), end in zip(marks, ends):
        out += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}", f"END={int(math.floor(end * 1000))}",
                f"title={esc(name)}"]
    return "\n".join(out) + "\n"


def audio_cmd(video: Path, meta: Path, out: Path) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(video), "-i", str(meta),
            "-map", "0:a:0", "-map_metadata", "1", "-map_chapters", "1", "-c:a", "copy", "-vn", str(out)]


def bilingual_srt(srt_text: str, doc: dict[str, Any], timeline: dict[str, Any]) -> tuple[str, int]:
    """The SRT with the scene's Vietnamese line under each cue that has one; returns (text, cues with a VI line)."""
    from .subtitles import parse_srt, to_srt, Cue

    subs = {s["id"]: s["visual"].get("caption", {}).get("sub") for s in doc["scenes"]}
    spans = [(t["start_s"], t["start_s"] + t["duration_s"], t["id"]) for t in timeline["scenes"]]
    cues, added, last = [], 0, None
    for cue in parse_srt(srt_text):
        sid = next((i for a, b, i in spans if a <= cue.start_s < b), None)
        vi = subs.get(sid) if sid else None
        if vi and sid != last:
            cues.append(Cue(cue.start_s, cue.end_s, f"{cue.text}\n{vi}"))
            added += 1
        else:
            cues.append(cue)
        last = sid if vi else last
    return to_srt(cues), added


@dataclass
class Extras:
    audio: Path
    description: Path
    intro: Path
    bilingual: Path | None


def write_extras(lesson: Lesson, job: Path, name: str, run: Callable[[list[str]], None] | None = None) -> Extras:
    """After a passing run: the m4a (chapters, AI voice comment), the description and the bilingual SRT."""
    video = job / f"{name}.en.mp4"
    doc = json.loads((job / "scenes.json").read_text(encoding="utf-8"))
    timeline = json.loads(video.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    marks = chapter_marks(doc, timeline)
    intro = job / f"{name}.en.intro.txt"
    intro.write_text(description_intro(lesson) + "\n", encoding="utf-8")
    desc = job / f"{name}.en.description.txt"
    desc.write_text(description(lesson, doc, timeline) + "\n", encoding="utf-8")
    meta = job / f".{name}.ffmetadata"
    meta.write_text(ffmetadata(doc["title"]["en"], marks, float(timeline["duration_s"]),
                               f"{AI_LABEL}: Kokoro {VOICES[HER]} and {VOICES[HIM]}"), encoding="utf-8")
    audio = job / f"{name}.en.m4a"
    try:
        (run or _run)(audio_cmd(video, meta, audio))
    finally:
        meta.unlink(missing_ok=True)
    bilingual = None
    srt = video.with_suffix(".srt")
    if srt.is_file():
        text, added = bilingual_srt(srt.read_text(encoding="utf-8"), doc, timeline)
        if added:
            bilingual = job / f"{name}.en-vi.srt"
            bilingual.write_text(text, encoding="utf-8")
    return Extras(audio=audio, description=desc, intro=intro, bilingual=bilingual)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise LessonError(f"{' '.join(cmd[:3])} ... failed: {proc.stderr.strip()[-400:]}")
