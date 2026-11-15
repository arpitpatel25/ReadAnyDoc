"""Draw the citation boxes of an extract result onto its page (needs pypdfium2 and Pillow)."""
import json, sys
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
HERE = Path(__file__).parent
src, js, out = (sys.argv[1:4] if len(sys.argv) > 3 else
                [HERE / "loan_estimate.pdf", HERE / "loan_estimate.json", HERE / "loan-estimate-citations.png"])
S = 2.5
page = pdfium.PdfDocument(src)[0].render(scale=S).to_pil().convert("RGBA")
W, H = page.size
d = json.load(open(js))
def _font():
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, 21)
        except OSError:
            pass
    return ImageFont.load_default()
font = _font()
GREEN, AMBER = (14, 77, 60), (176, 112, 20)
items = []
for path, cites in d["citations"].items():
    for c in cites:
        b = c.get("bbox")
        if b:
            items.append((b["width"] * b["height"], path.replace("[0]", ""), b, c.get("confidence")))
items.sort(key=lambda t: -t[0])  # big boxes first, small ones drawn on top
placed = []
def hit(r):
    return any(not (r[2] < p[0] or r[0] > p[2] or r[3] < p[1] or r[1] > p[3]) for p in placed)
img = page
for _, label, b, conf in items:
    x0, y0 = b["left"] * W, b["top"] * H
    x1, y1 = x0 + b["width"] * W, y0 + b["height"] * H
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); g = ImageDraw.Draw(layer)
    low = conf != "high"
    col = AMBER if low else GREEN
    if low:  # dashed outline, no fill
        for x in range(int(x0), int(x1), 18):
            g.line([x, y0 - 3, min(x + 10, x1), y0 - 3], fill=col + (255,), width=3); g.line([x, y1 + 3, min(x + 10, x1), y1 + 3], fill=col + (255,), width=3)
        for y in range(int(y0), int(y1), 18):
            g.line([x0 - 4, y, x0 - 4, min(y + 10, y1)], fill=col + (255,), width=3); g.line([x1 + 4, y, x1 + 4, min(y + 10, y1)], fill=col + (255,), width=3)
    else:
        g.rectangle([x0 - 4, y0 - 3, x1 + 4, y1 + 3], fill=(211, 234, 221, 90), outline=col + (255,), width=3)
    text = label + (" (low confidence)" if low else "")
    tw = g.textlength(text, font=font)
    for lx, ly in [(x0 - 4, y0 - 32), (x1 + 10, y0 - 2), (x0 - 4, y1 + 6), (x1 + 10, y1 - 26)]:
        r = (lx, ly, lx + tw + 16, ly + 28)
        if ly > 0 and r[2] < W and not hit(r):
            break
    placed.append(r)
    g.rounded_rectangle(r, radius=6, fill=col + (240,))
    g.text((r[0] + 8, r[1] + 3), text, font=font, fill=(255, 255, 255, 255))
    img = Image.alpha_composite(img, layer)
img.convert("RGB").save(out, optimize=True)
print(out, img.size)
