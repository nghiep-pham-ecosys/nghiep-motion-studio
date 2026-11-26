"""GPU worker of ``motion-studio images-library``: runs in the images venv (torch, diffusers, Pillow).

Run it through ``motion-studio images-library`` (or ``just images-library``),
which sets ``PYTHONPATH`` and ``HF_HOME``. It keeps one model on the GPU at a
time (8 GB is enough): text encoders, then SDXL for the portraits, then the
IP-Adapter image encoder, then SDXL with the IP-Adapter for the shots. Each
phase frees the GPU before the next one loads. Images already in the
library with the same prompt, seed and size are kept (``--force`` redraws).

Nothing here is imported by the tests or the renderer: torch and diffusers
exist only in the images venv.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from .images import MANIFEST, SHEET, Shot, expand, load_spec, shot_dict

BASE = "stabilityai/stable-diffusion-xl-base-1.0"
IP_REPO = "h94/IP-Adapter"
SDXL_FILES = ["model_index.json", "scheduler/*", "tokenizer/*", "tokenizer_2/*", "text_encoder/config.json",
              "text_encoder/model.fp16.safetensors", "text_encoder_2/config.json",
              "text_encoder_2/model.fp16.safetensors", "unet/config.json",
              "unet/diffusion_pytorch_model.fp16.safetensors", "vae/config.json",
              "vae/diffusion_pytorch_model.fp16.safetensors"]
IP_FILES = ["models/image_encoder/config.json", "models/image_encoder/model.safetensors"]


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def snapshot(repo: str, files: list[str]) -> tuple[Path, str]:
    from huggingface_hub import snapshot_download

    path = Path(snapshot_download(repo, allow_patterns=files))
    return path, path.name  # the snapshot folder is named after the commit


def free() -> None:
    import torch

    gc.collect()
    torch.cuda.empty_cache()


def vram() -> str:
    import torch

    return f"{torch.cuda.memory_allocated() / 2**30:.2f} GiB on the GPU"


# --- phases --------------------------------------------------------------------------------------------------------


def encode_prompts(base: Path, shots: list[Shot]) -> dict[str, tuple[Any, Any, Any, Any]]:
    """Phase 1: prompt embeddings of every shot (positive and negative), kept on the CPU."""
    import torch
    from diffusers import StableDiffusionXLPipeline

    pipe = StableDiffusionXLPipeline.from_pretrained(base, unet=None, vae=None, variant="fp16",
                                                     torch_dtype=torch.float16).to("cuda")
    log(f"text encoders loaded ({vram()})")
    out = {}
    for shot in shots:
        for text in (shot.prompt, shot.negative):
            n = len(pipe.tokenizer(text).input_ids)
            if n > 77:
                log(f"WARN {shot.id}: {n} tokens, CLIP keeps the first 77: {text[:80]}...")
        with torch.no_grad():
            pe, npe, pooled, npooled = pipe.encode_prompt(prompt=shot.prompt, device="cuda", num_images_per_prompt=1,
                                                          do_classifier_free_guidance=True,
                                                          negative_prompt=shot.negative)
        out[shot.id] = tuple(t.to("cpu") for t in (pe, npe, pooled, npooled))
    del pipe
    free()
    return out


def load_sdxl(base: Path, ip_folder: Path | None, ip_weight: str = ""):
    import torch
    from diffusers import DPMSolverMultistepScheduler, StableDiffusionXLPipeline

    pipe = StableDiffusionXLPipeline.from_pretrained(base, text_encoder=None, text_encoder_2=None, tokenizer=None,
                                                     tokenizer_2=None, variant="fp16", torch_dtype=torch.float16)
    pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
    if ip_folder is not None:
        pipe.load_ip_adapter(str(ip_folder), subfolder="sdxl_models", weight_name=ip_weight,
                             image_encoder_folder=None)
    pipe.to("cuda")
    # SDXL's VAE overflows in fp16, and the fp32 decode (diffusers' default upcast) failed on this GPU under WSL:
    # decode in bf16 (fp32's range) in 512 px tiles instead.
    pipe.vae.to(torch.bfloat16)
    pipe.vae.enable_tiling()
    pipe.vae.tile_sample_min_size, pipe.vae.tile_latent_min_size = 512, 64
    log(f"SDXL loaded{' with the IP-Adapter' if ip_folder else ''} ({vram()})")
    return pipe


def draw(pipe, shot: Shot, embeds, gen: dict[str, Any], ip_embeds=None, masks=None):
    import torch

    pe, npe, pooled, npooled = (t.to("cuda") for t in embeds)
    kwargs: dict[str, Any] = {}
    if ip_embeds is not None:
        pipe.set_ip_adapter_scale(shot.ip_scale)
        kwargs["ip_adapter_image_embeds"] = [ip_embeds.to("cuda")]
        if masks is not None:
            kwargs["cross_attention_kwargs"] = {"ip_adapter_masks": masks}
    generator = torch.Generator("cuda").manual_seed(shot.seed)
    with torch.no_grad():
        latents = pipe(prompt_embeds=pe, negative_prompt_embeds=npe, pooled_prompt_embeds=pooled,
                       negative_pooled_prompt_embeds=npooled, width=shot.width, height=shot.height,
                       num_inference_steps=int(gen["steps"]), guidance_scale=float(gen["guidance"]),
                       generator=generator, output_type="latent", **kwargs).images
        torch.cuda.synchronize()
        latents = latents.to(torch.bfloat16) / pipe.vae.config.scaling_factor
        decoded = pipe.vae.decode(latents, return_dict=False)[0]
        torch.cuda.synchronize()
    return pipe.image_processor.postprocess(decoded.float(), output_type="pil")[0]


def face_crop(image, face: list[float]):
    """The square around the face of a portrait: ``face`` = [centre x, centre y, side] as shares of width, height,
    height. The face adapter wants the face, not the backdrop (which it would copy into every shot)."""
    w, h = image.size
    side = min(w, h, round(float(face[2]) * h))
    cx, cy = float(face[0]) * w, float(face[1]) * h
    left = min(max(0, round(cx - side / 2)), w - side)
    top = min(max(0, round(cy - side / 2)), h - side)
    return image.crop((left, top, left + side, top + side))


def encode_portraits(ip_folder: Path, portraits: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Phase 3: (negative, positive) hidden states of each face crop for the IP-Adapter plus (penultimate layer)."""
    import torch
    from transformers import CLIPImageProcessor, CLIPVisionModelWithProjection

    encoder = CLIPVisionModelWithProjection.from_pretrained(ip_folder / "models" / "image_encoder",
                                                            torch_dtype=torch.float16).to("cuda")
    processor = CLIPImageProcessor()
    out = {}
    for side, image in portraits.items():
        pixels = processor(images=image, return_tensors="pt").pixel_values.to("cuda", torch.float16)
        with torch.no_grad():
            pos = encoder(pixels, output_hidden_states=True).hidden_states[-2]
            neg = encoder(torch.zeros_like(pixels), output_hidden_states=True).hidden_states[-2]
        out[side] = (neg.to("cpu"), pos.to("cpu"))
    del encoder
    free()
    return out


def ip_inputs(shot: Shot, refs: dict[str, tuple[Any, Any]]):
    """The image embeds for ``shot`` (negative and positive stacked for guidance) and, for a two-shot, its masks."""
    import torch

    negs = torch.cat([refs[side][0] for side in shot.ip])[None]  # 1 x images x 257 x 1280
    poss = torch.cat([refs[side][1] for side in shot.ip])[None]
    embeds = torch.cat([negs, poss]).to(torch.float16)
    if len(shot.ip) == 1:
        return embeds, None
    from diffusers.image_processor import IPAdapterMaskProcessor
    from PIL import Image, ImageDraw

    w, h = shot.width, shot.height
    masks = []
    for k in range(len(shot.ip)):
        m = Image.new("L", (w, h), 0)
        ImageDraw.Draw(m).rectangle([k * w // len(shot.ip), 0, (k + 1) * w // len(shot.ip), h], fill=255)
        masks.append(m)
    processed = IPAdapterMaskProcessor().preprocess(masks, height=h, width=w)
    return embeds, [processed.reshape(1, processed.shape[0], processed.shape[2], processed.shape[3])]


def save(image, path: Path, out_cfg: dict[str, Any], portrait: bool) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    if portrait:
        image.save(tmp, format="PNG")
    else:
        image = image.convert("RGB").resize((int(out_cfg["width"]), int(out_cfg["height"])), Image.LANCZOS)
        image.save(tmp, format="JPEG", quality=int(out_cfg.get("quality", 92)), optimize=True)
    tmp.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- the contact sheet ---------------------------------------------------------------------------------------------


def contact_sheet(out: Path, manifest: dict[str, Any]) -> Path:
    """One JPEG (under 2 MB): the two portraits, then a row per place with its eight shots, each labelled."""
    from PIL import Image, ImageDraw, ImageFont

    tw, th, pad, label = 300, 169, 14, 22
    shots = [s for s in manifest["shots"] if s["place"]]
    places = list(dict.fromkeys(s["place"] for s in shots))
    cols = max(1, max(sum(1 for s in shots if s["place"] == p) for p in places)) if places else 1
    width = max(pad + cols * (tw + pad), 2 * (420 + 240 + 4 * pad))
    head = 340
    height = head + len(places) * (th + label + pad) + pad + 40
    sheet = Image.new("RGB", (width, height), (248, 250, 252))
    draw = ImageDraw.Draw(sheet)

    def font(size: int):
        for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    draw.text((pad, 10), "Illustrated lesson library: Lan (her) and Minh (him), original characters, made with AI "
              "(SDXL base 1.0 + IP-Adapter, local GPU)", fill=(15, 23, 42), font=font(20))
    x = pad
    for side, entry in manifest["characters"].items():
        path = out / entry["file"]
        if path.is_file():
            img = Image.open(path).convert("RGB")
            img.thumbnail((420, 240))
            sheet.paste(img, (x, 44))
            draw.text((x, 44 + img.height + 4), f"{entry['name']} ({side}): portrait, seed {entry['seed']}; "
                      "the face crop is the IP-Adapter's reference", fill=(71, 85, 105), font=font(16))
            x += img.width + pad
            face = out / f"face-{side}.png"
            if face.is_file():
                crop = Image.open(face).convert("RGB")
                crop.thumbnail((240, 240))
                sheet.paste(crop, (x, 44))
                x += crop.width
            x += 3 * pad
    y = head
    for place in places:
        x = pad
        for s in (s for s in shots if s["place"] == place):
            path = out / s["file"]
            if path.is_file():
                img = Image.open(path).convert("RGB").resize((tw, th), Image.LANCZOS)
                sheet.paste(img, (x, y))
            else:
                draw.rectangle([x, y, x + tw, y + th], outline=(148, 163, 184), width=2)
            draw.text((x, y + th + 2), f"{s['id']} (seed {s['seed']})", fill=(15, 23, 42), font=font(13))
            x += tw + pad
        y += th + label + pad
    path = out / SHEET
    sheet.save(path, format="JPEG", quality=80, optimize=True)
    return path


# --- main ----------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="images_worker")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", action="append", default=[], help="draw only these shot ids (repeatable)")
    ap.add_argument("--force", action="store_true", help="redraw images that already exist")
    ap.add_argument("--sheet-only", action="store_true", help="only (re)write the contact sheet")
    ap.add_argument("--candidates", type=int, default=0,
                    help="draw this many seeds of each --only shot into candidates/ (seed, seed+1000, ...) to choose "
                         "one for the seeds of prompts.json; the library itself is left alone")
    args = ap.parse_args(argv)

    spec = load_spec(args.spec)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    portraits, shots = expand(spec)
    manifest_path = out / MANIFEST
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    old_keys = {e["id"]: e.get("key") for e in old.get("shots", [])}
    old_keys.update({f"portrait-{k}": v.get("key") for k, v in old.get("characters", {}).items()})
    if args.sheet_only:
        if not old:
            print(f"FAIL no {MANIFEST} in {out}", file=sys.stderr)
            return 1
        log(f"contact sheet: {contact_sheet(out, old)}")
        return 0

    import torch

    if not torch.cuda.is_available():
        print("FAIL no CUDA GPU: the library is drawn on the local GPU only", file=sys.stderr)
        return 1
    fmt = spec["output"].get("format", "jpg")

    def stale(shot: Shot) -> bool:
        if args.only and shot.id not in args.only and not shot.id.startswith("portrait-"):
            return False
        return args.force or old_keys.get(shot.id) != shot.key() or not (out / shot.file(fmt)).is_file()

    todo_portraits = [p for p in portraits if args.force or old_keys.get(p.id) != p.key()
                      or not (out / p.file(fmt)).is_file()]
    redo_all = bool(todo_portraits)  # new portraits change every shot's look
    todo = [s for s in shots if (redo_all and (not args.only or s.id in args.only)) or stale(s)]
    if args.candidates:
        if todo_portraits or not args.only:
            print("FAIL --candidates needs --only <shot> and a finished pair of portraits", file=sys.stderr)
            return 1
        todo = [Shot(**{**s.__dict__, "seed": s.seed + 1000 * k}) for s in shots if s.id in args.only
                for k in range(args.candidates)]
    log(f"{len(todo_portraits)} portrait(s) and {len(todo)} shot(s) to draw into {out}")

    ip_file = spec["models"]["ip_adapter"]["file"]
    ip_weight = Path(ip_file).name
    base, base_rev = snapshot(BASE, SDXL_FILES)
    ip_folder, ip_rev = snapshot(IP_REPO, IP_FILES + [ip_file])
    models = {**spec["models"]}
    models["base"] = {**models["base"], "revision": base_rev}
    models["ip_adapter"] = {**models["ip_adapter"], "revision": ip_rev}
    models["image_encoder"] = {**models["image_encoder"], "revision": ip_rev}

    t0 = time.time()
    embeds = encode_prompts(base, todo_portraits + todo) if (todo_portraits or todo) else {}
    log(f"phase 1 done: {len(embeds)} prompt(s) encoded ({time.time() - t0:.0f} s)")

    gen = spec["generate"]
    if todo_portraits:
        pipe = load_sdxl(base, None)
        for p in todo_portraits:
            t = time.time()
            save(draw(pipe, p, embeds[p.id], gen), out / p.file(fmt), spec["output"], portrait=True)
            log(f"{p.id}: {time.time() - t:.1f} s")
        del pipe
        free()

    from PIL import Image

    characters = {}
    for p in portraits:
        side = p.who
        characters[side] = {"name": spec["characters"][side]["name"], "file": p.file(fmt), "seed": p.seed,
                            "prompt": p.prompt, "negative": p.negative, "key": p.key(),
                            "sha256": sha256(out / p.file(fmt)), "model": models["base"]["repo"],
                            "license": models["base"]["license"]}
    entries = {e["id"]: e for e in old.get("shots", [])}
    if todo:
        faces = {p.who: face_crop(Image.open(out / p.file(fmt)).convert("RGB"), spec["characters"][p.who]["face"])
                 for p in portraits}
        for side, img in faces.items():
            img.save(out / f"face-{side}.png")
        refs = encode_portraits(ip_folder, faces)
        log("phase 3 done: portraits encoded")
        pipe = load_sdxl(base, ip_folder, ip_weight)
        for shot in todo:
            t = time.time()
            ip_embeds, masks = ip_inputs(shot, refs)
            try:
                image = draw(pipe, shot, embeds[shot.id], gen, ip_embeds, masks)
                if args.candidates:
                    save(image, out / "candidates" / f"{shot.id}-s{shot.seed}.jpg", spec["output"], portrait=False)
                    log(f"candidate {shot.id} seed {shot.seed}: {time.time() - t:.1f} s")
                    continue
            except torch.OutOfMemoryError:
                free()
                log(f"WARN {shot.id}: out of GPU memory, skipped")
                continue
            path = out / shot.file(fmt)
            save(image, path, spec["output"], portrait=False)
            entries[shot.id] = {**shot_dict(shot), "file": shot.file(fmt), "key": shot.key(), "sha256": sha256(path),
                                "model": f"{models['base']['repo']} + {models['ip_adapter']['repo']} ({ip_weight})",
                                "license": f"{models['base']['license']}; IP-Adapter {models['ip_adapter']['license']}"}
            log(f"{shot.id}: {time.time() - t:.1f} s")
            write_manifest(manifest_path, spec, models, characters, entries, [s.id for s in shots])
        del pipe
        free()
    if args.candidates:
        log(f"candidates in {out / 'candidates'}: put the chosen seed under seeds in the prompts file")
        return 0
    manifest = write_manifest(manifest_path, spec, models, characters, entries, [s.id for s in shots])
    log(f"library: {manifest_path} ({sum(1 for s in manifest['shots'] if (out / s['file']).is_file())} shots, "
        f"{time.time() - t0:.0f} s)")
    log(f"contact sheet: {contact_sheet(out, manifest)}")
    return 0


def write_manifest(path: Path, spec: dict[str, Any], models: dict[str, Any], characters: dict[str, Any],
                   entries: dict[str, Any], order: list[str]) -> dict[str, Any]:
    manifest = {
        "version": 1,
        "about": "Built by motion-studio images-library from samples/library/prompts.json. Images are original and "
                 "made with AI on a local GPU; they stay out of git.",
        "models": models,
        "generate": spec["generate"],
        "output": spec["output"],
        "characters": characters,
        "shots": [entries[i] for i in order if i in entries],
    }
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return manifest


if __name__ == "__main__":
    sys.exit(main())
