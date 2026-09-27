# Rebuild the deck

Requirements: Python 3.12, `python-pptx`, `pillow`, `pymupdf`; LibreOffice (for PDF/PNG export).

```
pip install python-pptx pillow pymupdf
```

Contents:

- `template.pptx` — the supplied hackathon template (9 slides; the build removes the instruction slide).
- `build_deck.py` — builds all eight slides from native editable shapes, embeds speaker notes, asserts word limits, and writes `FieldNotes-Physical-Agency.pptx`.
- `gen_images.py` — optional; regenerates the three conceptual assets with the OpenAI Images API (`gpt-image-2.5-flare`). Requires `OPENAI_API_KEY` in the environment. Not needed to rebuild the deck; the generated PNGs are already in `assets/`.
- `render.py` — renders the PDF to `slide_N.png` and `contact-sheet.png`.
- `assets/` — `cover.png`, `problem.png`, `roadmap.png`, `image_log.json`, and `evidence/` (drop `300.jpg` / `370.jpg` here to replace the on-slide placeholders; see `../assets/evidence/MANIFEST.md`).

Build (from this directory):

```
python build_deck.py template.pptx assets ../
soffice --headless --convert-to pdf ../FieldNotes-Physical-Agency.pptx --outdir ../
python render.py ../FieldNotes-Physical-Agency.pdf ../
```

On Windows, `soffice` is `"C:\Program Files\LibreOffice\program\soffice.exe"`.

Fonts: the deck specifies Aptos Display / Aptos / Consolas. LibreOffice substitutes when they are not installed; PowerPoint renders them natively.
