"""RAWLUX: CR3 to JPEG web converter with camera-rendered preview priority."""
from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import rawpy
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageCms, ImageOps
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

BASE = Path(__file__).resolve().parent
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '250'))
app = FastAPI(title='RAWLUX — Canon CR3 to JPG', docs_url=None, redoc_url=None)


def exif_bytes(src: Path, tag: str) -> bytes | None:
    """Return a camera-rendered embedded JPEG, or None if it is not present."""
    try:
        result = subprocess.run(['exiftool', '-b', f'-{tag}', str(src)],
                                capture_output=True, timeout=35, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.startswith(b'\xff\xd8\xff'):
        return None
    return result.stdout


def embedded_previews(src: Path):
    previews = []
    for tag in ('JpgFromRaw', 'PreviewImage', 'OtherImage'):
        content = exif_bytes(src, tag)
        if not content:
            continue
        try:
            with Image.open(io.BytesIO(content)) as photo:
                photo.verify()
            with Image.open(io.BytesIO(content)) as photo:
                w, h = photo.size
            if min(w, h) >= 300:
                previews.append((w * h, content, (w, h), tag))
        except (OSError, ValueError):
            continue
    return sorted(previews, key=lambda p: p[0], reverse=True)


def match_camera_color(rgb: Image.Image, preview_bytes: bytes) -> Image.Image:
    """Approximate Canon-rendered brightness/color distributions, not Canon DPP.

    Match per-channel quantiles on low-res representations to avoid massive RAM
    allocations for large RAW images. Works best when preview shows same framing.
    """
    with Image.open(io.BytesIO(preview_bytes)) as reference:
        reference = ImageOps.exif_transpose(reference).convert('RGB')
        reference.thumbnail((600, 600))
        ref = np.asarray(reference, dtype=np.uint8).reshape(-1, 3)
    sample = rgb.copy()
    sample.thumbnail((600, 600))
    src = np.asarray(sample, dtype=np.uint8).reshape(-1, 3)
    if src.size == 0 or ref.size == 0:
        return rgb
    # Clamp extreme percentiles so very small burned highlights do not skew map.
    percentiles = np.array([0, 1, 3, 7, 12, 20, 30, 40, 50, 60, 70, 80, 88, 93, 97, 99, 100])
    all_luts = []
    for channel in range(3):
        x = np.percentile(src[:, channel], percentiles)
        y = np.percentile(ref[:, channel], percentiles)
        # Preserve contrast and smooth noisy duplicated quantile positions.
        ux, indices = np.unique(x, return_index=True)
        uy = y[indices]
        if len(ux) < 2:
            lut = np.arange(256)
        else:
            lut = np.interp(np.arange(256), ux, uy, left=uy[0], right=uy[-1])
        all_luts.extend(np.clip(np.rint(lut), 0, 255).astype('uint8').tolist())
    return rgb.point(all_luts)


def convert(src: Path, dst: Path, mode: str, quality: int, ev: float) -> tuple[str, tuple[int,int]]:
    previews = embedded_previews(src) if mode == 'canon' else []
    # Verify RAW format and establish full-size output dimensions. If an embedded
    # JPEG has the full dimensions, its camera-made rendering is most faithful.
    with rawpy.imread(str(src)) as raw:
        raw_size = (int(raw.sizes.width), int(raw.sizes.height))
        if mode == 'canon':
            for _, content, (w, h), _tag in previews:
                if sorted((w, h)) == sorted(raw_size):
                    # Exact JPEG bytes supplied by camera; preserve profile/quality.
                    dst.write_bytes(content)
                    return ('canon-embedded', (w, h))
        rgb = raw.postprocess(
            use_camera_wb=True, use_auto_wb=False,
            no_auto_bright=False, auto_bright_thr=0.01,
            output_color=rawpy.ColorSpace.sRGB, output_bps=8,
            gamma=(2.222, 4.5), bright=1.0,
        )
    img = Image.fromarray(rgb, 'RGB')
    del rgb
    if mode == 'canon' and previews:
        img = match_camera_color(img, previews[0][1])
        label = 'canon-preview-matched'
    else:
        label = 'raw-developed'
    if abs(ev) > .001:
        # A bounded midtone curve; 0 and 255 remain fixed.
        exponent = 2 ** (-ev * .55)
        lut = np.clip(np.rint(255 * (np.arange(256) / 255.0) ** exponent), 0, 255).astype('uint8').tolist()
        img = img.point(lut * 3)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    img.save(dst, 'JPEG', quality=quality, subsampling=0, optimize=True, icc_profile=icc)
    return (label, img.size)


@app.get('/api/health')
def health():
    return {'ok': True, 'max_upload_mb': MAX_UPLOAD_MB}


@app.post('/api/convert')
async def convert_endpoint(file: UploadFile = File(...), mode: str = Form('canon'),
                           quality: int = Form(98), ev: float = Form(0)):
    if mode not in ('canon', 'raw'):
        raise HTTPException(400, 'Chế độ không hợp lệ.')
    if not 75 <= quality <= 100 or not math.isfinite(ev) or not -2 <= ev <= 3:
        raise HTTPException(400, 'Thông số chuyển đổi không hợp lệ.')
    name = Path(file.filename or 'photo.cr3').name
    if Path(name).suffix.lower() != '.cr3':
        raise HTTPException(400, 'Chỉ hỗ trợ tệp .CR3.')
    folder = Path(tempfile.mkdtemp(prefix='rawlux_'))
    src, dst = folder/'input.cr3', folder/'result.jpg'
    try:
        total = 0
        with src.open('wb') as out:
            while data := await file.read(1024 * 1024):
                total += len(data)
                if total > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f'Ảnh vượt quá giới hạn {MAX_UPLOAD_MB} MB của máy chủ.')
                out.write(data)
        await file.close()
        if total < 32:
            raise HTTPException(422, 'CR3 không hợp lệ.')
        render_type, dimensions = await run_in_threadpool(convert, src, dst, mode, quality, ev)
        return FileResponse(
            dst, media_type='image/jpeg', filename=f'{Path(name).stem}.jpg',
            headers={'X-Render-Mode': render_type, 'X-Image-Dimensions': f'{dimensions[0]}x{dimensions[1]}',
                     'Access-Control-Expose-Headers': 'Content-Disposition, X-Render-Mode, X-Image-Dimensions'},
            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True)
        )
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except (rawpy.LibRawError, OSError, ValueError, subprocess.SubprocessError) as err:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, f'Không thể xử lý CR3: {err}') from err
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


app.mount('/', StaticFiles(directory=BASE/'static', html=True), name='website')
