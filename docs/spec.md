# Spec: long-form explainer video pipeline

Status: draft for review. Nothing here is built yet.

## Goal

Produce 20-30 minute English videos about AI/ML and topics being studied,
with self-designed characters (anime-style or robot). Characters are original;
nothing is copied from other channels. Each video also yields a blog post and
a short post (see "Link to lab-to-prod").

## Formats

1. **Two-character dialogue** explaining one technique (e.g. RAG). One
   character asks, the other explains; scenes alternate between a character
   shot and a simple diagram.
2. **Narrated screen recording** of a notebook or code demo, with one voice,
   zooms on the relevant lines, and sentence subtitles.
3. **Animated architecture diagrams**: boxes and arrows built from a data
   description, animated step by step with narration.

## Pipeline

| # | Step | Input -> output | Free-first tool |
|---|------|-----------------|-----------------|
| 1 | Script | topic + outline -> `script.json` | LLM (offline/mock mode in tests) |
| 2 | Scene images | `script.json` + character sheet -> one image per scene | local image model or hand-drawn/SVG assets; paid services need a yes first |
| 3 | Voice | each line -> WAV/MP3, two voices | edge-tts, or Kokoro (local) |
| 4 | Assembly | images + audio + subtitles -> MP4 | Remotion or ffmpeg (Ken Burns, title cards, sentence subtitles, chapters) |
| 5 | Extras | MP4 + script -> thumbnail, description, chapter list | ffmpeg frame grab + template |

### `script.json` (sketch)

```json
{
  "title": "RAG explained",
  "characters": {"A": {"voice": "..."}, "B": {"voice": "..."}},
  "scenes": [
    {"id": 1, "image_prompt": "...", "chapter": "Intro",
     "lines": [{"who": "A", "text": "..."}]}
  ]
}
```

Character consistency: a fixed character sheet (description + reference
image) is prepended to every image prompt; seeds are stored in the JSON.

## Quality checks per step

1. Script: JSON schema valid; every line has a known speaker; length matches
   the target runtime (about 150 words per minute); factual claims reviewed by
   Nghiep.
2. Images: one per scene; resolution 1920x1080; spot-check character
   consistency on a contact sheet.
3. Voice: no clipped audio; per-line duration recorded; loudness normalised.
4. Assembly: audio/subtitle drift under 200 ms; no black frames; chapters
   match scene boundaries; output plays in a standard player.
5. Extras: thumbnail readable at small size; description lists chapters.

## Link to lab-to-prod

Each video also yields a blog post and a short post, generated from the same
`script.json`. Nghiep reviews both, and the video, before anything goes to
YouTube. The pipeline never publishes by itself.

## Cost and constraints

- Free tools first. If a paid API or image service is needed, state the
  estimated monthly cost and stop for Nghiep's yes.
- No publishing from automation.
- No material from any employer or client.
- Rendered output stays out of git (add to `.gitignore`); at most a small
  preview may be committed.

## First trial: 2-3 minute RAG dialogue

Plan (tracked in issue 2):

1. Hand-write or generate a `script.json` of about 400 words: two characters
   discuss what RAG is, retrieval, then generation.
2. Voices: edge-tts with two voices (Kokoro as fallback if offline is needed).
3. Images: 6-10 simple original character/diagram scenes.
4. Assembly with ffmpeg first (fewer dependencies), subtitles per sentence.
5. One command renders the MP4 locally.

Success: Nghiep can watch the clip and judge voice, visuals and pacing before
any long video is made.

## Tool choices and reasons

- **edge-tts / Kokoro**: free; edge-tts is quick to start, Kokoro runs locally.
  Trade-off: edge-tts needs network access.
- **ffmpeg first, Remotion later**: ffmpeg is enough for Ken Burns, titles and
  subtitles in the trial; Remotion becomes worth it when animated diagrams
  (format 3) need reusable components.
- **JSON script as the single source**: one file drives video, blog post and
  short post, and is easy to review and diff.

## Open questions for Nghiep

- Character style: anime-style or robot first?
- Image source for scenes: local model, hand-made SVG, or a paid service (cost
  estimate to follow before any use)?
- Confirm edge-tts is acceptable for the trial, given it needs network.
