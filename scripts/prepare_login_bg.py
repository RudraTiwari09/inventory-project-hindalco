"""
Prepare clean login background image from reference.
Erases the baked-in reference card using PIL blur-fill interpolation,
resizes to max 1920px wide, and saves as JPEG quality 80.
"""

import os
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw


def prepare_background(
    ref_path: str = "assets/login_reference.png",
    out_path: str = "assets/login_bg.jpg",
    max_width: int = 1920,
    jpeg_quality: int = 80,
) -> bool:
    if not os.path.exists(ref_path):
        print(f"Reference file {ref_path} not found.")
        return False

    img = Image.open(ref_path).convert("RGB")
    W, H = img.size

    # Target card box: x in [41.6%, 58.4%], y in [39.3%, 62.2%], expanded by 3%
    x1 = max(0, int((0.416 - 0.03) * W))
    x2 = min(W, int((0.584 + 0.03) * W))
    y1 = max(0, int((0.393 - 0.03) * H))
    y2 = min(H, int((0.622 + 0.03) * H))

    # Crop the card region, apply heavy Gaussian blur to wipe high-frequency text/edges
    crop_box = (x1, y1, x2, y2)
    region = img.crop(crop_box)
    
    # Heavy blur removes baked-in inputs, text, and button
    blurred_region = region.filter(ImageFilter.GaussianBlur(radius=28))

    # Feather the edges so the patch blends seamlessly into the surrounding photo
    mask = Image.new("L", (x2 - x1, y2 - y1), 0)
    draw = ImageDraw.Draw(mask)
    feather_border = int(min(W, H) * 0.03)
    draw.rectangle(
        [feather_border, feather_border, (x2 - x1) - feather_border, (y2 - y1) - feather_border],
        fill=255,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_border))

    # Paste blurred fill over original
    img.paste(blurred_region, crop_box, mask)

    # Resize if wider than max_width
    if img.width > max_width:
        new_height = int((max_width / img.width) * img.height)
        img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

    # Ensure output dir exists
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=jpeg_quality, optimize=True)
    print(f"Clean background saved to {out_path} ({img.size[0]}x{img.size[1]})")
    return True


if __name__ == "__main__":
    prepare_background()

