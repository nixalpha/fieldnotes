import sys, fitz
from PIL import Image
pdf, out = sys.argv[1], sys.argv[2]
doc = fitz.open(pdf)
imgs = []
for i, page in enumerate(doc):
    pix = page.get_pixmap(dpi=110)
    p = f"{out}/slide_{i+1}.png"
    pix.save(p)
    imgs.append(Image.open(p))
w, h = imgs[0].size
sheet = Image.new("RGB", (w*2 + 30, (h+10)*4 + 10), (13, 20, 17))
for i, im in enumerate(imgs):
    sheet.paste(im, (10 + (i % 2)*(w+10), 10 + (i//2)*(h+10)))
sheet.save(f"{out}/contact-sheet.png")
print(len(imgs), "pages", w, h)
