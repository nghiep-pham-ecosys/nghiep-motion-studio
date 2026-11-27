"""GPU worker of ``motion-studio images-library``: runs in the images venv (torch, diffusers, Pillow).

Run it through ``motion-studio images-library`` (or ``just images-library``),
which sets ``PYTHONPATH`` and ``HF_HOME``. One model is on the GPU at a time
(8 GB is enough; RAM is about 10 GB, so the text encoders and the UNet load
apart, never the whole pipeline at once):

1. the base model of each style is found in the cache: the local copy in
   ``models/`` when it is there, else the repo at its pinned revision;
2. the two text encoders encode every prompt, then leave the GPU;
3. the UNet and the VAE draw the figures, the empty backgrounds and the
   drawn shots (txt2img), then the composites: background + cut-out figures
   with a soft shadow, blended by img2img at a low strength;
4. split two-shots are put together from two panels (no GPU).

Images already in the library with the same parameters are kept
(``--force`` redraws). Nothing here is imported by the tests or the renderer:
torch and diffusers exist only in the images venv.
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

from .images import HOME, MANIFEST, SHEET, Item, expand, item_dict, load_spec, model_for

VAE_TILE = 512  # the VAE decodes in bf16 tiles: fp16 overflows and the fp32 upcast failed on this GPU under WSL


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def free() -> None:
    import torch

    gc.collect()
    torch.cuda.empty_cache()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_dir(model: dict[str, Any]) -> tuple[Path, str | None]:
    """The diffusers folder of ``model`` and its weight variant: the local copy if present, else the pinned revision."""
    local = HOME / model["local"] if model.get("local") else None
    if local is not None and (local / "model_index.json").is_file():
        folder = local
    else:
        from huggingface_hub import snapshot_download

        folder = Path(snapshot_download(model["repo"], revision=model["revision"], allow_patterns=model["files"]))
    variant = "fp16" if (folder / "unet" / "diffusion_pytorch_model.fp16.safetensors").is_file() else None
    return folder, variant


# --- phases ----------------------------------------------------------------------------------------------------------


def encode(folder: Path, variant: str | None, items: list[Item]) -> dict[str, tuple[Any, ...]]:
    """Prompt embeddings (positive, negative, pooled, negative pooled) of every item, kept on the CPU."""
    import torch
    from diffusers import EulerAncestralDiscreteScheduler, StableDiffusionXLPipeline
    from transformers import CLIPTextModel, CLIPTextModelWithProjection, CLIPTokenizer

    te1 = CLIPTextModel.from_pretrained(folder, subfolder="text_encoder", variant=variant,
                                        dtype=torch.float16).to("cuda")
    te2 = CLIPTextModelWithProjection.from_pretrained(folder, subfolder="text_encoder_2", variant=variant,
                                                      dtype=torch.float16).to("cuda")
    tok1 = CLIPTokenizer.from_pretrained(folder, subfolder="tokenizer")
    tok2 = CLIPTokenizer.from_pretrained(folder, subfolder="tokenizer_2")
    pipe = StableDiffusionXLPipeline(vae=None, text_encoder=te1, text_encoder_2=te2, tokenizer=tok1, tokenizer_2=tok2,
                                     unet=None, force_zeros_for_empty_prompt=False,
                                     scheduler=EulerAncestralDiscreteScheduler.from_pretrained(folder,
                                                                                               subfolder="scheduler"))
    out = {}
    for it in items:
        for text in (it.prompt, it.negative):
            n = len(tok1(text).input_ids)
            if n > 77:
                log(f"WARN {it.id}: {n} tokens, CLIP keeps the first 77: ...{text[-60:]}")
        with torch.no_grad():
            embeds = pipe.encode_prompt(prompt=it.prompt, negative_prompt=it.negative, device="cuda",
                                        num_images_per_prompt=1, do_classifier_free_guidance=True)
        out[it.id] = tuple(t.to("cpu") for t in embeds)
    del pipe, te1, te2
    free()
    return out


class Painter:
    """The UNet and the VAE of one model, as a txt2img and an img2img pipeline sharing them."""

    def __init__(self, folder: Path, variant: str | None) -> None:
        import torch
        from diffusers import (AutoencoderKL, EulerAncestralDiscreteScheduler, StableDiffusionXLImg2ImgPipeline,
                               StableDiffusionXLPipeline, UNet2DConditionModel)

        unet = UNet2DConditionModel.from_pretrained(folder, subfolder="unet", variant=variant, torch_dtype=torch.float16,
                                                    low_cpu_mem_usage=True).to("cuda")
        vae = AutoencoderKL.from_pretrained(folder, subfolder="vae", variant=variant, torch_dtype=torch.float16)
        vae.to("cuda", dtype=torch.bfloat16)
        vae.enable_tiling()
        vae.tile_sample_min_size, vae.tile_latent_min_size = VAE_TILE, VAE_TILE // 8
        sched = EulerAncestralDiscreteScheduler.from_pretrained(folder, subfolder="scheduler")
        common = dict(vae=vae, text_encoder=None, text_encoder_2=None, tokenizer=None, tokenizer_2=None, unet=unet,
                      scheduler=sched, force_zeros_for_empty_prompt=False)
        self.t2i = StableDiffusionXLPipeline(**common)
        self.i2i = StableDiffusionXLImg2ImgPipeline(**common, requires_aesthetics_score=False)
        for pipe in (self.t2i, self.i2i):
            pipe.set_progress_bar_config(disable=True)
        self.vae = vae
        log(f"UNet and VAE loaded ({torch.cuda.memory_allocated() / 2**30:.2f} GiB on the GPU)")

    @staticmethod
    def _embeds(e) -> dict[str, Any]:
        pe, npe, ppe, nppe = (t.to("cuda") for t in e)
        return dict(prompt_embeds=pe, negative_prompt_embeds=npe, pooled_prompt_embeds=ppe,
                    negative_pooled_prompt_embeds=nppe)

    def decode(self, latents):
        import torch
        from PIL import Image

        with torch.no_grad():
            img = self.vae.decode(latents.to(torch.bfloat16) / self.vae.config.scaling_factor).sample
        arr = ((img.float().clamp(-1, 1) + 1) / 2)[0].permute(1, 2, 0).cpu().numpy()
        return Image.fromarray((arr * 255).round().astype("uint8"))

    def encode_image(self, image):
        import numpy as np
        import torch

        x = torch.from_numpy(np.asarray(image.convert("RGB")).astype(np.float32) / 127.5 - 1).permute(2, 0, 1)[None]
        with torch.no_grad():
            lat = self.vae.encode(x.to("cuda", torch.bfloat16)).latent_dist.sample()
        return (lat * self.vae.config.scaling_factor).to(torch.float16)

    def draw(self, it: Item, embeds, gen: dict[str, Any], seed: int):
        import torch

        g = torch.Generator("cuda").manual_seed(seed)
        with torch.no_grad():
            lat = self.t2i(**self._embeds(embeds), width=it.width, height=it.height,
                           num_inference_steps=int(gen["steps"]), guidance_scale=float(gen["guidance"]), generator=g,
                           output_type="latent").images
        return self.decode(lat)

    def blend(self, image, embeds, blend: dict[str, Any], seed: int):
        import torch

        g = torch.Generator("cuda").manual_seed(seed)
        with torch.no_grad():
            lat = self.i2i(**self._embeds(embeds), image=self.encode_image(image), strength=float(blend["strength"]),
                           num_inference_steps=int(blend["steps"]), guidance_scale=float(blend["guidance"]),
                           generator=g, output_type="latent").images
        return self.decode(lat)


# --- composites ------------------------------------------------------------------------------------------------------


def cutout(image):
    """(figure, mask) cropped to the figure: the plain backdrop is flood-filled from the border (it may shade from
    row to row), and the bright floor under the feet is dropped."""
    import numpy as np
    import torch
    import torch.nn.functional as F
    from PIL import Image, ImageFilter

    im = image.convert("RGB")
    a = np.asarray(im).astype(np.float32)
    h, w, _ = a.shape
    bgc = np.median(np.concatenate([a[:, :40], a[:, -40:]], axis=1), axis=1)  # backdrop colour per row
    bglike = np.linalg.norm(a - bgc[:, None, :], axis=2) < 22
    reg = np.zeros((h, w), bool)
    reg[0, :] = reg[-1, :] = reg[:, 0] = reg[:, -1] = True
    reg &= bglike
    bl = torch.from_numpy(bglike)[None, None].float()
    r = torch.from_numpy(reg)[None, None].float()
    for _ in range(2000):
        n = F.max_pool2d(r, 3, 1, 1) * bl
        if torch.equal(n, r):
            break
        r = n
    fg = 1 - r[0, 0].numpy()
    ys = np.where(fg.any(axis=1))[0]
    top, bot = ys[0], ys[-1]
    band = int(bot - (bot - top) * 0.08)
    lum = a.mean(axis=2)
    fg[band:][lum[band:] > 90] = 0
    mask = Image.fromarray((fg * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).filter(
        ImageFilter.GaussianBlur(1.2))
    bbox = mask.point(lambda v: 255 if v > 128 else 0).getbbox()
    return im.crop(bbox), mask.crop(bbox)


def head_x(mask) -> float:
    """Centre of the head as a share of the cut-out's width: the middle of the mask's top quarter."""
    import numpy as np

    m = np.asarray(mask) > 128
    rows = m[: max(1, m.shape[0] // 4)]
    xs = np.where(rows.any(axis=0))[0]
    return float((xs[0] + xs[-1]) / 2 / m.shape[1]) if len(xs) else 0.5


def composite(background, figures: list[tuple[Any, Any]], layout, floor: int, face: float):
    """The background with each (figure, mask) at its (centre x, height share), bottom on the floor, with a soft
    shadow; returns the image and the face point of each figure (shares of the frame)."""
    from PIL import Image, ImageDraw, ImageFilter

    W, H = background.size
    comp = background.convert("RGB").copy()
    faces = []
    for (fim, fm), (cx, hs) in zip(figures, layout):
        th = int(H * hs)
        tw = round(fim.width * th / fim.height)
        fim2, fm2 = fim.resize((tw, th), Image.LANCZOS), fm.resize((tw, th), Image.LANCZOS)
        x, y = int(W * cx - tw / 2), H - th - floor
        shadow = Image.new("L", comp.size, 0)
        ImageDraw.Draw(shadow).ellipse((x + tw * 0.15, y + th - 14, x + tw * 0.85, y + th + 14), fill=110)
        comp = Image.composite(Image.new("RGB", comp.size, (60, 50, 45)), comp, shadow.filter(ImageFilter.GaussianBlur(8)))
        comp.paste(fim2, (x, y), fm2)
        faces.append((round((x + head_x(fm2) * tw) / W, 4), round((y + face * th) / H, 4)))
    return comp, faces


def split(left, right, width: int, height: int, gutter: int = 12):
    """A split-screen two-shot: two head-and-shoulders panels side by side, each cropped to its half."""
    from PIL import Image

    half = (width - gutter) // 2
    out = Image.new("RGB", (width, height), (250, 247, 240))
    for k, panel in enumerate((left, right)):
        p = panel.convert("RGB")
        scale = max(half / p.width, height / p.height)
        p = p.resize((round(p.width * scale), round(p.height * scale)), Image.LANCZOS)
        x0 = (p.width - half) // 2
        y0 = min(p.height - height, max(0, (p.height - height) // 4))  # keep the head: crop more from the bottom
        out.paste(p.crop((x0, y0, x0 + half, y0 + height)), (k * (half + gutter), 0))
    return out


def save(image, path: Path, out_cfg: dict[str, Any], shot: bool, mirror: bool = False) -> None:
    from PIL import Image, ImageOps

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    if mirror:
        image = ImageOps.mirror(image)
    if shot:
        size = (int(out_cfg["width"]), int(out_cfg["height"]))
        image = image.convert("RGB")
        if image.size != size:
            image = image.resize(size, Image.LANCZOS)
        image.save(tmp, format="JPEG", quality=int(out_cfg.get("quality", 92)), optimize=True)
    else:
        image.save(tmp, format="PNG")
    tmp.replace(path)


# --- the contact sheet -----------------------------------------------------------------------------------------------


def contact_sheet(out: Path, manifest: dict[str, Any]) -> Path:
    """One JPEG under 2 MB: the figures and portraits, then a row per style and place with its shots, labelled."""
    from PIL import Image, ImageDraw, ImageFont

    def font(size: int):
        for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    pad, label, width = 12, 20, 2200
    sources = [e for e in manifest["sources"] if e["kind"] == "figure" or "portrait" in e["id"]]
    shots = manifest["shots"]
    rows: list[tuple[str, list[dict[str, Any]], tuple[int, int]]] = [("Figures and portraits", sources, (150, 150))]
    for style in manifest["styles"]:
        for place in dict.fromkeys(s["place"] for s in shots if s["style"] == style):
            rows.append((f"{style} · {place}", [s for s in shots if s["style"] == style and s["place"] == place],
                         (256, 144)))
    heights = []
    for _, entries, (tw, th) in rows:
        per = max(1, (width - pad) // (tw + pad))
        heights.append(30 + -(-len(entries) // per) * (th + label + pad))
    head = 70
    sheet = Image.new("RGB", (width, head + sum(heights) + 40), (248, 250, 252))
    draw = ImageDraw.Draw(sheet)
    models = ", ".join(f"{m['repo']} ({m['license'].split(' (')[0]})" for m in manifest["models"].values())
    draw.text((pad, 10), "Lesson library: Lan (her) and Minh (him), original characters, made with AI on a local GPU. "
              "D: chibi anime figures (main); A: anime (mixed in)", fill=(15, 23, 42), font=font(22))
    draw.text((pad, 40), f"Model: {models}. No LoRA. Composites: own figures on an empty background, img2img 0.30.",
              fill=(71, 85, 105), font=font(16))
    y = head
    for (title, entries, (tw, th)), h in zip(rows, heights):
        draw.text((pad, y + 4), title, fill=(15, 23, 42), font=font(18))
        x, yy = pad, y + 30
        for e in entries:
            if x + tw > width:
                x, yy = pad, yy + th + label + pad
            path = out / e["file"]
            if path.is_file():
                img = Image.open(path).convert("RGB")
                img.thumbnail((tw, th))
                sheet.paste(img, (x + (tw - img.width) // 2, yy + (th - img.height) // 2))
            name = e.get("shot") or e["id"].split("-", 2)[-1]
            draw.text((x, yy + th + 2), f"{name} ({e['seed']})", fill=(15, 23, 42), font=font(12))
            x += tw + pad
        y += h
    path = out / SHEET
    for q in (85, 78, 70, 62):
        sheet.save(path, format="JPEG", quality=q, optimize=True)
        if path.stat().st_size < 1_900_000:
            break
    return path


# --- main ------------------------------------------------------------------------------------------------------------


def write_manifest(path: Path, spec: dict[str, Any], items: list[Item], entries: dict[str, Any]) -> dict[str, Any]:
    models = {}
    for name in spec["styles"]:
        m = model_for(spec, name)
        models[m["key"]] = {k: m[k] for k in ("repo", "revision", "license", "url")}
    manifest = {
        "version": 2,
        "about": "Built by motion-studio images-library from samples/library/prompts.json. Images are original and "
                 "made with AI on a local GPU; they stay out of git.",
        "models": models,
        "styles": {k: {"name": v["name"], "share": v["share"], "model": v["model"]} for k, v in spec["styles"].items()},
        "generate": spec["generate"],
        "blend": spec["blend"],
        "output": spec["output"],
        "sources": [entries[i.id] for i in items if i.id in entries and not i.is_shot],
        "shots": [entries[i.id] for i in items if i.id in entries and i.is_shot],
    }
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return manifest


def entry(it: Item, path: Path, spec: dict[str, Any], by_id: dict[str, Item], faces=None) -> dict[str, Any]:
    d = item_dict(it)
    if faces:
        d["faces"] = {side: list(f) for side, f in faces.items()}
        d["focus"] = d["faces"][it.talk]
    if it.kind == "split":
        d["seed"] = [by_id[x].seed for x in it.deps]  # a split has no seed of its own: those of its panels
    model = model_for(spec, it.style)
    d.update(file=it.file(), sha256=sha256(path), model=f"{model['repo']}@{model['revision'][:12]}",
             license=model["license"])
    if it.kind == "scene":
        d["method"] = f"composite of {', '.join(it.deps)}; img2img strength {spec['blend']['strength']}"
    elif it.kind == "split":
        d["method"] = f"split screen of {' | '.join(it.deps)}"
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="images_worker")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", action="append", default=[], help="draw only these ids (repeatable); the shots built "
                    "from them follow when they are stale")
    ap.add_argument("--force", action="store_true", help="redraw images that already exist")
    ap.add_argument("--sheet-only", action="store_true", help="only (re)write the contact sheet")
    ap.add_argument("--candidates", type=int, default=0,
                    help="draw this many seeds of each --only image into candidates/ (seed, seed+1000, ...) to choose "
                         "one for the seeds of prompts.json; the library itself is left alone")
    args = ap.parse_args(argv)

    spec = load_spec(args.spec)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    items = expand(spec)
    by_id = {it.id: it for it in items}
    manifest_path = out / MANIFEST
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if old.get("version") != 2:
        old = {}
    entries = {e["id"]: e for e in old.get("sources", []) + old.get("shots", [])}
    if args.sheet_only:
        if not old:
            print(f"FAIL no version 2 {MANIFEST} in {out}", file=sys.stderr)
            return 1
        log(f"contact sheet: {contact_sheet(out, old)}")
        return 0

    def stale(it: Item) -> bool:
        e = entries.get(it.id)
        return args.force or e is None or e.get("params_hash") != it.params_hash or not (out / it.file()).is_file()

    unknown = [i for i in args.only if i not in by_id]
    if unknown:
        print(f"FAIL unknown id(s): {', '.join(unknown)}", file=sys.stderr)
        return 1
    todo_ids: set[str] = set()
    for it in items:  # sources come first, so a redrawn source pulls in the shots built from it
        wanted = not args.only or it.id in args.only or (it.id in entries and any(d in todo_ids for d in it.deps))
        if wanted and (stale(it) or (args.force and it.id in args.only)):
            todo_ids.add(it.id)
    if args.candidates:
        if not args.only:
            print("FAIL --candidates needs --only <id>", file=sys.stderr)
            return 1
        todo_ids = set(args.only)
    for it in reversed(items):  # a shot to draw needs its sources on disk
        if it.id in todo_ids:
            todo_ids.update(d for d in it.deps if not (out / by_id[d].file()).is_file())
    todo = [it for it in items if it.id in todo_ids]
    log(f"{len(todo)} image(s) to draw into {out}")
    gpu = [it for it in todo if it.kind != "split"]

    import torch

    if gpu and not torch.cuda.is_available():
        print("FAIL no CUDA GPU: the library is drawn on the local GPU only", file=sys.stderr)
        return 1
    t0 = time.time()
    for name in dict.fromkeys(it.model for it in gpu):  # one model at a time (both styles share one today)
        model = spec["models"][name]
        batch = [it for it in gpu if it.model == name]
        folder, variant = model_dir(model)
        log(f"model {model['repo']} at {model['revision'][:12]} from {folder} ({len(batch)} image(s))")
        embeds = encode(folder, variant, batch)
        log(f"prompts encoded ({time.time() - t0:.0f} s)")
        painter = Painter(folder, variant)
        cuts: dict[str, Any] = {}
        from PIL import Image

        for it in batch:
            t = time.time()
            seeds = [it.seed + 1000 * k for k in range(args.candidates)] if args.candidates else [it.seed]
            for seed in seeds:
                faces = None
                if it.kind == "scene":
                    bg = Image.open(out / by_id[it.deps[0]].file())
                    figs = []
                    for d in it.deps[1:]:
                        if d not in cuts:
                            cuts[d] = cutout(Image.open(out / by_id[d].file()))
                        figs.append(cuts[d])
                    lay = spec["styles"][it.style]["layout"]
                    comp, points = composite(bg, figs, it.layout, int(lay.get("floor", 22)),
                                             float(lay.get("face", 0.33)))
                    image = painter.blend(comp, embeds[it.id], spec["blend"], seed)
                    faces = dict(zip(it.who, points))
                else:
                    image = painter.draw(it, embeds[it.id], spec["generate"], seed)
                if args.candidates:
                    ext = "jpg" if it.is_shot else "png"
                    save(image, out / "candidates" / f"{it.id}-s{seed}.{ext}", spec["output"], shot=it.is_shot,
                         mirror=it.mirror)
                    continue
                path = out / it.file()
                save(image, path, spec["output"], shot=it.is_shot, mirror=it.mirror)
                entries[it.id] = entry(it, path, spec, by_id, faces)
                write_manifest(manifest_path, spec, items, entries)
            log(f"{it.id} seed {it.seed}{f' x{args.candidates}' if args.candidates else ''}: {time.time() - t:.1f} s")
        del painter
        free()
    if args.candidates:
        log(f"candidates in {out / 'candidates'}: put the chosen seed under seeds in the prompts file")
        return 0
    from PIL import Image

    for it in (x for x in todo if x.kind == "split"):
        left, right = (Image.open(out / by_id[d].file()) for d in it.deps)
        path = out / it.file()
        save(split(left, right, it.width, it.height), path, spec["output"], shot=True)
        entries[it.id] = entry(it, path, spec, by_id)
    for it in items:  # where the camera goes in a drawn shot is read off the image by eye (focus in the spec)
        if it.kind in ("draw", "split") and it.is_shot and it.id in entries:
            entries[it.id]["focus"] = list(it.focus)
            entries[it.id]["faces"] = {k: list(v) for k, v in it.faces.items()}
    manifest = write_manifest(manifest_path, spec, items, entries)
    log(f"library: {manifest_path} ({len(manifest['shots'])} shots, {len(manifest['sources'])} sources, "
        f"{time.time() - t0:.0f} s)")
    log(f"contact sheet: {contact_sheet(out, manifest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
