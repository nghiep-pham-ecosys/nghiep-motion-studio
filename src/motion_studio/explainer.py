"""One lab-to-prod post (or one study lesson) and its merged work -> one 8-15 min explainer video (``motion-studio
explainer``).

A *source* is either

* a lab-to-prod **post folder**: ``post.md`` (front matter and the channel sections; the ``## linkedin`` section is
  the blog note), ``image.png``, the scripts next to it and their ``.expected`` output, and the work entries its
  ``work:`` names in the nearest ``work.yaml`` (``content/work.yaml`` in lab-to-prod): what was merged, where, when;
* or a **study lesson**: one Markdown file ``# Lesson YYYY-MM-DD: <title>`` with the sections ``## Concept``,
  ``## Worked example``, ``## Edge cases`` and ``## Quiz`` (numbered questions, each with an ``Answer:`` line).
  Only concepts with self-written examples; never graded course code.

The video is played by Lan (her) and Minh (him), the two original characters of the English lessons, with the same
preset Kokoro voices (never the owner's clone). What they say is a *talk file* (Markdown) written for the day:

.. code-block:: text

    Title: optional, else from the post or lesson
    Job: optional, ends in -explainer

    ## The problem {cafe}
    Lan: A RAG test question, and BM25 ranked the right chunk 14th of 28.
    [code bm25_title.py 52-53 | with_context(): the fix]
    Minh: Two lines of code. **Bold** reaches the screen, not the voice.
    [chart Rank of the right chunk | max 28 | Chunk text only = 14 (14 of 28) | Title first = 4 (4 of 28)]
    [still]
    Lan: Answer before I do. [pause 8]

``## heading`` starts a chapter; ``{place}`` picks the library place of its stills (classroom, cafe, desk,
interview, exam). ``Lan:`` and ``Minh:`` lines are split into sentences, one scene each, and every sentence must
fit one subtitle cue. A ``[...]`` directive sets what the following lines show until the next directive:
``code FILE A-B [hl C-D]`` and ``output FILE A-B [hl C-D]`` (lines of a file next to the source),
``image FILE``, ``chart TITLE | max N | note TEXT | LABEL = VALUE (TEXT) ...``, ``points TITLE | item ...``,
``flow TITLE | a -> b, c -> d`` (full-frame ``card`` scenes); ``card TITLE | line ...`` (a small card beside the
speech bubble) and ``still`` (the bubble alone) keep the ``illustrated`` look. Any panel takes ``| source TEXT``.

Every number in the voice and on a panel (not code or output lines, which are the files themselves) must occur in
the source: the post, its scripts and ``.expected`` files and its work entries, or the lesson file. The builder
refuses a number it cannot find, and writes a draft fact check that lists each number with the file and line it
found it in, unchecked, so the gate stops until a person checks them.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .gate import narration_hash
from .images import Library, focus_for, load_library, pick_shots
from .lesson import AI_BADGE, AI_LABEL, HER, HIM, NAMES, VOICES, plain, voices_config
from .narrate import split_lines

GATE_WINDOW = (480.0, 900.0)  # 8-15 minutes
SHORTS = 2
PLACES = ("classroom", "cafe", "desk", "interview", "exam")
ROTATION = ("classroom", "cafe", "desk", "classroom", "cafe", "desk", "classroom")  # places of sections without {place}
SPEAKERS = {"lan": HER, "minh": HIM}
MAX_CODE_LINES = 16
PAUSE_CUE = "Answer aloud"
LESSON_SECTIONS = ("concept", "worked example", "edge cases", "quiz")
BLOG_NOTES = "https://nghiep-pham-ecosys.github.io/lab-to-prod/notes/"
PLAYLISTS = {
    "post": ("lab-to-prod-ai-engineering", "Lab to Prod — AI engineering in practice",
             "The project work behind each Lab to Prod post, explained by two AI characters: the problem, the code "
             "that runs, the measured number and its limit."),
    "lesson": ("study-notes-explained", "Study notes explained — cloud, data structures and agents",
               "Study lessons explained by two AI characters: the concept, a worked example, the edge cases and a "
               "short quiz."),
}
PANELS = ("code", "output", "image", "chart", "points", "flow")


class ExplainerError(ValueError):
    """The source, the talk file or a number in it is wrong; the message says which line."""


# --- the source ------------------------------------------------------------------------------------------------------


@dataclass
class Source:
    kind: str  # "post" or "lesson"
    path: Path  # the post folder or the lesson file
    folder: Path  # where the files a talk names live
    title: str
    date: str
    slug: str
    texts: dict[str, str]  # name -> text: everywhere a number may come from
    work: list[dict[str, str]] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"{BLOG_NOTES}{self.path.name}/" if self.kind == "post" else ""


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """The flat ``key: value`` lines of a ``---`` front matter (nested blocks skipped) and the body after it."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        raise ExplainerError("post.md: the front matter has no closing ---")
    meta = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return meta, text[end + 4:]


def parse_work(text: str) -> dict[str, dict[str, str]]:
    """The ``entries`` of a work ledger, read without a YAML library: ``  - id: ...`` starts an entry, ``    key: value``
    sets a field, and a deeper line continues a folded (``>-``) value."""
    entries: list[dict[str, str]] = []
    key = ""
    inside = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.startswith("entries:")
            continue
        if not inside:
            continue
        if m := re.match(r"^ {2}- (\w+):\s*(.*)$", line):
            entries.append({})
        elif not (m := re.match(r"^ {4}(\w+):\s*(.*)$", line)):
            if entries and key:
                entries[-1][key] = (entries[-1][key] + " " + line.strip()).strip()
            continue
        if not entries:
            continue
        key, value = m.group(1), m.group(2).strip()
        entries[-1][key] = "" if value in (">-", ">", "|", "|-") else value.strip('"').strip("'")
    return {e["id"]: e for e in entries if "id" in e}


def find_work_ledger(folder: Path) -> Path | None:
    for parent in [folder, *folder.parents]:
        for candidate in (parent / "work.yaml", parent / "content" / "work.yaml"):
            if candidate.is_file():
                return candidate
    return None


def load_post(folder: Path) -> Source:
    post = folder / "post.md"
    try:
        text = post.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExplainerError(f"cannot read {post}: {exc}") from exc
    meta, _ = front_matter(text)
    for key in ("title", "date", "work"):
        if not meta.get(key):
            raise ExplainerError(f"{post}: the front matter has no {key}: (a post without merged work has no explainer)")
    texts = {"post.md": text}
    for pattern in ("*.py", "*.expected"):
        for f in sorted(folder.glob(pattern)):
            texts[f.name] = f.read_text(encoding="utf-8")
    ledger = find_work_ledger(folder)
    if ledger is None:
        raise ExplainerError(f"no work.yaml at or above {folder} (lab-to-prod keeps it in content/work.yaml)")
    known = parse_work(ledger.read_text(encoding="utf-8"))
    ids = [w.strip() for w in meta["work"].strip("[]").split(",") if w.strip()]
    missing = [w for w in ids if w not in known]
    if missing:
        raise ExplainerError(f"post.md names work {', '.join(missing)}, which {ledger} does not list")
    work = [known[w] for w in ids]
    for entry in work:
        texts[f"work.yaml ({entry['id']})"] = "\n".join(f"{k}: {v}" for k, v in entry.items())
    return Source(kind="post", path=folder, folder=folder, title=meta["title"], date=meta["date"], slug=folder.name,
                  texts=texts, work=work, meta=meta)


def lesson_sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip().lower()
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return out


def quiz_items(text: str) -> list[tuple[str, str]]:
    """``1. Question`` followed by an ``Answer:`` line -> (question, answer) pairs; indented lines continue the question
    or the answer above them."""
    items: list[list[str]] = []
    in_answer = False
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s*\d+\.\s+(.*\S)\s*$", line)
        a = re.match(r"^\s*(?:\*\*)?Answer:(?:\*\*)?\s*(.*\S)\s*$", line)
        if a and items:
            items[-1][1], in_answer = a.group(1), True
        elif m and not line.startswith("  "):
            items.append([m.group(1), ""])
            in_answer = False
        elif items and line.startswith(" "):
            items[-1][1 if in_answer else 0] += " " + line.strip()
    for q, a in items:
        if not a:
            raise ExplainerError(f"quiz: the question {q[:50]!r} has no 'Answer:' line")
    return [(q, a) for q, a in items]


def load_lesson_file(path: Path) -> Source:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExplainerError(f"cannot read {path}: {exc}") from exc
    head = next((ln for ln in text.splitlines() if ln.startswith("# ")), "")
    m = re.match(r"^#\s+Lesson\s+(\d{4}-\d{2}-\d{2})\s*:\s*(.+)$", head)
    if not m:
        raise ExplainerError(f"{path.name}: the first heading must be '# Lesson YYYY-MM-DD: <title>'")
    sections = lesson_sections(text)
    missing = [s for s in LESSON_SECTIONS if not any(k.startswith(s) for k in sections)]
    if missing:
        raise ExplainerError(f"{path.name}: missing section(s) {', '.join('## ' + s.capitalize() for s in missing)}")
    quiz = next(v for k, v in sections.items() if k.startswith("quiz"))
    if not quiz_items(quiz):
        raise ExplainerError(f"{path.name}: the quiz has no numbered questions")
    stem = re.sub(r"[^a-z0-9-]+", "-", path.stem.lower()).strip("-") or "lesson"
    return Source(kind="lesson", path=path, folder=path.parent, title=m.group(2).strip(), date=m.group(1), slug=stem,
                  texts={path.name: text})


def load_source(path: str | Path) -> Source:
    path = Path(path)
    if path.is_dir():
        return load_post(path)
    if path.suffix == ".md":
        return load_lesson_file(path)
    raise ExplainerError(f"{path}: give a lab-to-prod post folder (with post.md) or a lesson .md file")


# --- numbers and where they come from ----------------------------------------------------------------------------------

_DATE = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}(?![\w])")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:st|nd|rd|th|s)?(?![\w])")


def numbers_in(text: str) -> list[str]:
    """The numbers of a text as written (dates whole; ``3,000`` -> ``3000``); digits inside a word (``bm25``, ``k1``,
    ``C03``) are names, not numbers."""
    out = _DATE.findall(text)
    rest = _DATE.sub(" ", text)
    out += [m.group(1).replace(",", "") for m in _NUMBER.finditer(rest)]
    return out


def _key(number: str) -> Any:
    if _DATE.fullmatch(number):
        return number
    try:
        return Decimal(number)
    except InvalidOperation:
        return number


@dataclass(frozen=True)
class Found:
    name: str  # the source file (or work entry)
    line: int
    text: str

    def ref(self) -> str:
        return f"{self.name}:{self.line}"


def number_index(texts: dict[str, str]) -> dict[Any, list[Found]]:
    """Each number of the sources -> every place (file and line) it occurs."""
    index: dict[Any, list[Found]] = {}
    for name, text in texts.items():
        for n, line in enumerate(text.splitlines(), 1):
            for number in dict.fromkeys(numbers_in(line)):
                index.setdefault(_key(number), []).append(Found(name, n, line.strip()))
    return index


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


def best_place(number: str, text: str, index: dict[Any, list[Found]]) -> Found | None:
    """Where ``number`` comes from: of the source lines that hold it, the one sharing most words with ``text``
    (the first one on a tie)."""
    places = index.get(_key(number), [])
    words = _words(text)
    return max(places, key=lambda f: len(words & _words(f.text)), default=None)


@dataclass
class Claim:
    text: str  # the sentence or panel text
    where: str  # scene id
    numbers: list[tuple[str, Found | None]]


def source_numbers(text: str, where: str, index: dict[Any, list[Found]]) -> Claim | None:
    found = [(n, best_place(n, text, index)) for n in dict.fromkeys(numbers_in(text))]
    return Claim(plain(text), where, found) if found else None


# --- the talk file -----------------------------------------------------------------------------------------------------


@dataclass
class Line:
    who: str  # her or him
    text: str
    view: tuple[str, str]  # (directive kind, its arguments); ("still", "") by default
    pause: float
    lineno: int


@dataclass
class Section:
    heading: str
    place: str
    lines: list[Line]


@dataclass
class Talk:
    title: str
    job: str
    sections: list[Section]


_DIRECTIVE = re.compile(r"^\[(\w+)\s*(.*?)\]\s*$")
_SPEAK = re.compile(r"^(Lan|Minh)\s*:\s*(.+)$", re.I)
_PAUSE = re.compile(r"\s*\[pause\s+(\d+(?:\.\d+)?)\]\s*$")


def parse_talk(text: str) -> Talk:
    title = job = ""
    sections: list[Section] = []
    view = ("still", "")
    last: Line | None = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("<!--") or line.startswith("# "):
            last = None if not line else last
            continue
        if not sections and (m := re.match(r"^(Title|Job)\s*:\s*(.+)$", line)):
            if m.group(1) == "Title":
                title = m.group(2).strip()
            else:
                job = m.group(2).strip()
            continue
        if line.startswith("## "):
            m = re.match(r"^##\s+(.+?)\s*(?:\{(\w+)\})?$", line)
            assert m
            place = (m.group(2) or ROTATION[len(sections) % len(ROTATION)]).lower()
            if place not in PLACES:
                raise ExplainerError(f"talk line {n}: unknown place {{{place}}}; use {', '.join(PLACES)}")
            sections.append(Section(m.group(1), place, []))
            view, last = ("still", ""), None
            continue
        if not sections:
            raise ExplainerError(f"talk line {n}: text before the first '## ' section")
        if m := _DIRECTIVE.match(line):
            kind = m.group(1).lower()
            if kind not in (*PANELS, "card", "still"):
                raise ExplainerError(f"talk line {n}: unknown directive [{kind}]")
            view, last = (kind, m.group(2).strip()), None
            continue
        if m := _SPEAK.match(line):
            body, pause = m.group(2).strip(), 0.0
            if p := _PAUSE.search(body):
                pause, body = float(p.group(1)), body[:p.start()].rstrip()
            if re.search(r"\bTODO\b", body):
                raise ExplainerError(f"talk line {n}: write the TODO line before building")
            last = Line(SPEAKERS[m.group(1).lower()], body, view, pause, n)
            sections[-1].lines.append(last)
            continue
        if last is not None:  # a wrapped line continues the previous speech
            last.text += " " + line
            continue
        raise ExplainerError(f"talk line {n}: start with 'Lan:', 'Minh:', '[directive]' or '## section' ({line[:40]!r})")
    empty = [s.heading for s in sections if not s.lines]
    if not sections or empty:
        raise ExplainerError(f"the talk needs sections with lines (empty: {', '.join(empty) or 'no sections'})")
    return Talk(title=title, job=job, sections=sections)


def load_talk(path: str | Path) -> Talk:
    try:
        return parse_talk(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExplainerError(f"cannot read {path}: {exc}") from exc


# --- panels ----------------------------------------------------------------------------------------------------------


def _range(text: str, what: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", text)
    if not m:
        raise ExplainerError(f"{what}: write a line range as A-B, not {text!r}")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if b < a:
        raise ExplainerError(f"{what}: the range {text} runs backwards")
    return a, b


def _file(source: Source, name: str, what: str) -> Path:
    path = (source.folder / name).resolve()
    if not path.is_file() or source.folder.resolve() not in path.parents:
        raise ExplainerError(f"{what}: no file {name} next to the source")
    return path


@dataclass
class Panel:
    spec: dict[str, Any]
    media: Path | None = None  # a picture to copy into the job (image panels)
    checked: list[str] = field(default_factory=list)  # panel text whose numbers must come from the source
    claims: list[str] = field(default_factory=list)  # file claims for the fact check (code and output cards)


def panel_for(kind: str, args: str, source: Source, what: str) -> Panel:
    parts = [p.strip() for p in args.split("|")]
    head, rest = parts[0], [p for p in parts[1:] if p]
    note = next((p[len("source "):].strip() for p in rest if p.lower().startswith("source ")), "")
    rest = [p for p in rest if not p.lower().startswith("source ")]
    if kind in ("code", "output"):
        m = re.fullmatch(r"(\S+)\s+(\d+(?:-\d+)?)(?:\s+hl\s+([\d,\s-]+))?", head)
        if not m:
            raise ExplainerError(f"{what}: write [{kind} FILE A-B] or [{kind} FILE A-B hl C-D | title]")
        path = _file(source, m.group(1), what)
        a, b = _range(m.group(2), what)
        if b - a + 1 > MAX_CODE_LINES:
            raise ExplainerError(f"{what}: {b - a + 1} lines; show at most {MAX_CODE_LINES} per card")
        lines = path.read_text(encoding="utf-8").splitlines()
        if b > len(lines):
            raise ExplainerError(f"{what}: {path.name} has {len(lines)} lines, not {b}")
        highlight = [list(_range(h.strip(), what)) for h in (m.group(3) or "").split(",") if h.strip()]
        if any(h[0] < a or h[1] > b for h in highlight):
            raise ExplainerError(f"{what}: a highlight lies outside lines {a}-{b}")
        title = rest[0] if rest else (path.name if kind == "code" else f"Output of {path.stem}")
        spec = {"type": kind, "title": title, "lines": lines[a - 1:b], "first_line": a, "highlight": highlight,
                "source": note or f"{path.name}, lines {a}-{b}" if a != b else note or f"{path.name}, line {a}"}
        claim = (f"Code card: {path.name} lines {a}-{b}, shown unchanged" if kind == "code" else
                 f"Output card: {path.name} lines {a}-{b} equal a fresh run of its script")
        return Panel(spec, checked=[title], claims=[f"{claim} - source: {path.name}:{a}"])
    if kind == "image":
        path = _file(source, head, what)
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ExplainerError(f"{what}: an image panel shows a PNG or JPEG")
        title = rest[0] if rest else ""
        spec = {"type": "image", "src": f"media/{path.name}", "source": note or path.name}
        if title:
            spec["title"] = title
        return Panel(spec, media=path, checked=[title])
    default_note = note or (source.path.name if source.kind == "lesson" else "post.md")
    if kind == "chart":
        bars, top, chart_note = [], None, ""
        for p in rest:
            if p.lower().startswith("max "):
                top = float(p[4:].strip())
            elif p.lower().startswith("note "):
                chart_note = p[5:].strip()
            elif m := re.fullmatch(r"(.+?)\s*=\s*(\d+(?:\.\d+)?)(?:\s*\((.+)\))?", p):
                bar = {"label": m.group(1).strip(), "value": float(m.group(2))}
                if m.group(3):
                    bar["text"] = m.group(3).strip()
                bars.append(bar)
            else:
                raise ExplainerError(f"{what}: a chart item is 'label = value (text)', 'max N' or 'note TEXT': {p!r}")
        if not bars:
            raise ExplainerError(f"{what}: a chart needs at least one 'label = value' bar")
        spec = {"type": "chart", "title": head, "bars": bars, "source": default_note}
        if top:
            spec["max"] = top
        if chart_note:
            spec["note"] = chart_note
        checked = [head, chart_note, *(f"{b['label']}: {_num(b['value'])} ({b.get('text', '')})" for b in bars),
                   f"{head}: full bar {_num(top)}" if top else ""]
        return Panel(spec, checked=checked)
    if kind == "points":
        if not rest:
            raise ExplainerError(f"{what}: [points TITLE | item | item]")
        return Panel({"type": "points", "title": head, "items": rest, "source": default_note}, checked=[head, *rest])
    if kind == "flow":
        if len(rest) != 1 or "->" not in rest[0]:
            raise ExplainerError(f"{what}: [flow TITLE | a -> b, c -> d]")
        columns = [[box.strip() for box in col.split(",") if box.strip()] for col in rest[0].split("->")]
        return Panel({"type": "flow", "title": head, "columns": columns, "source": default_note},
                     checked=[head, *(b for col in columns for b in col)])
    raise ExplainerError(f"{what}: unknown panel {kind!r}")


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def card_board(args: str) -> dict[str, Any]:
    parts = [p.strip() for p in args.split("|") if p.strip()]
    if not parts:
        raise ExplainerError("[card TITLE | line | line]: the card needs a title")
    return {"title": parts[0], "lines": [{"style": "bullet", "text": p} for p in parts[1:]]}


# --- scenes ----------------------------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:24].strip("-") or "part"


def video_title(source: Source, talk: Talk) -> str:
    title = talk.title or (f"Lab to Prod: {source.title}" if source.kind == "post" else source.title)
    return title if len(title) <= 100 else title[:97].rstrip() + "..."


def job_name(source: Source, talk: Talk) -> str:
    job = talk.job or (f"en-{source.slug}-explainer" if source.kind == "post" else f"en-{source.date}-{source.slug}-explainer")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*-explainer", job) or len(job) > 64:
        raise ExplainerError(f"job {job!r}: lower-case words and hyphens, at most 64 characters, ending in -explainer")
    return job


def fits_one_cue(sentence: str) -> bool:
    from .subtitles import LANDSCAPE_CHARS, pieces

    return len(pieces(sentence, LANDSCAPE_CHARS)) <= 1


@dataclass
class Built:
    doc: dict[str, Any]
    claims: list[Claim]
    file_claims: list[str]
    media: list[Path]


def build_scenes(source: Source, talk: Talk, library: Library, stills: str = "library") -> Built:
    """The scenes.json of the talk: one scene per sentence, a chapter per section, the illustrated look for plain
    lines and a full-frame card while a panel directive is on. Raises on a number with no source."""
    index = number_index(source.texts)
    claims: list[Claim] = []
    file_claims: list[str] = []
    media: list[Path] = []
    scenes: list[dict[str, Any]] = []
    lines_for_stills: list[tuple[str, str, str]] = []
    panels: dict[tuple[str, str], Panel] = {}
    used: set[str] = set()
    too_long: list[str] = []
    for k, section in enumerate(talk.sections):
        key = _slug(section.heading)
        while key in used:
            key += "-x"
        used.add(key)
        chapter_pending = True
        count = 0
        for line in section.lines:
            what = f"talk line {line.lineno}"
            kind, args = line.view
            panel = board = None
            if kind in PANELS:
                if (kind, args) not in panels:
                    panels[(kind, args)] = panel_for(kind, args, source, what)
                    p = panels[(kind, args)]
                    file_claims += [c for c in p.claims if c not in file_claims]
                    if p.media and p.media not in media:
                        media.append(p.media)
                    for text in p.checked:
                        if text and (c := source_numbers(text, f"{key} panel", index)):
                            claims.append(c)
                panel = panels[(kind, args)]
            elif kind == "card":
                board = card_board(args)
                for item in [board["title"], *(ln["text"] for ln in board["lines"])]:
                    if c := source_numbers(item, f"{key} card", index):
                        claims.append(c)
            if line.pause and panel is not None:
                raise ExplainerError(f"{what}: a [pause] needs the still look; put [still] or [card ...] before it")
            for sentence in split_lines(line.text):
                if not fits_one_cue(plain(sentence)):
                    too_long.append(f"{what}: {plain(sentence)!r}")
                count += 1
                sid = f"{key}-{count}"
                visual: dict[str, Any] = {"talk": line.who, "part": f"{k + 1} · {section.heading}", "label": AI_LABEL,
                                          "caption": {"text": " ".join(sentence.split())}}
                if panel is not None:
                    visual["panel"] = panel.spec
                if board:
                    visual["board"] = board
                scene: dict[str, Any] = {"id": sid, "speaker": line.who, "narration": {"en": plain(sentence)},
                                         "duration_s": 1, "visual": visual}
                if chapter_pending:
                    scene["chapter"] = {"en": section.heading}
                    chapter_pending = False
                if c := source_numbers(sentence, sid, index):
                    claims.append(c)
                scenes.append(scene)
                lines_for_stills.append((key, section.place, line.who))
            if line.pause:
                last = scenes[-1]["visual"]
                last["pause"] = {"scale": 0.0, "add_s": line.pause, "cue": PAUSE_CUE}
    if too_long:
        raise ExplainerError(f"{len(too_long)} sentence(s) too long for one subtitle cue (two rows of 42 characters; "
                             "split them): " + "; ".join(too_long))
    unsourced = [(n, c) for c in claims for n, f in c.numbers if f is None]
    if unsourced:
        rows = "; ".join(f"{n} in {c.where} ({c.text[:50]!r})" for n, c in unsourced[:8])
        raise ExplainerError(f"{len(unsourced)} number(s) with no source in {', '.join(source.texts)}: {rows}")
    for scene, shot_id in zip(scenes, pick_shots(lines_for_stills, library)):
        flat = scene["visual"]
        entry = library.shots[shot_id]
        visual = {"kind": "card" if "panel" in flat else "illustrated", "image": f"{stills}/{entry['file']}",
                  "focus": focus_for(entry, flat["talk"]), "talk": flat["talk"], "name": NAMES[flat["talk"]]}
        for k2 in ("part", "label", "caption", "board", "pause", "panel"):
            if k2 in flat:
                visual[k2] = flat[k2]
        visual["badge"] = AI_BADGE
        scene["visual"] = visual
    voice = lambda who: {"en": {"engine": "kokoro", "voice": VOICES[who]}}  # noqa: E731
    doc = {
        "version": 1,
        "title": {"en": video_title(source, talk)},
        "languages": ["en"],
        "output": {"width": 1920, "height": 1080, "fps": 30},
        "theme": "light",
        "speakers": {HER: voice(HER), HIM: voice(HIM)},
        "scenes": scenes,
    }
    return Built(doc=doc, claims=claims, file_claims=file_claims, media=media)


# --- the job source folder -------------------------------------------------------------------------------------------


def explainer_voices(kind: str) -> dict[str, Any]:
    name, title, description = PLAYLISTS[kind]
    doc = voices_config()
    doc["presets"]["lesson"]["description"] = "Explainer: Lan (her) and Minh (him), preset Kokoro voices, never the clone."
    doc["playlists"] = {"presets": {name: {"title": title, "description": description, "privacy": "public"}}}
    return doc


def topic_file(job: str, source: Source, shorts: int = SHORTS) -> dict[str, Any]:
    return {
        "version": 1,
        "job": job,
        "topic": f"Explainer {source.date}: {source.title}"[:200],
        "scenes": "scenes.json",
        "factcheck": "factcheck.md",
        "languages": ["en"],
        "cast": {"en": {"preset": "lesson"}},
        "voices": "voices.json",
        "shorts": shorts,
        "playlist": PLAYLISTS[source.kind][0],
        "gate": {"min_s": GATE_WINDOW[0], "max_s": GATE_WINDOW[1]},
    }


def draft_factcheck(source: Source, built: Built) -> str:
    """Every number with the file and line it came from, unchecked, so the gate stops until a person checks them."""
    doc = built.doc
    out = [f"# Fact check: {doc['title']['en']}", "", "Reviewed-by:", f"Date: {source.date}",
           f"Narration: {narration_hash(doc)[:12]}", ""]
    if source.kind == "post":
        out.append(f"Source: the lab-to-prod post {source.path.name} ({source.url}) and its merged work.")
    else:
        out.append(f"Source: the study lesson {source.path.name}.")
    out += ["Check each claim against its source, then tick it. The builder found every number below in the file and",
            "line it names; check that the sentence uses it in the same sense.", ""]
    for entry in source.work:
        out.append(f"- [ ] The work behind the post: {entry['id']} in {entry.get('repo', '?')}, merged "
                   f"{entry.get('merged', '?')}: {entry.get('what', '')} - source: {entry.get('ref', '?')}")
    out += [f"- [ ] {c}" for c in built.file_claims]
    seen: set[tuple[str, str]] = set()
    for claim in built.claims:
        key = (claim.text, ",".join(n for n, _ in claim.numbers))
        if key in seen:
            continue
        seen.add(key)
        refs = ", ".join(f"{n} at {f.ref()}" for n, f in claim.numbers if f)
        out.append(f"- [ ] \"{claim.text}\" ({claim.where}) - source: {refs}")
    return "\n".join(out) + "\n"


def stills_used(doc: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(s["visual"]["image"] for s in doc["scenes"]))


@dataclass
class Written:
    folder: Path
    topic: Path
    scenes: Path
    factcheck_ready: bool
    narration: str
    job: str
    count: int


def write_source(source_path: str | Path, talk_path: str | Path, out: Path, library: Path | None = None,
                 shorts: int = SHORTS) -> Written:
    """Write scenes.json, topic.json, voices.json, factcheck.md (the reviewed one next to the talk file when it
    exists, else a draft), script.md, the stills and the panel pictures into ``out``."""
    from .pipeline import script_markdown

    source, talk = load_source(source_path), load_talk(talk_path)
    lib = load_library(library)
    built = build_scenes(source, talk, lib)
    job = job_name(source, talk)
    out.mkdir(parents=True, exist_ok=True)
    (out / "library").mkdir(exist_ok=True)
    for rel in stills_used(built.doc):
        shutil.copy2(lib.folder / Path(rel).name, out / "library" / Path(rel).name)
    if built.media:
        (out / "media").mkdir(exist_ok=True)
        for path in built.media:
            shutil.copy2(path, out / "media" / path.name)
    _write_json(out / "scenes.json", built.doc)
    _write_json(out / "topic.json", topic_file(job, source, shorts))
    _write_json(out / "voices.json", explainer_voices(source.kind))
    (out / "script.md").write_text(script_markdown(built.doc, "en"), encoding="utf-8")
    talk_path = Path(talk_path)
    reviewed = talk_path.with_name(talk_path.name.replace(".talk.md", "") + ".factcheck.md")
    if reviewed.is_file():
        shutil.copy2(reviewed, out / "factcheck.md")
    else:
        (out / "factcheck.md").write_text(draft_factcheck(source, built), encoding="utf-8")
    return Written(folder=out, topic=out / "topic.json", scenes=out / "scenes.json", factcheck_ready=reviewed.is_file(),
                   narration=narration_hash(built.doc)[:12], job=job, count=len(built.doc["scenes"]))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- a draft talk --------------------------------------------------------------------------------------------------


def _functions(path: Path) -> list[tuple[str, int, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []
    return [(n.name, n.lineno, n.end_lineno or n.lineno) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def draft_talk(source: Source) -> str:
    """A starting talk: the sections, the source's own sentences and its code and output cards; every place a person
    must write is a ``TODO`` line, which the builder refuses until it is written."""
    out = [f"Title: {video_title(source, Talk('', '', []))}", "", "## Intro {classroom}",
           "Lan: Hello, and welcome. I'm Lan.", "Minh: And I'm Minh.",
           "Lan: Both of our voices are AI voices, and the pictures are made with AI.", ""]
    if source.kind == "post":
        _, body = front_matter(source.texts["post.md"])
        note = body.split("## linkedin", 1)[1].split("\n## ", 1)[0] if "## linkedin" in body else body
        out += ["## The problem {cafe}"]
        if (source.folder / "image.png").is_file():
            out.append("[image image.png]")
        for n, sentence in enumerate(s for s in split_lines(note) if not s.startswith("#")):
            out.append(f"{'Lan' if n % 2 == 0 else 'Minh'}: {sentence}")
        out += ["", "## The code {desk}"]
        for script in sorted(source.folder.glob("*.py")):
            for name, a, b in _functions(script):
                out += [f"[code {script.name} {a}-{min(b, a + MAX_CODE_LINES - 1)} | {name}()]",
                        f"Minh: TODO what {name}() does."]
        out += ["", "## The number {classroom}"]
        for expected in sorted(source.folder.glob("*.expected")):
            n_lines = len(expected.read_text(encoding="utf-8").splitlines())
            out += [f"[output {expected.name} 1-{min(n_lines, MAX_CODE_LINES)}]", "Lan: TODO what the output shows."]
        out += ["", "## The limit {cafe}", "[still]", "Minh: TODO what it does not show.", "",
                "## Recap {classroom}", "Lan: TODO the lesson in one line.", "Minh: See you in the next one."]
    else:
        sections = lesson_sections(source.texts[source.path.name])
        for title in LESSON_SECTIONS[:3]:
            out += [f"## {title.capitalize()}", f"Lan: TODO teach the {title}.", ""]
        out += ["## Quiz {exam}", "[still]"]
        quiz = next(v for k, v in sections.items() if k.startswith("quiz"))
        for n, (q, a) in enumerate(quiz_items(quiz), 1):
            out += [f"Lan: Question {n}. {q} [pause 8]", f"Minh: {a}"]
        out += ["", "## Recap {classroom}", "Lan: TODO the lesson in one line.", "Minh: See you in the next one."]
    return "\n".join(out) + "\n"


# --- after the render: the description -------------------------------------------------------------------------------


def description_intro(source: Source) -> str:
    lines = []
    if source.kind == "post":
        lines.append(f"The project work behind the Lab to Prod post \"{source.title}\", explained by two characters, "
                     f"{NAMES[HER]} and {NAMES[HIM]}: the problem, the code that runs, the measured number and its limit.")
        lines.append(f"Post: {source.url}")
        for entry in source.work:
            if entry.get("ref", "").startswith("http"):
                lines.append(f"The work ({entry['id']}, merged {entry.get('merged', '?')}): {entry['ref']}")
    else:
        lines.append(f"A study lesson explained by two characters, {NAMES[HER]} and {NAMES[HIM]}: {source.title}. "
                     "The concept, a worked example, the edge cases and a short quiz; answer aloud in the pauses.")
    lines.append(f"{AI_LABEL}: both voices are synthetic (Kokoro {VOICES[HER]} and {VOICES[HIM]}); the characters "
                 "are original anime stills made with AI (Animagine XL 3.1 on a local GPU); the cards are drawn by code.")
    return "\n\n".join(lines)


def write_description(source: Source, job: Path, name: str) -> tuple[Path, Path]:
    """After a render: ``<name>.en.intro.txt`` (top text for stage-upload) and ``<name>.en.description.txt`` (with
    the chapter timestamps)."""
    from .publish import chapters, describe

    video = job / f"{name}.en.mp4"
    doc = json.loads((job / "scenes.json").read_text(encoding="utf-8"))
    timeline = json.loads(video.with_suffix(".timeline.json").read_text(encoding="utf-8"))
    marks = chapters(doc, timeline, "en", doc["title"]["en"])
    intro = job / f"{name}.en.intro.txt"
    intro.write_text(description_intro(source) + "\n", encoding="utf-8")
    desc = job / f"{name}.en.description.txt"
    desc.write_text(describe(description_intro(source), marks) + "\n", encoding="utf-8")
    return intro, desc
