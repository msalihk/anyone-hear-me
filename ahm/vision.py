"""Photos attached to help requests: find what rescuers need to see with SAM 3.1 and draw it on the image.

SAM 3.1 (Meta Model API, Responses endpoint) takes a short phrase ("collapsed building", "person") and
returns a box and a pixel mask for every match; the official meta-sam-parser package decodes the masks.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import meta_sam_parser as sam
import requests
from PIL import Image, ImageDraw, ImageFont

from .config import META_BASE, meta_api_key

COLORS = {'collapsed building': (230, 57, 70), 'rubble pile': (244, 162, 97), 'person': (42, 157, 143)}
PHRASES = list(COLORS)


@dataclass
class Found:
    phrase: str
    box: tuple[int, int, int, int]  # left, top, right, bottom (exclusive)
    confidence: float | None
    mask: Image.Image | None  # 'L' mask sized to the box


def _data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(Path(path).read_bytes()).decode()


def segment(image_path: str, phrase: str, timeout: float = 120) -> list[Found]:
    body = {'model': 'sam-3.1',
            'input': [{'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': phrase},
                {'type': 'input_image', 'image_url': _data_url(image_path)}]}],
            'metadata': {'include_confidence': 'true'}}
    r = requests.post(f'{META_BASE}/responses', json=body, timeout=timeout,
                      headers={'Authorization': f'Bearer {meta_api_key()}', 'Content-Type': 'application/json'})
    if r.status_code != 200:
        raise RuntimeError(f'SAM 3.1 HTTP {r.status_code}: {r.text[:300]}')
    d = r.json()
    text = d.get('output_text') or ''.join(c.get('text', '') for o in d.get('output', [])
                                           for c in o.get('content', []) if isinstance(c, dict))
    parser = sam.image_segmentation_format().create_parser()
    parser.push(text)
    result = parser.finish(sam.CompletedOutcome()).result
    boxes = {rec.object_id: rec for rec in result.records if rec.kind == 'box'}
    out = []
    for rec in result.records:
        if rec.kind != 'mask':
            continue
        b = rec.bounds
        raw = sam.decode_mask_to_raster(rec.mask)
        m = Image.frombytes('L', (rec.mask.width, rec.mask.height), bytes(255 if v else 0 for v in raw))
        size = (max(1, b.right - b.left), max(1, b.bottom - b.top))
        if m.size != size:
            m = m.resize(size, Image.NEAREST)
        box = boxes.get(rec.object_id)
        conf = rec.confidence if rec.confidence is not None else (box.confidence if box else None)
        out.append(Found(phrase, (b.left, b.top, b.right, b.bottom), conf, m))
    return out


def overlay(image_path: str, found: list[Found], out_path: str, label: str = 'AI-generated image (synthetic)',
            min_conf: float = 0.5) -> str:
    """Draw masks and boxes; the corner label marks synthetic images as such."""
    img = Image.open(image_path).convert('RGBA')
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        font = ImageFont.truetype('DejaVuSans-Bold.ttf', max(14, img.width // 60))
    except OSError:
        font = ImageFont.load_default()
    for f in found:
        if f.confidence is not None and f.confidence < min_conf:
            continue
        color = COLORS.get(f.phrase, (255, 255, 255))
        if f.mask is not None:
            tint = Image.new('RGBA', f.mask.size, color + (90,))
            layer.paste(tint, f.box[:2], f.mask)
        draw.rectangle(f.box, outline=color + (255,), width=max(2, img.width // 400))
        if f.box[2] - f.box[0] >= img.width * 0.08:  # small objects are counted in the legend instead
            tag = f'{f.phrase} {f.confidence:.0%}' if f.confidence is not None else f.phrase
            draw.text((f.box[0] + 4, max(0, f.box[1] - font.size - 6)), tag, fill=color + (255,), font=font,
                      stroke_width=2, stroke_fill=(0, 0, 0, 255))
    counts = {}
    for f in found:
        if f.confidence is None or f.confidence >= min_conf:
            counts[f.phrase] = counts.get(f.phrase, 0) + 1
    y = 10
    for phrase, n in counts.items():
        draw.text((10, y), f'{phrase}: {n}', fill=COLORS.get(phrase, (255, 255, 255)) + (255,), font=font,
                  stroke_width=2, stroke_fill=(0, 0, 0, 255))
        y += font.size + 6
    if label:
        draw.text((10, img.height - font.size - 12), label, fill=(255, 255, 255, 230), font=font,
                  stroke_width=2, stroke_fill=(0, 0, 0, 255))
    Image.alpha_composite(img, layer).convert('RGB').save(out_path, quality=90)
    return out_path


def analyse(image_path: str, out_path: str, phrases: list[str] | None = None, synthetic: bool = True) -> dict:
    """Segment the standard phrases and write the overlay; returns counts and the output path."""
    found = []
    for ph in phrases or PHRASES:
        found += segment(image_path, ph)
    overlay(image_path, found, out_path, label='AI-generated image (synthetic)' if synthetic else '')
    return {'image': out_path, 'found': {ph: sum(1 for f in found if f.phrase == ph and (f.confidence or 1) >= 0.5)
                                         for ph in phrases or PHRASES}}
