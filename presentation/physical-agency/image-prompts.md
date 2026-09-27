# FieldNotes — Physical Agency: image prompts

**Tool:** OpenAI Images API via `source/gen_images.py` (Python, `urllib`).
**Model actually used:** `gpt-image-2.5-flare` (recorded per asset in `source/assets/image_log.json`).
**Output:** 1536 × 1024 PNG, three assets. All three are **conceptual art** and are labeled on-slide as *not recorded evidence*. No generated image is used in place of an original evidence frame.

Assets that are **not** images: the slide 03 conceptual model, slide 05 metrics, slide 06 comparison, slide 07 architecture, and slide 08 roadmap cards are native editable PowerPoint shapes, not pictures.

## Shared style prefix (prepended to every prompt)

> Create a 16:9 visual concept for a premium FieldNotes hackathon presentation about physical agency. Near-black and charcoal background, warm white editorial typography, restrained lime accents, subtle frosted glass, generous negative space. One clear composition, readable at presentation distance. No invented metrics, capabilities, logos, or evidence. Distinguish future concepts from implemented features. No text, letters, or numbers in the image.

## `cover.png` — slide 01

> Cover composition. Large clear empty space on the left for the project name. On the right, a small consumer drone overlooking a work area with a workbench, with a restrained visual suggestion of observations across time (a faint series of translucent frames). Human-operated prototype, not autonomous manipulation. No people.

Placement: right column, cropped to 2.95 × 2.75 in, captioned "CONCEPT ART · NOT RECORDED EVIDENCE".

## `problem.png` — slide 02

> A workstation viewed through a moving camera, with one work surface partly hidden by a person seen from behind. Leave room on the left for a large job question. Convey incomplete visibility without hazard symbols, invented missing-object claims, or fake statistics.

Placement: right column, captioned "ILLUSTRATIVE WORK SCENARIO · CONCEPT ART".

## `roadmap.png` — slide 08 (generated, not placed)

> A calm three-horizon roadmap motif: an observation lens motif continuing across time from left to right, a faint feedback loop between reasoning and a small bounded observation tool. No delivery-date promises, no autonomous manipulation imagery, no completed-feature styling. Abstract, minimal.

Status: generated and kept in `source/assets/` for optional use; the final slide 08 uses editable cards instead so that the roadmap text stays editable and the slide stays under the density limit.

## Prompts from the brief that were intentionally not rendered as images

- Slide 03 conceptual model, slide 04 evidence workflow, slide 06 comparison, slide 07 architecture — built as native shapes so the content remains editable and no generated image could be mistaken for evidence or an implemented UI.

## Reproduce

```
set OPENAI_API_KEY=...        # never written to disk
python source/gen_images.py gpt-image-2.5-flare
```
