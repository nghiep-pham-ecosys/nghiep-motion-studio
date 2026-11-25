# Voices: TTS models, licences and setup

Narration comes from local, free text-to-speech. Nothing is sent to an online
service. Every licence below was checked on **2026-11-25** against the
primary source (LICENSE file, model card, base model and dataset cards). Check
again before a model or version changes.

Rule for this channel: the code licence, the weights licence and any
non-commercial term inherited from a base model or dataset must all allow
commercial use, because the channel is monetised. Otherwise the model is
marked **not usable**.

## Engines in use

| Engine (`--engine`) | Language | Model | Code / weights licence | Monetised channel | Voice cloning |
|---|---|---|---|---|---|
| `kokoro` | English | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) via `kokoro==0.9.4` | Apache-2.0 / Apache-2.0 | **usable**, with the AI-voice disclosure below | no |
| `vieneu` | Vietnamese (+ English words) | [VieNeu-TTS v3 Turbo](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo) via `vieneu==3.8.4` | Apache-2.0 / Apache-2.0 | **usable** (preset voices listed below) | yes |
| `clone` | Vietnamese | VieNeu-TTS v3 Turbo, zero-shot clone of a local reference clip | as `vieneu` | **usable** for your own voice only | yes |
| `clone-en` | English | [Chatterbox-Turbo](https://huggingface.co/ResembleAI/chatterbox-turbo) (voice `clone`) or [Chatterbox](https://huggingface.co/ResembleAI/chatterbox) (voice `clone-standard`) via `chatterbox-tts==0.1.7`, zero-shot clone of a local reference clip | MIT / MIT | **usable** for your own voice only, with the disclosure below | yes |
| `mock` | any | test tone, no model | n/a | not for videos | no |

### Kokoro-82M (English)

- Licence: Apache-2.0 for the code ([kokoro](https://github.com/hexgrad/kokoro/blob/main/LICENSE),
  [misaki](https://github.com/hexgrad/misaki)) and the weights ([model card](https://huggingface.co/hexgrad/Kokoro-82M)).
  The card says the model "can be deployed anywhere from production environments to personal projects".
- GPL parts: `phonemizer-fork` and the espeak-ng library inside `espeakng-loader` are GPL-3.0. They run
  locally and are never redistributed; the GPL does not cover program output
  ([GPL FAQ](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput)).
- Disclosure: the card names [StyleTTS2-LJSpeech](https://huggingface.co/yl4579/StyleTTS2-LJSpeech) as base
  model, and [StyleTTS2](https://github.com/yl4579/StyleTTS2#license) asks users of its pre-trained models to tell
  listeners the speech is synthesised. Every video description therefore says "Narration: AI-generated voice
  (Kokoro-82M, Apache-2.0)". This also meets the repo rule "made with AI".
- Residual risk: the card says part of the training audio was made by closed TTS systems, without saying which
  voices. Open questions about per-voice provenance ([#379](https://github.com/hexgrad/kokoro/issues/379),
  [#385](https://github.com/hexgrad/kokoro/issues/385)) had no maintainer reply on 2026-11-25. No licence forbids
  monetised use; this is a provenance risk, not a licence bar.
- Voices offered by the adapter: `af_heart`, `af_bella`, `am_michael`, `am_fenrir`, `bf_emma`, `bm_george`.

### VieNeu-TTS v3 Turbo (Vietnamese)

- Licence: Apache-2.0 for the SDK ([LICENSE](https://github.com/pnnbao97/VieNeu-TTS/blob/main/LICENSE)), the weights
  and the codec [MOSS-Audio-Tokenizer-Nano](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano).
  The [model card](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo) states that audio made with the preset
  voices may be used in commercial and monetised content, and that the preset speakers consented. These are the
  author's statements; the card describes the 10k-hour training set (VieNeu-TTS-10k-ENVI) as gated (anonymous
  requests got HTTP 401 on 2026-11-25), and its sources are not public.
- Preset voices: the card lists 23 presets (documented for SDK 3.7.1); the pinned SDK 3.8.4 ships 25, three of
  them renamed (Thiện Minh = Anh Khôi, Hải Đăng = Minh Quân, Quốc Tuấn = Mạnh Dũng). The adapter hides presets
  that must not be used (`BLOCKED_PRESETS` in `src/motion_studio/tts/vieneu_tts.py`):
  - **Ngọc Huyền** and **Quốc Tuấn / Mạnh Dũng**: these names are voices of a commercial Vietnamese TTS service
    ([Vbee voice list](https://vbee.vn/blog/languages/page/5/)), and the author's own LoRA for Ngọc Huyền is labelled
    "Ngọc Huyền (Vbee)" ([model card](https://huggingface.co/pnnbao-ump/VieNeu-TTS-0.3B-lora-ngoc-huyen)). Treat
    them as copies of a commercial voice.
  - "Adam bựa", "Thiền Tâm Đức": in the SDK but not on the card.
  - Phạm Tuyên, Xuân Vĩnh, Đoan Trang: names of well-known public figures.
- The comparison page uses **Trúc Ly** (female, northern), **Minh Đức** (male, northern) and **Thùy Dung** (female,
  southern). They are on the card and were not found in the partial Vbee list above; that check is not exhaustive.
- Cloning: zero-shot, no transcript needed. The engine reads at most the first 8 s of the reference, so give it a
  clean 6-8 s clip of continuous speech (16-bit or float WAV). Only clone a voice you have the rights to; the card
  forbids cloning other people without permission and forbids impersonation. A bad reference clip only disables
  the clone voice; error messages name the file, never its folder.
- Safety note: on the GPU path the SDK loads the codec with `trust_remote_code=True`, which runs Python code
  downloaded from the codec's Hugging Face repo. Its code has not been audited here: pin a revision or read the
  downloaded files in the Hugging Face cache before upgrading.
- Older VieNeu models are **not usable**: v1 and 0.3B were trained on a CC BY-NC dataset by the same author
  ([VieNeu-TTS-1000h](https://huggingface.co/datasets/pnnbao-ump/VieNeu-TTS-1000h)); the 0.3B GGUF weights are
  CC BY-NC 4.0; v2 has no commercial statement and a codec trained partly on CC BY-NC data. v4 is API-only.

### Chatterbox and Chatterbox-Turbo (English clone)

Checked on **2026-11-25** against the primary sources.

- Licence: MIT for the code ([LICENSE](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE), PyPI
  `chatterbox-tts` 0.1.7) and for both weight repos (model cards
  [chatterbox](https://huggingface.co/ResembleAI/chatterbox) and
  [chatterbox-turbo](https://huggingface.co/ResembleAI/chatterbox-turbo), tag `license: mit`, not gated). MIT allows
  commercial use. The adapter downloads a pinned commit of each repo (`MODELS` in
  `src/motion_studio/tts/chatterbox_tts.py`).
- Inherited parts: the speech tokenizer comes from CosyVoice
  ([code Apache-2.0](https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE),
  [CosyVoice2-0.5B weights Apache-2.0](https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B)) through
  [S3Tokenizer](https://github.com/xingchensong/S3Tokenizer) (Apache-2.0); the watermarker
  [Perth](https://github.com/resemble-ai/Perth) is MIT; `conformer` is MIT. The card thanks Llama 3, but only its
  architecture is used: the backbone is a 520M Llama-style network (1024 wide, 30 layers, in `llama_configs.py`)
  that no Meta checkpoint fits, so the Llama licence does not reach the weights. No non-commercial term was found.
- Training data: "0.5M hours of cleaned data" (model card), sources not public. As with Kokoro and VieNeu, this is a
  provenance risk, not a licence bar.
- Watermark: every line carries Resemble AI's Perth watermark (imperceptible, made to survive MP3 compression and
  editing). It stays in place; it helps the disclosure below.
- Disclosure: videos that use the English clone say "Narration: AI-generated clone of the presenter's own voice
  (Chatterbox, MIT)". The card's only use term is "Don't use this model to do bad things": clone only your own voice.
- Status: the README marks Chatterbox as retired research (no updates or support). The pinned versions keep working;
  nothing is fetched at run time once the weights are cached.
- Reference: a clean clip of about 10 s of your own **English** speech. Turbo refuses clips of 5 s or less (the
  adapter asks for at least 5.5 s); the model reads the first 10 s. A Vietnamese clip also works, but the card warns
  that the clip's accent carries over, so give `MOTION_STUDIO_REF_WAV_EN` an English clip when you have one.
- Voices: `clone` is Chatterbox-Turbo (350M, real-time factor 0.33 on an RTX 4070 laptop, 3.4 GB of VRAM at most);
  `clone-standard` is the original Chatterbox (500M, real-time factor 0.73, 3.8 GB). Both were understood word for
  word by faster-whisper base on the test lines with a synthetic reference. Which one sounds more like you is decided
  by ear on the comparison page.

### English cloning options checked

| Model | Verdict | Why |
|---|---|---|
| Chatterbox, Chatterbox-Turbo | **usable** (chosen) | MIT code and weights; inherited parts Apache-2.0 or MIT; local; built-in watermark |
| [OpenVoice V2](https://huggingface.co/myshell-ai/OpenVoiceV2) + [MeloTTS-English](https://huggingface.co/myshell-ai/MeloTTS-English) | usable (fallback) | MIT code and weights; changes the timbre of another TTS's output, so the clone is less close than Chatterbox's zero-shot model |
| [XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | not usable | Coqui Public Model License (non-commercial) |
| [F5-TTS](https://huggingface.co/SWivid/F5-TTS) | not usable | weights CC BY-NC 4.0 |

## Other Vietnamese options checked

| Model | Verdict | Why |
|---|---|---|
| [Piper vi_VN-vais1000-medium](https://huggingface.co/rhasspy/piper-voices/blob/main/vi/vi_VN/vais1000/medium/MODEL_CARD) | usable (fallback) | MIT voice repo; dataset VAIS-1000 CC BY 4.0; credit VAIS-1000 and Hi-Fi TTS. No cloning; not yet compared by ear. |
| [OpenVoice V2](https://huggingface.co/myshell-ai/OpenVoiceV2) | usable (cloning layer) | MIT code and weights; changes timbre on top of another TTS. Not needed while VieNeu clones. |
| [F5-TTS](https://huggingface.co/SWivid/F5-TTS) and Vietnamese fine-tunes ([hynt](https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice), [zalopay](https://huggingface.co/zalopay/vietnamese-tts)) | not usable | base weights CC BY-NC (Emilia data); fine-tunes inherit it or are NC-SA themselves |
| [viXTTS](https://huggingface.co/capleaf/viXTTS) | not usable | Coqui Public Model License (non-commercial) and NC-SA data |
| [facebook/mms-tts-vie](https://huggingface.co/facebook/mms-tts-vie) | not usable | CC BY-NC 4.0 |
| Piper [vi_VN-25hours_single](https://huggingface.co/rhasspy/piper-voices/blob/main/vi/vi_VN/25hours_single/low/MODEL_CARD), [vi_VN-vivos](https://huggingface.co/rhasspy/piper-voices/blob/main/vi/vi_VN/vivos/x_low/MODEL_CARD) | not usable | dataset licence unknown or CC BY-NC-SA |
| [VietTTS](https://github.com/dangvansam/viet-tts) | not usable | weights CC BY-NC |
| [ZipVoice](https://huggingface.co/k2-fsa/ZipVoice), [ZipVoice-Vietnamese-2500h](https://huggingface.co/wt5719001/ZipVoice-Vietnamese-2500h), [ViZipVoice](https://huggingface.co/anphunl/ViZipvoice) | not usable | base weights CC BY-NC; Vietnamese data NC-SA or undisclosed |
| [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) | not usable | weights CC BY-NC |
| [Chatterbox Multilingual](https://github.com/resemble-ai/chatterbox) | not usable for VI | MIT, but no Vietnamese |
| Zalo AI TTS | not usable | cloud API only (no local model; paid services need a yes first) |

## Setup (local only)

Needs `uv`, an NVIDIA GPU with a recent driver (tested: RTX 4070 laptop, 8 GB) and about 9 GB of disk. No `apt`
packages: espeak-ng comes inside the `espeakng-loader` wheel, and VieNeu uses the pure-Python `sea-g2p`.

```sh
just tts-setup      # venv in ~/.cache/nghiep-motion-studio/tts-venv: torch 2.11 (CUDA 12.8), kokoro, vieneu, faster-whisper
```

Model weights download to the Hugging Face cache (`~/.cache/huggingface`) on first use (measured 2026-11-25):
Kokoro 0.33 GB, VieNeu with its codec 0.39 GB, the faster-whisper check model 1.6 GB. Nothing is stored in the repo.
`MOTION_STUDIO_TTS_VENV` moves the venv.

The English clone has its own venv, because Chatterbox pins torch 2.6, transformers 5.2 and numpy 1.x:

```sh
just clone-en-setup  # venv in ~/.cache/nghiep-motion-studio/clone-en-venv: torch 2.11 (CUDA 12.8), chatterbox-tts 0.1.7
```

The recipe installs `chatterbox-tts` without its own pins (torch 2.6 and the unused gradio UI) and then the
libraries its code imports; this set was tested together on 2026-11-25 (torch 2.11.0+cu128, transformers 5.2.0,
diffusers 0.29.0, huggingface_hub 1.33.0, numpy 1.26.4, librosa 0.11.0, resemble-perth 1.0.1, s3tokenizer 0.3.0). The
venv takes about 7 GB. Weights download on first use into `~/.cache/nghiep-motion-studio/hf` (not the shared
Hugging Face cache): Turbo about 3.0 GB, the original model about 3.2 GB, 6.2 GB (5.8 GiB) for both. `MOTION_STUDIO_CLONE_EN_VENV`
and `MOTION_STUDIO_MODELS_DIR` move them. The other commands keep running in the TTS venv: when they need
`clone-en`, they start a small worker in the clone venv and talk to it over a pipe, so Kokoro, VieNeu and the English
clone can read scenes of one video in one run.

Tested versions: torch 2.11.0+cu128, kokoro 0.9.4, misaki 0.9.4, vieneu 3.8.4, transformers 4.57.6,
faster-whisper 1.2.1. `vieneu[cuda]` would pin torch 2.8; the plain `vieneu` package runs on the torch above.

## Listening page

```sh
MOTION_STUDIO_REF_WAV=/path/to/clean-6-8s-clip.wav just voices
```

This writes `out/voices/index.html` (gitignored): three VieNeu presets, the cloned voice (only when
`MOTION_STUDIO_REF_WAV` is set), three Kokoro voices and the two English clones (only when
`MOTION_STUDIO_REF_WAV_EN` or `MOTION_STUDIO_REF_WAV` is set) read the same test lines. Each model is closed after
its last voice, so one model is in memory at a time. The page shows the real-time
factor and, with `--asr` (on in `just voices`), what a local speech recogniser heard and its word error rate. The
reference clip is read in place and never copied into `out/` or the repo.

## Narration

```sh
just narrate path/to/scenes.json vi vieneu "Trúc Ly"
just narrate path/to/scenes.json en kokoro af_heart
MOTION_STUDIO_REF_WAV=/path/to/clip.wav just narrate path/to/scenes.json vi clone clone
MOTION_STUDIO_REF_WAV_EN=/path/to/english-clip.wav just narrate path/to/scenes.json en clone-en clone
```

### Several voices in one video

scenes.json can name voices and choose them per scene (example: `samples/voice-mix/scenes.json`):

```json
"speakers": {
  "owner": {"en": {"engine": "clone-en", "voice": "clone"}, "vi": {"engine": "vieneu", "voice": "clone"}},
  "guest": {"en": {"engine": "kokoro", "voice": "af_heart"}, "vi": {"engine": "vieneu", "voice": "Trúc Ly"}}
},
"voice_mix": {"policy": "narrator", "narrator": "owner"}
```

- `voice_mix` decides who reads the scenes that name no speaker. `narrator`: one speaker reads them (the owner's
  clone as narrator); a scene with `"speaker": "guest"` is read by the second voice. `alternate-chapters` with
  `"speakers": ["owner", "guest"]`: the speakers take turns, and every scene with a `chapter` starts the next turn.
- A scene's own `"voice": {"en": {"engine": ..., "voice": ...}}` wins over its speaker.
- A language that no rule covers falls back to `--engine` and `--voice`; without them `narrate` names every scene
  left without a voice.
- `just narrate-mix path/to/scenes.json en` narrates with the voices of the file. Set `MOTION_STUDIO_REF_WAV`
  (Vietnamese clone) and `MOTION_STUDIO_REF_WAV_EN` (English clone) first. The reference paths never go into
  scenes.json.
- For a Vietnamese clone next to Vietnamese presets, use the engine `vieneu` with the voice `clone`: one model serves
  both. Lines from engines with different sample rates are upsampled to the highest rate of the language.
- The timing file records the engine, voice and speaker of every scene; its top-level `engine` or `voice` is `mix`
  when the scenes differ. Changing a scene's voice makes only that scene's lines again.

`narrate` splits each scene's narration into sentences (abbreviations such as "TP.", "e.g." and list numbers do
not end a sentence), synthesises each one, joins them with 0.3 s pauses into `audio/tts/<scene>.<lang>.wav`, sets
the scene's `audio` entry and writes `audio/tts/timing.<lang>.json` (start and end of every line inside its scene).
Hand-made audio elsewhere under `audio/` is never touched.

- Cache: a line file's name holds a hash of its text, engine, voice, language and engine settings (seed,
  temperature, speed, and a hash of the clone reference clip's bytes). Unchanged lines are reused; `--force` makes
  every line again. A failed or interrupted run replaces nothing: scene files and the timing file change only after
  every line succeeded.
- Saving: scenes.json is read again before saving and only the new `audio` entries are written, so edits made
  during a long run are kept. If a scene's narration changed meanwhile, nothing is saved and you run narrate again.
- Render: each scene is sized to its audio. When the timing file still matches the scenes and their audio,
  `render` writes `<name>.<lang>.lines.json` with every line on the video clock; otherwise it warns, removes any old
  lines file and still renders the video.
