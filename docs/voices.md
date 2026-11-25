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

Tested versions: torch 2.11.0+cu128, kokoro 0.9.4, misaki 0.9.4, vieneu 3.8.4, transformers 4.57.6,
faster-whisper 1.2.1. `vieneu[cuda]` would pin torch 2.8; the plain `vieneu` package runs on the torch above.

## Listening page

```sh
MOTION_STUDIO_REF_WAV=/path/to/clean-6-8s-clip.wav just voices
```

This writes `out/voices/index.html` (gitignored): three VieNeu presets, the cloned voice (only when
`MOTION_STUDIO_REF_WAV` is set) and three Kokoro voices read the same test lines. The page shows the real-time
factor and, with `--asr` (on in `just voices`), what a local speech recogniser heard and its word error rate. The
reference clip is read in place and never copied into `out/` or the repo.

## Narration

```sh
just narrate path/to/scenes.json vi vieneu "Trúc Ly"
just narrate path/to/scenes.json en kokoro af_heart
MOTION_STUDIO_REF_WAV=/path/to/clip.wav just narrate path/to/scenes.json vi clone clone
```

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
