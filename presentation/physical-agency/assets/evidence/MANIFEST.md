# Evidence copy manifest

Session: `80777bc1e2ac4fa8b2578015d93a332d` (27 Sep 2026, recorded on the operator's Mac).
Original location: `data/evidence/80777bc1e2ac4fa8b2578015d93a332d/<frame>.jpg` (gitignored `data/`, not in this checkout).

Timestamps are local receipt time at decode (PDT), not exposure time.

| Copy in this directory | Original frame | Receipt time | Content | Used on | Status |
|---|---|---|---|---|---|
| `300.jpg` | `data/evidence/80777bc1e2ac4fa8b2578015d93a332d/300.jpg` | 02:50:45.991 PDT | Person seated beside an open laptop | Slide 04 (left) | **MISSING — PLACEHOLDER on slide.** Copy from recording Mac. |
| `370.jpg` | `data/evidence/80777bc1e2ac4fa8b2578015d93a332d/370.jpg` | 02:50:59.918 PDT | Person standing | Slide 04 (right) | **MISSING — PLACEHOLDER on slide.** Copy from recording Mac. |

Frames referenced in notes only (do not copy unless needed for Q&A): 358 (still seated, turned toward camera), 376 (person farther right, camera reframed), 442 (compression corruption), 454 (facing away).

## To complete the deck with real evidence

On the recording Mac:

```
cp data/evidence/80777bc1e2ac4fa8b2578015d93a332d/300.jpg presentation/physical-agency/source/assets/evidence/300.jpg
cp data/evidence/80777bc1e2ac4fa8b2578015d93a332d/370.jpg presentation/physical-agency/source/assets/evidence/370.jpg
cp presentation/physical-agency/source/assets/evidence/*.jpg presentation/physical-agency/assets/evidence/
```

Then rebuild (see `../../source/README.md`). `build_deck.py` replaces the two placeholders with the originals automatically when `assets/evidence/300.jpg` and `370.jpg` exist. Do not edit, crop, or enhance the originals; the builder letterboxes them without cropping.

Not packaged, by design: `data/memory.sqlite3`, `data/journal.sqlite3`, `.env` / API keys, other session directories, model caches.
