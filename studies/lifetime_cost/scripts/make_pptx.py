"""Build the AdaptiveCache decks (.pptx).

Outputs two files:
  reports/AdaptiveCache_talk.pptx     — 5 slides, the 4-5 min talk
  reports/AdaptiveCache_appendix.pptx — 18 backup slides for Q&A

Talk (mirrors TALK_5MIN.md, 5 slides ≈ 5 min):
  1. Title — hook
  2. Setup — what's the experiment?
  3. Experiment — fig3 placeholder ablation (HERO)
  4. Mechanism — why "less is more"
  5. Why it matters — closing takeaway

Appendix (backup, never opened during the talk itself):
  - Project arc
  - AdaptiveCache vision
  - Method family tree
  - 12 method panels
  - Pareto SWE-bench, cost decomposition, cliff amplification

Palette (content-informed, matches figures): navy + cream + warm orange + green.
"""

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FIG = ROOT / "reports" / "figures"
OUT_TALK = ROOT / "reports" / "AdaptiveCache_talk.pptx"
OUT_APPENDIX = ROOT / "reports" / "AdaptiveCache_appendix.pptx"

# 16:9 widescreen
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5

# Palette
NAVY        = RGBColor(0x1E, 0x3A, 0x8A)
NAVY_DEEP   = RGBColor(0x14, 0x25, 0x5C)
CREAM       = RGBColor(0xF8, 0xF6, 0xF0)
WARM        = RGBColor(0xE6, 0x9F, 0x00)
GREEN       = RGBColor(0x00, 0x9E, 0x73)
INK         = RGBColor(0x1F, 0x29, 0x37)
MUTED       = RGBColor(0x6B, 0x72, 0x80)
WHITE       = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_NAVY  = RGBColor(0x60, 0x77, 0xC7)

HEAD_FONT = "Calibri"
BODY_FONT = "Calibri"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def add_blank_slide(prs):
    layout = prs.slide_layouts[6]  # blank
    return prs.slides.add_slide(layout)


def fill_background(slide, color):
    bg = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height,
    )
    bg.line.fill.background()
    bg.fill.solid()
    bg.fill.fore_color.rgb = color
    bg.shadow.inherit = False
    # Push to back
    spTree = bg._element.getparent()
    spTree.remove(bg._element)
    spTree.insert(2, bg._element)
    return bg


def add_text(slide, x, y, w, h, text, *,
             size=18, bold=False, italic=False, color=INK, font=BODY_FONT,
             align="left", valign="top"):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.margin_left = tf.margin_right = Emu(0)
    tf.margin_top = tf.margin_bottom = Emu(0)
    tf.word_wrap = True
    if valign == "middle":
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    elif valign == "bottom":
        tf.vertical_anchor = MSO_ANCHOR.BOTTOM

    lines = text.split("\n") if isinstance(text, str) else [text]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
                       "right": PP_ALIGN.RIGHT}[align]
        run = p.add_run()
        run.text = line
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = color
    return tb


def add_image_fit(slide, path, x, y, max_w, max_h):
    """Place image inside a max bounding box, preserving aspect ratio, centered."""
    with Image.open(path) as im:
        iw, ih = im.size
    ratio = iw / ih
    box_ratio = max_w / max_h
    if ratio > box_ratio:
        # image wider than box → fit width
        w = max_w
        h = max_w / ratio
    else:
        h = max_h
        w = max_h * ratio
    cx = x + (max_w - w) / 2
    cy = y + (max_h - h) / 2
    return slide.shapes.add_picture(str(path), Inches(cx), Inches(cy),
                                     Inches(w), Inches(h))


def add_shape(slide, x, y, w, h, *, fill=None, line=None, line_w=0.5,
              shape=MSO_SHAPE.RECTANGLE):
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
    s.shadow.inherit = False
    return s


def add_footer(slide, label, *, on_dark=False):
    color = LIGHT_NAVY if on_dark else MUTED
    add_text(slide, 0.5, SLIDE_H_IN - 0.4, 8, 0.3,
             "AdaptiveCache · Vlad Cainamisir · Harvard",
             size=10, color=color)
    add_text(slide, SLIDE_W_IN - 4, SLIDE_H_IN - 0.4, 3.5, 0.3,
             label, size=10, color=color, align="right")


# ---------------------------------------------------------------------------
# Slide builders
# ---------------------------------------------------------------------------

def slide_title(prs):
    s = add_blank_slide(prs)
    fill_background(s, NAVY_DEEP)

    # Decorative accent rectangle on left edge — visual motif (not under title)
    add_shape(s, 0, 0, 0.35, SLIDE_H_IN, fill=WARM)

    add_text(s, 1.2, 1.7, 11, 0.5,
             "ADAPTIVECACHE", size=14, bold=True, color=WARM,
             font=HEAD_FONT)
    add_text(s, 1.2, 2.25, 11, 1.6,
             "When less is more —\na memory experiment\nin AI agents",
             size=44, bold=True, color=WHITE, font=HEAD_FONT)
    add_text(s, 1.2, 5.4, 11, 0.5,
             "I gave four AI agents the same bug to fix.",
             size=18, italic=True, color=LIGHT_NAVY)
    add_text(s, 1.2, 5.85, 11, 0.5,
             "The one that succeeded was the one I told the least.",
             size=18, italic=True, color=LIGHT_NAVY)
    add_text(s, 1.2, 6.7, 11, 0.4,
             "Vlad Cainamisir  ·  Harvard  ·  AdaptiveCache project",
             size=12, color=LIGHT_NAVY)


def slide_setup(prs):
    """Slide 2 — the experimental setup using a Post-it visual metaphor."""
    s = add_blank_slide(prs)
    fill_background(s, CREAM)

    add_text(s, 0.6, 0.5, 12, 0.6,
             "An AI agent's memory fills up — what should we leave behind?",
             size=28, bold=True, color=NAVY, font=HEAD_FONT)
    add_text(s, 0.6, 1.15, 12, 0.4,
             "Compaction = drop old observations and leave a placeholder. The question is: what should the placeholder say?",
             size=14, italic=True, color=MUTED)

    # Two Post-it cards, side by side
    card_y = 2.2
    card_h = 3.4

    # Plain Post-it
    add_shape(s, 0.9, card_y, 5.5, card_h, fill=RGBColor(0xFF, 0xF4, 0xC8),
              line=RGBColor(0xCC, 0xB8, 0x70), line_w=0.75)
    add_text(s, 1.1, card_y + 0.2, 5.1, 0.45,
             "PLAIN POST-IT", size=12, bold=True, color=NAVY)
    add_text(s, 1.1, card_y + 0.7, 5.1, 0.6,
             "[I read this file]", size=24, bold=True, color=INK,
             font="Consolas", align="center", valign="middle")
    add_text(s, 1.1, card_y + 1.7, 5.1, 1.6,
             "Just a marker. The agent has to re-read the file if it needs the content again.",
             size=15, color=INK, valign="top")

    # Detailed Post-it
    add_shape(s, 6.95, card_y, 5.5, card_h, fill=RGBColor(0xFF, 0xF4, 0xC8),
              line=RGBColor(0xCC, 0xB8, 0x70), line_w=0.75)
    add_text(s, 7.15, card_y + 0.2, 5.1, 0.45,
             "DETAILED POST-IT", size=12, bold=True, color=NAVY)
    add_text(s, 7.15, card_y + 0.65, 5.1, 1.0,
             "[I read this file. It contained:\n setup, configure, runtest,\n makereport, evaluate]",
             size=14, bold=True, color=INK, font="Consolas",
             align="center", valign="middle")
    add_text(s, 7.15, card_y + 1.7, 5.1, 1.6,
             "Same eviction, but the placeholder summarizes what was inside. More information → should be better, right?",
             size=15, color=INK, valign="top")

    # Hypothesis bar at bottom
    add_shape(s, 0.6, 6.05, 12.13, 0.7, fill=NAVY)
    add_text(s, 0.7, 6.15, 12, 0.5,
             "Reasonable expectation: the detailed Post-it should help the AI more.",
             size=15, italic=True, color=WHITE, align="center", valign="middle")

    add_footer(s, "Slide 2 / 5")


def slide_experiment(prs):
    """Slide 3 — fig3 placeholder ablation (HERO)."""
    s = add_blank_slide(prs)
    fill_background(s, CREAM)

    add_text(s, 0.6, 0.4, 12, 0.6,
             "Same bug. Same agent. Four runs. Only the Post-it changed.",
             size=26, bold=True, color=NAVY, font=HEAD_FONT)
    add_text(s, 0.6, 1.05, 12, 0.4,
             "pytest-7490 — a real bug from the pytest test framework.",
             size=14, italic=True, color=MUTED)

    # Hero figure
    add_image_fit(s, FIG / "fig3_placeholder_ablation.png", 0.6, 1.6, 12.13, 4.6)

    # Callout — the surprising result
    add_shape(s, 0.6, 6.4, 12.13, 0.85, fill=WARM)
    add_text(s, 0.8, 6.5, 11.9, 0.6,
             "The DETAILED Post-it lost both runs — agent retried the wrong fix 18 times.",
             size=17, bold=True, color=NAVY_DEEP, valign="middle")

    add_footer(s, "Slide 3 / 5")


def slide_mechanism(prs):
    """Slide 4 — why detailed loses."""
    s = add_blank_slide(prs)
    fill_background(s, CREAM)

    add_text(s, 0.6, 0.5, 12, 0.6,
             "Why does more information make things worse?",
             size=28, bold=True, color=NAVY, font=HEAD_FONT)

    # Two columns: PLAIN vs DETAILED mechanism
    col_y = 1.5
    col_h = 4.7

    # Plain column
    add_shape(s, 0.6, col_y, 6.0, col_h, fill=WHITE,
              line=GREEN, line_w=2.0)
    add_text(s, 0.8, col_y + 0.15, 5.6, 0.5,
             "PLAIN POST-IT  →  succeeds", size=15, bold=True, color=GREEN)
    add_text(s, 0.8, col_y + 0.75, 5.6, 4,
             "Agent reads placeholder.\n"
             "Sees nothing actionable.\n"
             "Forced to re-read the file.\n"
             "Notices a detail it missed.\n"
             "Follows the lead to the right region.\n"
             "Fixes the bug in 4 edits.",
             size=15, color=INK)

    # Detailed column
    add_shape(s, 6.73, col_y, 6.0, col_h, fill=WHITE,
              line=WARM, line_w=2.0)
    add_text(s, 6.93, col_y + 0.15, 5.6, 0.5,
             "DETAILED POST-IT  →  fails", size=15, bold=True, color=WARM)
    add_text(s, 6.93, col_y + 0.75, 5.6, 4,
             "Agent reads function names.\n"
             "Picks one and starts editing.\n"
             "Test fails. Tries again. Fails.\n"
             "Never re-reads the actual file —\n"
             "the placeholder seemed enough.\n"
             "18 edits on the wrong function.",
             size=15, color=INK)

    # Lesson bar
    add_shape(s, 0.6, 6.4, 12.13, 0.85, fill=NAVY)
    add_text(s, 0.8, 6.5, 11.7, 0.6,
             "Lesson: a confident-looking summary anchors the agent on the wrong hypothesis.",
             size=17, bold=True, color=WHITE, valign="middle")

    add_footer(s, "Slide 4 / 5", on_dark=False)


def slide_why_it_matters(prs):
    """Slide 5 — closing takeaway."""
    s = add_blank_slide(prs)
    fill_background(s, NAVY_DEEP)

    add_shape(s, 0, 0, 0.35, SLIDE_H_IN, fill=WARM)

    add_text(s, 1.2, 1.0, 11, 0.5,
             "TAKEAWAYS", size=14, bold=True, color=WARM)

    add_text(s, 1.2, 1.7, 11, 1.2,
             "What you leave behind\nmatters more than what you remove.",
             size=34, bold=True, color=WHITE, font=HEAD_FONT)

    add_text(s, 1.2, 3.7, 11, 0.5,
             "1.  More context can hurt — it removes the agent's pressure to update.",
             size=18, color=LIGHT_NAVY)
    add_text(s, 1.2, 4.4, 11, 0.5,
             "2.  Empty hints force a fresh look. Confident summaries anchor.",
             size=18, color=LIGHT_NAVY)
    add_text(s, 1.2, 5.1, 11, 0.5,
             "3.  Compaction tested 8 strategies — most lose to doing nothing.",
             size=18, color=LIGHT_NAVY)
    add_text(s, 1.2, 5.8, 11, 0.5,
             "    But when you DO compress: less is more.",
             size=18, color=WARM)

    add_text(s, 1.2, 6.7, 11, 0.4,
             "Thank you.   ·   github · /vladcainamisir/adaptivecache",
             size=12, color=LIGHT_NAVY)


# ---------------------------------------------------------------------------
# Appendix slides
# ---------------------------------------------------------------------------

def appendix_divider(prs, title, subtitle):
    s = add_blank_slide(prs)
    fill_background(s, NAVY)
    add_text(s, 1.2, 2.8, 11, 1.5, title, size=44, bold=True, color=WHITE,
             font=HEAD_FONT)
    add_text(s, 1.2, 4.3, 11, 0.6, subtitle, size=20, italic=True,
             color=LIGHT_NAVY)


def slide_figure(prs, title, fig_path, caption, *, slide_num=None):
    """Generic content slide: title + dominant figure + 1-line caption."""
    s = add_blank_slide(prs)
    fill_background(s, CREAM)

    add_text(s, 0.6, 0.4, 12, 0.6, title, size=24, bold=True, color=NAVY,
             font=HEAD_FONT)
    if caption:
        add_text(s, 0.6, 1.0, 12, 0.4, caption, size=13, italic=True,
                 color=MUTED)

    add_image_fit(s, fig_path, 0.6, 1.55, 12.13, 5.4)

    if slide_num:
        add_footer(s, slide_num)


def slide_method(prs, fig_filename, family_label, family_color, *, slide_num):
    s = add_blank_slide(prs)
    fill_background(s, CREAM)

    # Family pill at top-left
    add_shape(s, 0.6, 0.45, 2.5, 0.45, fill=family_color, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    add_text(s, 0.6, 0.45, 2.5, 0.45, family_label, size=11, bold=True,
             color=WHITE, align="center", valign="middle")

    # Method title from filename
    method_name = fig_filename.replace(".png", "").replace("method_", "")
    method_name = method_name.split("_", 1)[1]  # strip "01_" prefix
    add_text(s, 3.3, 0.45, 9.5, 0.5, method_name, size=22, bold=True,
             color=NAVY, font=HEAD_FONT, valign="middle")

    # Figure dominant
    add_image_fit(s, FIG / fig_filename, 0.6, 1.2, 12.13, 5.8)

    add_footer(s, slide_num)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _new_pres():
    p = Presentation()
    p.slide_width = Inches(SLIDE_W_IN)
    p.slide_height = Inches(SLIDE_H_IN)
    return p


def build_talk():
    """5-slide deck for the 4-5 min presentation."""
    global prs
    prs = _new_pres()
    slide_title(prs)
    slide_setup(prs)
    slide_experiment(prs)
    slide_mechanism(prs)
    slide_why_it_matters(prs)
    prs.save(str(OUT_TALK))
    print(f"wrote {OUT_TALK}")
    print(f"  size: {OUT_TALK.stat().st_size / 1024:.0f} KB")
    print(f"  slides: {len(prs.slides)}")


def build_appendix():
    """Backup deck for Q&A — never opened during the talk itself."""
    global prs
    prs = _new_pres()
    n_total = 18  # divider + 3 framing + 12 methods + 3 supporting
    sn = lambda i: f"Backup {i} / {n_total}"

    appendix_divider(prs, "Appendix",
                     "Backup slides — open only if asked.")

    slide_figure(prs,
                 "The project — four phases, one negative-with-a-twist result",
                 FIG / "fig6_project_arc.png",
                 "Phase A: measurement → Phase B: τ-bench → Phase C: SWE-bench → Phase D: mechanism finding.",
                 slide_num=sn(2))

    slide_figure(prs,
                 "AdaptiveCache vision — context as a live, ordered memory",
                 FIG / "fig8_vision.png",
                 "Reorganize the prompt on compaction: pin stable items at the front; leave evictable holes in the suffix.",
                 slide_num=sn(3))

    slide_figure(prs,
                 "The compaction design space — 6 families, 12 methods",
                 FIG / "fig9_method_family_tree.png",
                 "All 12 policies we tested split on which signal they trust to decide what to drop.",
                 slide_num=sn(4))

    method_specs = [
        ("method_01_none.png",                       "BASELINE",      MUTED),
        ("method_02_naive_summary.png",              "SUMMARIZE",     RGBColor(0x9D, 0x7C, 0xD8)),
        ("method_03_microcompact.png",               "SUMMARIZE",     RGBColor(0x9D, 0x7C, 0xD8)),
        ("method_04_prefix_preserving.png",          "SUMMARIZE",     RGBColor(0x9D, 0x7C, 0xD8)),
        ("method_05_position_aware.png",             "REORDER",       RGBColor(0x56, 0xB4, 0xE9)),
        ("method_06_evict_oldest.png",               "HEURISTIC",     WARM),
        ("method_07_smart_evict.png",                "HEURISTIC",     WARM),
        ("method_08_score_periodic.png",             "LLM-SCORED",    RGBColor(0xCC, 0x79, 0xA7)),
        ("method_09_llm_reorganizer.png",            "LLM-SCORED",    RGBColor(0xCC, 0x79, 0xA7)),
        ("method_10_consumption_evict_plain.png",    "ACTION-GRAPH",  RGBColor(0x00, 0x72, 0xB2)),
        ("method_11_consumption_evict_facts.png",    "ACTION-GRAPH",  RGBColor(0x00, 0x72, 0xB2)),
        ("method_12_consumption_evict_outline.png",  "ACTION-GRAPH",  RGBColor(0x00, 0x72, 0xB2)),
    ]
    for i, (fname, fam, col) in enumerate(method_specs, start=5):
        slide_method(prs, fname, fam, col, slide_num=sn(i))

    slide_figure(prs,
                 "Headline result — none dominates on N=10 SWE-bench Lite",
                 FIG / "fig1_pareto_swebench.png",
                 "The training-free heuristic-compaction Pareto frontier vs. no-compaction baseline.",
                 slide_num=sn(17))

    slide_figure(prs,
                 "Cost decomposition — uncached input tokens dominate after cliffs",
                 FIG / "fig2_cost_decomposition.png",
                 "Each compaction event invalidates the prefix cache → uncached re-billing dominates.",
                 slide_num=sn(18))

    slide_figure(prs,
                 "Cliff tax — 10× cost amplification per compaction event",
                 FIG / "fig5_cliff_amplification.png",
                 "$0.10/MTok cached vs $1.00/MTok uncached. One cliff fires the difference for the rest of the trajectory.",
                 slide_num=sn(n_total))

    prs.save(str(OUT_APPENDIX))
    print(f"wrote {OUT_APPENDIX}")
    print(f"  size: {OUT_APPENDIX.stat().st_size / 1024:.0f} KB")
    print(f"  slides: {len(prs.slides)}")


def build():
    build_talk()
    build_appendix()


if __name__ == "__main__":
    build()
