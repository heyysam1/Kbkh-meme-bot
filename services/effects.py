"""
Canvas visual effects and meme filters for KBKH Meme Engine.
Applies transformations such as deepfry, grayscale, and invert without disk I/O.
"""

from PIL import Image, ImageEnhance, ImageOps

def apply_filter(image: Image.Image, filter_name: str) -> Image.Image:
    """
    Apply requested filter transformation to PIL Image in-memory.
    Supported: 'none', 'deepfry', 'grayscale', 'invert'.
    """
    mode = filter_name.lower().strip()
    if mode in ("none", "", "normal"):
        return image

    rgb_image = image.convert("RGB")

    if mode == "deepfry":
        # Extreme saturation (+150% -> factor 2.5)
        color_enhancer = ImageEnhance.Color(rgb_image)
        saturated = color_enhancer.enhance(2.5)

        # Extreme contrast (+100% -> factor 2.0)
        contrast_enhancer = ImageEnhance.Contrast(saturated)
        contrasted = contrast_enhancer.enhance(2.0)

        # Sharpness enhancement (+100% -> factor 2.0)
        sharp_enhancer = ImageEnhance.Sharpness(contrasted)
        return sharp_enhancer.enhance(2.0)

    elif mode in ("grayscale", "bw", "b&w"):
        gray = ImageOps.grayscale(rgb_image)
        return gray.convert("RGB")

    elif mode == "invert":
        return ImageOps.invert(rgb_image)

    return image
