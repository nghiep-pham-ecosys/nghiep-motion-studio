"""Choose the voice of every scene: per-scene voice, speaker, then the ``voice_mix`` policy.

scenes.json can name voices (``speakers``), give a scene a ``speaker`` or its
own ``voice``, and set a ``voice_mix`` policy for the scenes that name none:

* ``{"policy": "narrator", "narrator": "owner"}``: every scene without a
  speaker is read by ``owner`` (for example the owner's cloned voice), and a
  scene with ``"speaker": "guest"`` by a second voice;
* ``{"policy": "alternate-chapters", "speakers": ["owner", "guest"]}``: the
  speakers take turns by chapter (a scene with ``chapter`` starts a new one).

For one scene and language the first match wins: the scene's ``voice``, its
``speaker`` (explicit, else from the policy), then the default passed on the
command line (``--engine`` and ``--voice``). Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

POLICIES = ("narrator", "alternate-chapters")


@dataclass(frozen=True)
class VoiceChoice:
    engine: str
    voice: str
    speaker: str | None = None  # the speaker id from scenes.json, if a speaker chose the voice
    source: str = "default"  # "scene", "speaker", "voice_mix" or "default"


def scene_speakers(doc: dict[str, Any]) -> list[str | None]:
    """The speaker of every scene: its own ``speaker``, else the one the ``voice_mix`` policy gives."""
    mix = doc.get("voice_mix") or {}
    policy = mix.get("policy")
    out: list[str | None] = []
    chapter = -1
    for n, scene in enumerate(doc["scenes"]):
        if n == 0 or "chapter" in scene:
            chapter += 1
        if "speaker" in scene:
            out.append(scene["speaker"])
        elif policy == "narrator":
            out.append(mix.get("narrator"))
        elif policy == "alternate-chapters" and mix.get("speakers"):
            out.append(mix["speakers"][chapter % len(mix["speakers"])])
        else:
            out.append(None)
    return out


def _from_policy(doc: dict[str, Any], index: int) -> bool:
    return "speaker" not in doc["scenes"][index] and bool(doc.get("voice_mix"))


def plan_voices(doc: dict[str, Any], lang: str, default: tuple[str, str] | None = None) -> list[VoiceChoice]:
    """One VoiceChoice per scene for ``lang``. Raises ValueError naming every scene left without a voice."""
    speakers = doc.get("speakers", {})
    choices: list[VoiceChoice] = []
    missing: list[str] = []
    for n, (scene, speaker) in enumerate(zip(doc["scenes"], scene_speakers(doc))):
        own = scene.get("voice", {}).get(lang)
        by_speaker = speakers.get(speaker, {}).get(lang) if speaker else None
        if own:
            choices.append(VoiceChoice(own["engine"], own["voice"], speaker, "scene"))
        elif by_speaker:
            source = "voice_mix" if _from_policy(doc, n) else "speaker"
            choices.append(VoiceChoice(by_speaker["engine"], by_speaker["voice"], speaker, source))
        elif default is not None:
            choices.append(VoiceChoice(default[0], default[1], speaker, "default"))
        else:
            who = f" (speaker {speaker!r} has no {lang} voice)" if speaker else ""
            missing.append(f"{scene['id']}{who}")
    if missing:
        raise ValueError(
            f"no {lang} voice for scene(s) {', '.join(missing)}; pass --engine and --voice, "
            f"or set speakers and voice_mix in scenes.json"
        )
    return choices


def mix_errors(doc: dict[str, Any], engines: set[str] | None = None) -> list[str]:
    """Cross-field checks of speakers, scene speakers and voice_mix (the JSON Schema checks the shapes)."""
    errs: list[str] = []
    languages = doc["languages"]
    speakers = doc.get("speakers", {})

    def check_voices(value: dict[str, Any], where: str) -> None:
        extra = [lang for lang in value if lang not in languages]
        if extra:
            errs.append(f"{where}: language(s) {', '.join(extra)} not listed in /languages")
        for lang, ref in value.items():
            if engines is not None and ref["engine"] not in engines:
                errs.append(f"{where}/{lang}/engine: unknown TTS engine {ref['engine']!r} (use {', '.join(sorted(engines))})")

    for sid, value in speakers.items():
        check_voices(value, f"/speakers/{sid}")
    for i, scene in enumerate(doc["scenes"]):
        if "speaker" in scene and scene["speaker"] not in speakers:
            errs.append(f"/scenes/{i}/speaker: unknown speaker {scene['speaker']!r}; define it in /speakers")
        if "voice" in scene:
            check_voices(scene["voice"], f"/scenes/{i}/voice")
    mix = doc.get("voice_mix")
    if mix:
        policy = mix["policy"]
        if policy == "narrator":
            if "narrator" not in mix:
                errs.append("/voice_mix: the narrator policy needs narrator (a speaker id)")
            if "speakers" in mix:
                errs.append("/voice_mix/speakers: only the alternate-chapters policy takes speakers")
        elif policy == "alternate-chapters":
            if "speakers" not in mix:
                errs.append("/voice_mix: the alternate-chapters policy needs speakers (two or more speaker ids)")
            if "narrator" in mix:
                errs.append("/voice_mix/narrator: only the narrator policy takes narrator")
        named = ([mix["narrator"]] if "narrator" in mix else []) + list(mix.get("speakers", []))
        for name in named:
            if name not in speakers:
                errs.append(f"/voice_mix: unknown speaker {name!r}; define it in /speakers")
    return errs
