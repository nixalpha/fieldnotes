"""Build FieldNotes-Physical-Agency.pptx from the Innovation Cup hackathon template.

Usage: python build_deck.py <template.pptx> <assets_dir> <out_dir>

The template's instruction slide is removed; the eight section slides are rebuilt as
editable native shapes (text, comparison rows, architecture diagram) in the dark
editorial design system from the production brief.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

TEMPLATE, ASSETS, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(OUT, exist_ok=True)

# ---- design system -----------------------------------------------------------------
BG = RGBColor(0x0D, 0x14, 0x11)
SURFACE = RGBColor(0x19, 0x23, 0x1D)
SURFACE2 = RGBColor(0x22, 0x2E, 0x27)
TEXT = RGBColor(0xF0, 0xF2, 0xE9)
MUTED = RGBColor(0x9B, 0xA9, 0x9F)
ACCENT = RGBColor(0xD6, 0xF2, 0x8C)
AMBER = RGBColor(0xC9, 0xA2, 0x4A)
RULE = RGBColor(0x2E, 0x3B, 0x33)
TITLE_FONT, BODY_FONT, MONO_FONT = "Aptos Display", "Aptos", "Consolas"

SESSION = "80777bc1e2ac4fa8b2578015d93a332d"
SESSION_SHORT = SESSION[:8]

# ---- helpers -----------------------------------------------------------------------
def rgb_hex(c: RGBColor) -> str:
    return str(c)


def set_alpha(shape, alpha_pct: int):
    """Set fill transparency (alpha_pct = opacity percent) on a solid-filled shape."""
    sp = shape.fill._xPr.find(qn("a:solidFill"))
    if sp is None:
        return
    clr = sp[0]
    a = etree.SubElement(clr, qn("a:alpha"))
    a.set("val", str(alpha_pct * 1000))


def rect(slide, x, y, w, h, fill=None, line=None, line_w=0.75, dash=None, shape=MSO_SHAPE.RECTANGLE, radius=None):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(line_w)
        if dash:
            s.line.dash_style = dash
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    s.shadow.inherit = False
    s.text_frame.text = ""
    return s


def text(slide, x, y, w, h, runs, size=14, color=TEXT, font=BODY_FONT, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, spacing=None, italic=False, margin=0.0, wrap=True):
    """runs: str | list of paragraphs; a paragraph is str | list[(text, {overrides})]."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        pieces = para if isinstance(para, list) else [(para, {})]
        for t, ov in pieces:
            r = p.add_run()
            r.text = t
            f = r.font
            f.name = ov.get("font", font)
            f.size = Pt(ov.get("size", size))
            f.bold = ov.get("bold", bold)
            f.italic = ov.get("italic", italic)
            f.color.rgb = ov.get("color", color)
    return tb


def line(slide, x1, y1, x2, y2, color=RULE, w=0.75, dash=None, arrow=False):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(w)
    if dash:
        c.line.dash_style = dash
    if arrow:
        ln = c.line._get_or_add_ln()
        tail = etree.SubElement(ln, qn("a:tailEnd"))
        tail.set("type", "triangle")
        tail.set("w", "sm")
        tail.set("len", "sm")
    return c


def picture(slide, path, x, y, w, h, crop_to_fill=True):
    from PIL import Image

    pic = slide.shapes.add_picture(path, Inches(x), Inches(y), Inches(w), Inches(h))
    if crop_to_fill:
        iw, ih = Image.open(path).size
        target = w / h
        src = iw / ih
        if src > target:  # too wide: crop sides
            k = (1 - target / src) / 2
            pic.crop_left = pic.crop_right = k
        elif src < target:
            k = (1 - src / target) / 2
            pic.crop_top = pic.crop_bottom = k
    return pic


def placeholder(slide, x, y, w, h, label, path_hint):
    box = rect(slide, x, y, w, h, fill=SURFACE, line=AMBER, line_w=1, dash=MSO_LINE.DASH)
    text(slide, x + 0.15, y + 0.15, w - 0.3, h - 0.3,
         [[("PLACEHOLDER — INSERT ORIGINAL", {"color": AMBER, "font": MONO_FONT, "size": 9, "bold": True})],
          [(label, {"color": TEXT, "size": 12, "bold": True})],
          [(path_hint, {"color": MUTED, "font": MONO_FONT, "size": 8})],
          [("Not in this checkout. Copy from the recording Mac; do not substitute generated imagery.", {"color": MUTED, "size": 8})]],
         anchor=MSO_ANCHOR.MIDDLE, spacing=3)
    return box


def asset(name):
    p = os.path.join(ASSETS, name)
    return p if os.path.exists(p) else None


def chrome(slide, n, section, notes):
    """Panel, top-left FieldNotes label, section number bottom-right, footer, speaker notes."""
    rect(slide, 0.47, 0.43, 9.05, 4.76, fill=BG, line=RULE, line_w=0.5)
    text(slide, 0.7, 0.55, 6.5, 0.22,
         [[("FIELDNOTES", {"color": ACCENT, "font": MONO_FONT, "size": 9, "bold": True}),
           (f"   /   {n:02d}  {section.upper()}", {"color": MUTED, "font": MONO_FONT, "size": 9})]])
    text(slide, 8.45, 0.57, 0.8, 0.25, f"{n:02d}", size=10, color=MUTED, font=MONO_FONT, align=PP_ALIGN.RIGHT)
    text(slide, 0.47, 5.3, 6, 0.2, "FIELDNOTES  /  HACKATHON PROTOTYPE  /  INNOVATION CUP 2026",
         size=7, color=MUTED, font=MONO_FONT)
    slide.notes_slide.notes_text_frame.text = notes


def headline(slide, title, sub=None, size=21):
    text(slide, 0.7, 0.86, 8.6, 0.72, title, size=size, color=TEXT, font=TITLE_FONT, bold=True, anchor=MSO_ANCHOR.BOTTOM)
    if sub:
        text(slide, 0.7, 1.6, 8.6, 0.25, sub, size=10, color=MUTED)
    line(slide, 0.7, 1.9, 9.25, 1.9, color=RULE, w=0.75)


def label(slide, x, y, w, t, color=ACCENT):
    return text(slide, x, y, w, 0.22, t, size=8, color=color, font=MONO_FONT, bold=True)


def clear_slide(slide, keep_names=()):
    """Remove every shape except the template background picture and the footer logo."""
    for sh in list(slide.shapes):
        keep = sh.shape_type == 13 and (sh.width == slide.part.package.presentation_part.presentation.slide_width
                                        or sh.top > Inches(5.2))
        if not keep:
            sh._element.getparent().remove(sh._element)


def delete_slide(prs, index):
    sldIdLst = prs.slides._sldIdLst
    sld = sldIdLst[index]
    prs.part.drop_rel(sld.rId)
    sldIdLst.remove(sld)


def words(*chunks):
    return sum(len(re.findall(r"[A-Za-z0-9’'\-\.]+", c)) for c in chunks)


# ---- build -------------------------------------------------------------------------
prs = Presentation(TEMPLATE)
assert (prs.slide_width, prs.slide_height) == (Inches(10), Inches(5.625))
delete_slide(prs, 0)  # instruction slide
slides = list(prs.slides)
assert len(slides) == 8
for s in slides:
    clear_slide(s)

copy_ledger = {}

# ---------------------------------------------------------------- 1 Title
s = slides[0]
chrome(s, 1, "Title",
       "Physical agency means an AI can help pursue a goal in the real world. FieldNotes builds one necessary part: "
       "a memory of physical observations that the agent can revisit.\n\n"
       "[12 s] Cover only: do not introduce the technology stack here. The drone is flown by a human operator; "
       "FieldNotes observes video and does not fly the aircraft.\n"
       "Team/member names and roles go in the submission message, not on this slide.")
text(s, 0.75, 1.4, 5.5, 1.0, "FieldNotes", size=46, color=TEXT, font=TITLE_FONT, bold=True)
text(s, 0.75, 2.4, 5.5, 0.5, "Physical memory for physical agency", size=21, color=ACCENT, font=TITLE_FONT)
text(s, 0.75, 2.95, 5.5, 0.4, "Timestamped observations that AI agents can revisit", size=13, color=MUTED)
chip = rect(s, 0.75, 3.65, 1.9, 0.3, fill=None, line=MUTED, line_w=0.75, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
text(s, 0.75, 3.65, 1.9, 0.3, "HACKATHON PROTOTYPE", size=8, color=MUTED, font=MONO_FONT, bold=True,
     align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
text(s, 0.75, 4.3, 5.5, 0.5,
     [[("Drone → timestamped evidence → MCP → agent.  ", {"color": MUTED, "size": 10}),
       ("Human controls flight.", {"color": AMBER, "size": 10})]])
# right: conceptual visual + temporal motif (conceptual, not recorded evidence)
if asset("cover.png"):
    picture(s, asset("cover.png"), 6.35, 0.95, 2.95, 2.75)
    rect(s, 6.35, 0.95, 2.95, 2.75, fill=None, line=RULE, line_w=0.5)
else:
    placeholder(s, 6.35, 0.95, 2.95, 2.75, "Conceptual drone / work-area visual", "assets/cover.png")
text(s, 6.35, 3.72, 2.95, 0.2, "CONCEPT ART · NOT RECORDED EVIDENCE", size=7, color=MUTED, font=MONO_FONT)
for i in range(4):
    x = 6.35 + i * 0.76
    rect(s, x, 3.95, 0.66, 0.4, fill=SURFACE, line=ACCENT if i == 3 else RULE, line_w=0.75)
    text(s, x, 4.36, 0.66, 0.2, f"t{i}", size=7, color=ACCENT if i == 3 else MUTED, font=MONO_FONT, align=PP_ALIGN.CENTER)
line(s, 6.35, 4.62, 9.3, 4.62, color=RULE, w=0.75, arrow=True)
text(s, 6.35, 4.66, 2.2, 0.2, "observations across time (motif)", size=7, color=MUTED, font=MONO_FONT)

# ---------------------------------------------------------------- 2 Problem
s = slides[1]
p2_copy = ("A physical decision depends on what is still true. For people overseeing physical work. "
           "Is the workstation ready for the next job? Earlier images may be stale. Missing objects may simply be out of view. "
           "Reviewing footage still takes human attention. Who: people overseeing physical work. What: the moment a job "
           "decision needs recent evidence. Why now: capable agents need more than a plausible answer. "
           "Why options fall short: someone reconstructs context by hand.")
chrome(s, 2, "Problem & urgency",
       "Consider a worker asking whether a station is ready for the next job. A useful agent needs more than a plausible "
       "answer. It needs recent evidence. Earlier images can be stale, and an object outside the camera view may still be "
       "present. Someone usually has to reconstruct that context.\n\n"
       "[22 s] The job question is an intended workflow, not a completed FieldNotes demonstration. Urgency is framed as a "
       "design need for capable agents, not as a market trend. No claim that all existing video products lack search.\n"
       "Grounding research (notes only): SayCan https://arxiv.org/abs/2204.01691 ; DynaMem https://arxiv.org/abs/2411.04999 ; "
       "Gemini Robotics https://deepmind.google/blog/gemini-robotics-brings-ai-into-the-physical-world/")
headline(s, "A physical decision depends on what is still true", "Problem + Inspiration: 200 words maximum across slides 02–03")
label(s, 0.7, 2.02, 4, "WHO  ·  FOR PEOPLE OVERSEEING PHYSICAL WORK")
text(s, 0.7, 2.25, 5.2, 0.8, "“Is the workstation ready for the next job?”", size=20, color=TEXT, font=TITLE_FONT, bold=True)
label(s, 0.7, 3.17, 1.6, "WHAT", MUTED)
text(s, 2.2, 3.13, 3.7, 0.3, "Earlier images may be stale.", size=11)
line(s, 0.7, 3.45, 5.9, 3.45)
label(s, 0.7, 3.55, 1.6, "WHY IT MATTERS NOW", MUTED)
text(s, 2.2, 3.51, 3.7, 0.3, "Missing objects may simply be out of view.", size=11)
line(s, 0.7, 3.83, 5.9, 3.83)
label(s, 0.7, 3.93, 1.6, "CURRENT OPTION", MUTED)
text(s, 2.2, 3.89, 3.7, 0.3, "Reviewing footage still takes human attention.", size=11)
line(s, 0.7, 4.21, 5.9, 4.21)
text(s, 0.7, 4.32, 5.2, 0.3, "A capable agent needs recent evidence, not only a plausible answer.", size=10, color=ACCENT)
text(s, 0.7, 4.6, 5.2, 0.25, "Research: SayCan · DynaMem · Gemini Robotics (see notes)", size=8, color=MUTED, font=MONO_FONT)
if asset("problem.png"):
    picture(s, asset("problem.png"), 6.1, 2.0, 3.2, 2.4)
    rect(s, 6.1, 2.0, 3.2, 2.4, fill=None, line=RULE, line_w=0.5)
else:
    placeholder(s, 6.1, 2.0, 3.2, 2.4, "Workstation with partially occluded surface", "assets/problem.png")
text(s, 6.1, 4.45, 3.2, 0.2, "ILLUSTRATIVE WORK SCENARIO · CONCEPT ART", size=7, color=MUTED, font=MONO_FONT)
text(s, 6.1, 4.62, 3.2, 0.3, "Part of the surface is hidden: an unseen object is unknown, not absent.", size=8, color=AMBER)

# ---------------------------------------------------------------- 3 Inspiration
s = slides[2]
p3_copy = ("Physical agency needs shared context. LLM reasons about the job. Robot tools execute bounded capabilities. "
           "Space Info preserves observations across time. Observed Last seen Changed Unknown. Future integration. "
           "FieldNotes builds the temporal evidence layer. Observed: reasoning alone answers without evidence. "
           "Revealed: the missing part is memory of what was seen, when. Unlocks: MCP lets an agent request evidence "
           "the way it requests any tool.")
chrome(s, 3, "Inspiration / key insight",
       "Research in robot planning separates high-level reasoning from executable skills. Dynamic-memory research shows why "
       "changing environments also matter. Our approach uses three responsibilities: the LLM reasons about the job, robot "
       "tools execute bounded capabilities, and Space Info carries observations through time. FieldNotes focuses on that "
       "third part.\n\n"
       "[20 s] Observed / Last seen / Changed / Unknown are information categories, not a claim that all are automatically "
       "resolved. Robot tools are a future integration; FieldNotes implements no robot control, 3D localization, or DynaMem.\n"
       "Related research (not dependencies): SayCan https://arxiv.org/abs/2204.01691 ; DynaMem https://arxiv.org/abs/2411.04999 ; "
       "MCP tools spec https://modelcontextprotocol.io/specification/2025-06-18/server/tools")
headline(s, "Physical agency needs shared context", "User goal → reasoning → permitted robot capability → observation → memory update → reassessment")
# left column: template labels
label(s, 0.7, 2.0, 2.4, "WHAT WE OBSERVED", MUTED)
text(s, 0.7, 2.2, 2.5, 0.6, "Reasoning alone answers without evidence.", size=11)
label(s, 0.7, 2.85, 2.4, "WHAT IT REVEALED", MUTED)
text(s, 0.7, 3.05, 2.5, 0.6, "The missing part is memory of what was seen, and when.", size=11)
label(s, 0.7, 3.7, 2.4, "WHY IT UNLOCKS A SOLUTION", MUTED)
text(s, 0.7, 3.9, 2.5, 0.7, "MCP lets an agent request evidence the way it requests any tool.", size=11)
# three-part conceptual diagram
bx, by, bw, bh, gap = 3.55, 2.05, 1.75, 1.55, 0.27
cols = [("LLM", "reasons about the job", RULE, TEXT, None),
        ("Robot tools", "execute bounded capabilities", RULE, MUTED, MSO_LINE.DASH),
        ("Space Info", "preserves observations across time", ACCENT, TEXT, None)]
for i, (t, d, ln, tc, dash) in enumerate(cols):
    x = bx + i * (bw + gap)
    rect(s, x, by, bw, bh, fill=SURFACE if i != 2 else SURFACE2, line=ln, line_w=1.25 if i == 2 else 0.75, dash=dash)
    text(s, x + 0.12, by + 0.12, bw - 0.24, 0.4, t, size=15, color=ACCENT if i == 2 else tc, font=TITLE_FONT, bold=True)
    text(s, x + 0.12, by + 0.52, bw - 0.24, 0.6, d, size=10, color=MUTED)
    if i == 1:
        text(s, x + 0.12, by + bh - 0.32, bw - 0.24, 0.25, "FUTURE INTEGRATION", size=7, color=AMBER, font=MONO_FONT, bold=True)
    if i == 2:
        text(s, x + 0.12, by + bh - 0.38, bw - 0.24, 0.3, "Observed · Last seen · Changed · Unknown", size=8, color=ACCENT, font=MONO_FONT)
    if i < 2:
        line(s, x + bw, by + bh / 2, x + bw + gap, by + bh / 2, color=MUTED if i == 0 else AMBER, w=1, arrow=True,
             dash=MSO_LINE.DASH if i == 1 else None)
# return arrow from Space Info back to LLM
line(s, bx + 2 * (bw + gap) + bw / 2, by + bh, bx + 2 * (bw + gap) + bw / 2, by + bh + 0.3, color=ACCENT, w=1)
line(s, bx + 2 * (bw + gap) + bw / 2, by + bh + 0.3, bx + bw / 2, by + bh + 0.3, color=ACCENT, w=1)
line(s, bx + bw / 2, by + bh + 0.3, bx + bw / 2, by + bh + 0.02, color=ACCENT, w=1, arrow=True)
text(s, bx, by + bh + 0.32, 5.8, 0.25, "evidence and temporal records return to the reasoner", size=8, color=MUTED, font=MONO_FONT, align=PP_ALIGN.CENTER)
text(s, 3.55, 4.45, 5.8, 0.4, "FieldNotes builds the temporal evidence layer", size=15, color=ACCENT, font=TITLE_FONT, bold=True)

# ---------------------------------------------------------------- 4 Solution overview
s = slides[3]
frames = {"300": "02:50:45.991", "358": "02:50:57.457", "370": "02:50:59.918", "376": "02:51:01.054",
          "442": "02:51:14.435", "454": "02:51:17.014"}
chrome(s, 4, "Solution overview",
       "Here is evidence from our recorded session. An agent can search earlier images and inspect the originals. In the "
       "first frame, the person is seated beside a laptop. In the later frame, they are standing. That supports a posture "
       "change. It does not establish that a job finished, and the camera also reframed.\n\n"
       f"[32 s] Evidence: session {SESSION}, frames 300.jpg (receipt 02:50:45.991 PDT, 27 Sep 2026) and 370.jpg "
       "(02:50:59.918 PDT). Timestamps are local receipt at decode, not exposure. The text is an evidence-based example, "
       "not a verbatim model transcript. The redesigned portal is not shown because it does not exist yet.\n"
       "MCP tools exercised in the inspected session: get_memory_status, list_sessions, get_observation_window, "
       "search_visual_history, get_evidence (src/fieldnotes/mcp_server.py).")
headline(s, "The agent can return to the evidence", "Evidence-based example from one recorded session · not a verbatim transcript")
label(s, 0.7, 2.0, 1.4, "WHO IT IS FOR", MUTED)
text(s, 2.0, 1.97, 4.3, 0.3, "AI agents and the people overseeing physical work", size=10)
text(s, 0.7, 2.27, 5.6, 0.45, "“What changed around the workstation?”", size=16, color=TEXT, font=TITLE_FONT, bold=True)
# before / after pair
for i, (fid, t_pdt, cap) in enumerate([("300", frames["300"], "Person seated beside an open laptop"),
                                        ("370", frames["370"], "Person standing")]):
    x = 0.7 + i * 2.85
    ev = asset(f"evidence/{fid}.jpg")
    if ev:
        picture(s, ev, x, 2.8, 2.7, 1.52, crop_to_fill=False)
    else:
        placeholder(s, x, 2.8, 2.7, 1.52, f"Original frame {fid}.jpg", f"data/evidence/{SESSION}/{fid}.jpg")
    text(s, x, 4.34, 2.7, 0.2, [[(f"{fid}.jpg", {"color": ACCENT, "font": MONO_FONT, "size": 8, "bold": True}),
                                 (f"   {t_pdt} PDT · 27 Sep 2026", {"color": MUTED, "font": MONO_FONT, "size": 8})]])
    text(s, x, 4.52, 2.7, 0.2, cap, size=9, color=TEXT)
text(s, 3.28, 3.35, 0.4, 0.4, "→", size=18, color=ACCENT, align=PP_ALIGN.CENTER)
text(s, 0.7, 4.75, 5.6, 0.25, "Camera reframed between frames: not every image difference is object movement.", size=8, color=AMBER)
# right column: steps + interpretation
rect(s, 6.45, 2.0, 2.85, 2.95, fill=SURFACE, line=RULE, line_w=0.5)
label(s, 6.6, 2.1, 2.6, "HOW IT WORKS  ·  THREE MCP CALLS")
steps = [("1", "Search earlier observations", "search_visual_history"),
         ("2", "Inspect original frames", "get_evidence"),
         ("3", "Explain the supported change", "get_memory_state / record_memory")]
for i, (n, t, tool) in enumerate(steps):
    y = 2.38 + i * 0.5
    text(s, 6.6, y, 0.3, 0.3, n, size=12, color=ACCENT, font=MONO_FONT, bold=True)
    text(s, 6.9, y - 0.02, 2.35, 0.3, t, size=11, color=TEXT)
    text(s, 6.9, y + 0.2, 2.35, 0.2, tool, size=7, color=MUTED, font=MONO_FONT)
line(s, 6.6, 3.9, 9.15, 3.9)
text(s, 6.6, 3.95, 2.6, 0.3, "Seated earlier. Standing later.", size=11, color=ACCENT, font=TITLE_FONT, bold=True)
text(s, 6.6, 4.22, 2.6, 0.25, "Task completion was not observed.", size=9, color=TEXT)
text(s, 6.6, 4.5, 2.6, 0.4, "WHAT CHANGES: the agent cites originals instead of guessing.", size=8, color=MUTED)

# ---------------------------------------------------------------- 5 Impact
s = slides[4]
chrome(s, 5, "Quantified impact",
       "One recorded session received 712 frames over about 141 seconds and retained 148 evidence images. Those are "
       "verified prototype counts, not a productivity claim. They demonstrate that observations remain available for "
       "later inspection. Whether this reduces review time is the next evaluation question.\n\n"
       f"[20 s] Session {SESSION}. Source: data/memory.sqlite3 and data/journal.sqlite3 read in SQLite read-only mode.\n"
       "First receipt 2026-09-27T09:49:47.368214+00:00; last receipt 2026-09-27T09:52:08.075604+00:00; "
       "140.707 s = last − first (CALCULATED). 712 received frames and 148 retained evidence images are VERIFIED counts "
       "from the frozen session database. 148 is evidence coverage, not accuracy, compression efficiency, or labor savings.\n"
       "NOTE: this checkout does not contain data/; the counts are a previously recorded snapshot from the recording Mac, "
       "not independently re-verified here.")
headline(s, "148 retained evidence images", "One recorded prototype session · numbers classified below")
text(s, 0.7, 1.95, 4.6, 1.6, "148", size=96, color=ACCENT, font=TITLE_FONT, bold=True)
text(s, 0.7, 3.5, 4.6, 0.3, [[("VERIFIED", {"color": ACCENT, "font": MONO_FONT, "size": 9, "bold": True}),
                              ("  ·  one recorded prototype session", {"color": MUTED, "size": 10})]])
text(s, 0.7, 3.85, 4.6, 0.6, "A record the agent can inspect after the moment has passed.", size=13, color=TEXT)
text(s, 0.7, 4.5, 4.6, 0.3, "Productivity improvement has not yet been measured.", size=10, color=AMBER)
# secondary numbers
for i, (num, lab, cls, clr) in enumerate([("712", "received frames", "VERIFIED", ACCENT),
                                          ("140.7 s", "of observed footage", "CALCULATED", AMBER)]):
    x = 5.6 + i * 1.9
    rect(s, x, 2.0, 1.75, 1.3, fill=SURFACE, line=RULE, line_w=0.5)
    text(s, x + 0.12, 2.08, 1.55, 0.6, num, size=26, color=TEXT, font=TITLE_FONT, bold=True)
    text(s, x + 0.12, 2.7, 1.55, 0.3, lab, size=10, color=MUTED)
    text(s, x + 0.12, 3.0, 1.55, 0.25, cls, size=8, color=clr, font=MONO_FONT, bold=True)
# filmstrip motif
for i in range(9):
    rect(s, 5.6 + i * 0.41, 3.45, 0.34, 0.22, fill=SURFACE2 if i % 5 else ACCENT, line=None)
text(s, 5.6, 3.7, 3.7, 0.2, "sampled frames retained as evidence (motif, not to scale)", size=7, color=MUTED, font=MONO_FONT)
rect(s, 5.6, 3.95, 3.7, 1.0, fill=SURFACE, line=RULE, line_w=0.5)
label(s, 5.72, 4.0, 3.5, "HOW THE NUMBERS WERE CALCULATED", MUTED)
text(s, 5.72, 4.22, 3.5, 0.7,
     [[(f"session {SESSION_SHORT}… · 27 Sep 2026 · memory.sqlite3 (read-only)", {"font": MONO_FONT, "size": 7, "color": TEXT})],
      [("Counts: rows of received frames and retained evidence.", {"size": 8, "color": MUTED})],
      [("Duration: last receipt − first receipt = 140.707 s.", {"size": 8, "color": MUTED})]], spacing=2)

# ---------------------------------------------------------------- 6 Differentiation
s = slides[5]
p6_copy = ("The agent retrieves context when it needs it. Manual review: a person reconstructs the sequence. "
           "Summary alone: an interpretation without the full visual context. FieldNotes: MCP access to earlier originals, "
           "timestamps, and temporal records. Built today: an integrated evidence workflow. Still to prove: reliability "
           "across varied physical jobs. A decision can require another observation.")
chrome(s, 6, "What makes this different?",
       "The distinction is that evidence remains available to the agent when it needs context. A summary is useful, but it "
       "is still an interpretation. FieldNotes exposes earlier originals and temporal records through MCP. Our contribution "
       "is the integrated workflow. Reliability across different jobs remains to be demonstrated.\n\n"
       "[22 s] Compare workflows, not named competitors. No established moat is claimed; the potential advantage is "
       "accumulated workflow evaluation and integration. Requesting a physical re-observation through a robot tool is "
       "future work.\nMCP tools spec: https://modelcontextprotocol.io/specification/2025-06-18/server/tools")
headline(s, "The agent retrieves context when it needs it", "Maximum 100 words · workflows compared, not vendors")
label(s, 0.7, 2.0, 3, "HOW IT IS DIFFERENT")
rows = [("Manual review", "a person reconstructs the sequence", False),
        ("Summary alone", "an interpretation without the full visual context", False),
        ("FieldNotes", "MCP access to earlier originals, timestamps, and temporal records", True)]
for i, (a, b, hi) in enumerate(rows):
    y = 2.3 + i * 0.62
    if hi:
        rect(s, 0.7, y - 0.06, 5.4, 0.58, fill=SURFACE2, line=None)
        rect(s, 0.7, y - 0.06, 0.05, 0.58, fill=ACCENT, line=None)
    text(s, 0.85, y, 1.6, 0.45, a, size=13, color=ACCENT if hi else TEXT, font=TITLE_FONT, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 2.5, y, 3.5, 0.45, b, size=11, color=TEXT if hi else MUTED, anchor=MSO_ANCHOR.MIDDLE)
    if not hi:
        line(s, 0.7, y + 0.52, 6.1, y + 0.52)
text(s, 0.7, 4.25, 5.4, 0.3, [[("Built today  ", {"color": ACCENT, "font": MONO_FONT, "size": 9, "bold": True}),
                              ("an integrated evidence workflow", {"size": 11})]])
text(s, 0.7, 4.55, 5.4, 0.3, [[("Still to prove  ", {"color": AMBER, "font": MONO_FONT, "size": 9, "bold": True}),
                              ("reliability across varied physical jobs", {"size": 11})]])
rect(s, 6.45, 2.0, 2.85, 1.65, fill=SURFACE, line=RULE, line_w=0.5)
label(s, 6.6, 2.08, 2.6, "WHY IT IS POSSIBLE — AND HARD TO COPY", MUTED)
text(s, 6.6, 2.35, 2.6, 1.3,
     [[("Originals, receipt timestamps, and temporal records are stored together and served through one MCP interface, "
        "so the agent can cite evidence rather than a summary.", {"size": 9, "color": TEXT})],
      [("Defensibility is not a moat yet; it is accumulated workflow evaluation.", {"size": 8, "color": MUTED})]], spacing=4)
rect(s, 6.45, 3.85, 2.85, 1.1, fill=None, line=ACCENT, line_w=0.75)
text(s, 6.6, 3.93, 2.6, 0.95,
     [[("A decision can require another observation.", {"size": 12, "color": ACCENT, "font": TITLE_FONT, "bold": True})],
      [("Requesting it through a robot tool is future work.", {"size": 8, "color": MUTED})]], spacing=3, anchor=MSO_ANCHOR.MIDDLE)

# ---------------------------------------------------------------- 7 Technical design
s = slides[6]
p7_copy = ("Observation becomes agent-accessible memory. Local Mac. DJI + phone RTMP publish. MediaMTX + FFmpeg ingest. "
           "Timestamped frame buffer. Original JPEG evidence + SQLite temporal records. MobileCLIP2 visual retrieval. "
           "FieldNotes MCP. Agent. Portal. OpenAI API vision summaries. selected images. summaries. Future robot MCP: bounded "
           "observation/action tools. Human controls flight. Evidence is sampled. Interpretations can be wrong. "
           "Stack Python 3.12 FastMCP FFmpeg MediaMTX SQLite OpenAI Responses API MobileCLIP2-S0 EdgeTAM optional")
chrome(s, 7, "Technical design",
       "The drone publishes video through DJI Fly. MediaMTX and FFmpeg decode timestamped frames. FieldNotes retains "
       "selected originals, generates vision summaries, and indexes evidence for local visual search. MCP lets an agent "
       "retrieve that history. The portal reads the same backend. Flight remains with the operator. A separate "
       "robot-control interface belongs to the future architecture.\n\n"
       "[32 s] Solid lines = implemented data flow; dotted = future boundary. Selected sampled images are sent to the OpenAI "
       "API while observation is enabled (not all processing is local). MCP is an interface, not a model. Optional EdgeTAM "
       "selected-region tracking (start_visual_track / get_visual_track) is implemented but was not exercised in the inspected "
       "session. Excluded by design: Gaussian splatting, calibrated 3D maps, Core ML, automatic flight control.\n"
       "Sources: src/fieldnotes/{ingest,core,mcp_server,memory,perception}.py; docs/visual-memory.md; docs/sessions.md; "
       "MobileCLIP2 https://machinelearning.apple.com/research/mobileclip2 ; EdgeTAM https://github.com/facebookresearch/EdgeTAM")
headline(s, "Observation becomes agent-accessible memory", "Maximum 150 words · solid = implemented · dotted = future boundary")

# local boundary (everything inside runs on the operator's Mac)
rect(s, 0.7, 2.05, 5.05, 2.45, fill=SURFACE, line=RULE, line_w=0.75)
text(s, 0.8, 2.08, 3, 0.2, "LOCAL MAC", size=7, color=MUTED, font=MONO_FONT, bold=True)
text(s, 6.0, 2.08, 3.3, 0.2, "OUTSIDE THE LOCAL BOUNDARY", size=7, color=AMBER, font=MONO_FONT, bold=True)


def node(x, y, w, h, title, sub=None, hi=False, dash=None, fill=SURFACE2, tsize=9):
    rect(s, x, y, w, h, fill=fill, line=ACCENT if hi else (AMBER if dash else RULE), line_w=1 if (hi or dash) else 0.5, dash=dash)
    text(s, x + 0.05, y + 0.03, w - 0.1, h - 0.06,
         [[(title, {"size": tsize, "bold": True, "color": ACCENT if hi else TEXT})]] +
         ([[(sub, {"size": 7, "color": MUTED})]] if sub else []), anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.CENTER)


# row 1: ingest pipeline
node(0.85, 2.35, 0.95, 0.55, "DJI + phone", "RTMP publish")
line(s, 1.8, 2.62, 1.95, 2.62, color=MUTED, w=1, arrow=True)
node(1.95, 2.35, 1.05, 0.55, "MediaMTX + FFmpeg", "ingest · decode")
line(s, 3.0, 2.62, 3.15, 2.62, color=MUTED, w=1, arrow=True)
node(3.15, 2.35, 0.95, 0.55, "Frame buffer", "timestamped")
line(s, 4.1, 2.62, 4.25, 2.62, color=MUTED, w=1, arrow=True)
node(4.25, 2.35, 1.35, 0.55, "Original JPEG evidence + SQLite records", None, hi=True, tsize=8)
# row 2: retrieval index under the evidence store
line(s, 4.92, 2.9, 4.92, 3.05, color=MUTED, w=1, arrow=True)
node(4.25, 3.05, 1.35, 0.5, "MobileCLIP2 retrieval", "local visual search")
# row 3: MCP + portal
node(0.85, 3.8, 0.95, 0.5, "Portal", "same backend")
line(s, 1.8, 4.05, 1.95, 4.05, color=MUTED, w=1, arrow=True)
node(1.95, 3.8, 3.65, 0.5, "FieldNotes MCP", "status · sessions · evidence · search · memory · tracking", hi=True)
line(s, 4.92, 3.55, 4.92, 3.8, color=ACCENT, w=1, arrow=True)  # evidence + index -> MCP
line(s, 3.6, 3.05, 3.6, 3.8, color=ACCENT, w=1, arrow=True)
line(s, 4.25, 3.05, 3.6, 3.05, color=ACCENT, w=1)
text(s, 2.0, 3.35, 1.5, 0.4, "originals · timestamps · temporal records", size=7, color=MUTED, font=MONO_FONT)
# OpenAI (outside): selected images out, summaries back
node(6.0, 2.35, 1.7, 0.55, "OpenAI API", "vision summaries", fill=BG)
line(s, 5.6, 2.5, 6.0, 2.5, color=MUTED, w=1, arrow=True)
line(s, 6.0, 2.75, 5.6, 2.75, color=MUTED, w=1, arrow=True)
text(s, 5.62, 2.3, 0.4, 0.2, "images", size=6, color=MUTED, font=MONO_FONT)
text(s, 5.55, 2.78, 0.5, 0.2, "summaries", size=6, color=MUTED, font=MONO_FONT)
# Agent (outside) connected through MCP
node(6.0, 3.8, 1.35, 0.5, "Agent", "LLM reasoning", fill=BG)
line(s, 5.6, 3.95, 6.0, 3.95, color=ACCENT, w=1.25, arrow=True)
line(s, 6.0, 4.15, 5.6, 4.15, color=ACCENT, w=1.25, arrow=True)
text(s, 5.6, 4.32, 0.9, 0.2, "MCP (interface)", size=6, color=ACCENT, font=MONO_FONT, align=PP_ALIGN.CENTER)
# Future robot MCP (dotted boundary only)
node(7.85, 3.55, 1.45, 0.75, "Future robot MCP", "bounded observation / action tools", dash=MSO_LINE.DASH, fill=BG)
line(s, 7.35, 4.05, 7.85, 4.05, color=AMBER, w=1, dash=MSO_LINE.DASH, arrow=True)
text(s, 7.85, 4.32, 1.45, 0.2, "NOT IMPLEMENTED", size=7, color=AMBER, font=MONO_FONT, bold=True, align=PP_ALIGN.CENTER)
# footer + stack
text(s, 6.0, 3.0, 3.3, 0.5, "Human controls flight. Evidence is sampled. Interpretations can be wrong.", size=9, color=AMBER)
label(s, 0.7, 4.65, 0.6, "STACK", MUTED)
text(s, 1.3, 4.62, 8.0, 0.3, "Python 3.12 · FastMCP (Streamable HTTP) · FFmpeg · MediaMTX · SQLite · OpenAI Responses API · MobileCLIP2-S0 · EdgeTAM (optional)",
     size=8, color=TEXT, font=MONO_FONT)

# ---------------------------------------------------------------- 8 Roadmap
s = slides[7]
p8_copy = ("Toward evidence-grounded physical agency. Planned milestones. Near term: session review and cited process "
           "timelines. Medium term: evaluate change interpretation across 20 annotated sessions. Long term: connect one "
           "bounded robot observation tool and verify its result. The agent should know when it needs another look.")
chrome(s, 8, "Future roadmap",
       "Next we will improve session review and evaluate interpretations across twenty annotated sessions. Then we will "
       "connect a bounded robot observation tool and check its returned evidence. The broader goal is physical agency where "
       "an agent can recognize that its knowledge is incomplete and obtain the next observation it needs.\n\n"
       "[20 s] All three horizons are planned milestones, not achieved results. 20 sessions is a proposed evaluation target "
       "(ILLUSTRATIVE), not existing evidence. The long-term milestone is a constrained observation capability, not manipulation. "
       "No extra thank-you slide.")
headline(s, "Toward evidence-grounded physical agency", "Planned milestones · maximum 75 words")
label(s, 0.7, 1.97, 3, "PLANNED MILESTONES  ·  NOT ACHIEVED RESULTS", AMBER)
horizons = [("01", "NEAR TERM", "Session review and cited process timelines", None),
            ("02", "MEDIUM TERM", "Evaluate change interpretation across 20 annotated sessions", "20 sessions = proposed target"),
            ("03", "LONG TERM", "Connect one bounded robot observation tool and verify its result", "observation first, not manipulation")]
for i, (n, h, m, note) in enumerate(horizons):
    x = 0.7 + i * 2.9
    rect(s, x, 2.3, 2.75, 1.85, fill=SURFACE, line=ACCENT if i == 2 else RULE, line_w=1 if i == 2 else 0.5)
    text(s, x + 0.15, 2.4, 0.4, 0.3, n, size=11, color=ACCENT, font=MONO_FONT, bold=True)
    text(s, x + 0.55, 2.4, 2.0, 0.3, h, size=9, color=MUTED, font=MONO_FONT, bold=True)
    text(s, x + 0.15, 2.8, 2.45, 0.9, m, size=14, color=TEXT, font=TITLE_FONT, bold=True)
    if note:
        text(s, x + 0.15, 3.75, 2.45, 0.3, note, size=8, color=AMBER, font=MONO_FONT)
    if i < 2:
        line(s, x + 2.75, 3.22, x + 2.9, 3.22, color=MUTED, w=1, arrow=True)
line(s, 0.7, 4.35, 9.25, 4.35, color=RULE)
text(s, 0.7, 4.45, 8.5, 0.5, "The agent should know when it needs another look.", size=20, color=ACCENT, font=TITLE_FONT, bold=True)
if asset("roadmap.png"):
    pass  # kept for source package; the roadmap slide stays spacious without imagery

# ---- word counts -------------------------------------------------------------------
counts = {"problem+inspiration (<=200)": words(p2_copy, p3_copy), "differentiation (<=100)": words(p6_copy),
          "technical (<=150)": words(p7_copy), "roadmap (<=75)": words(p8_copy)}
print(json.dumps(counts, indent=2))
assert counts["problem+inspiration (<=200)"] <= 200 and counts["differentiation (<=100)"] <= 100
assert counts["technical (<=150)"] <= 150 and counts["roadmap (<=75)"] <= 75

out = os.path.join(OUT, "FieldNotes-Physical-Agency.pptx")
prs.save(out)
print("saved", out, "slides", len(prs.slides))
