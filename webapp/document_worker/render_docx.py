#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


def rgb(hex_value):
    value = (hex_value or "222222").lstrip("#")
    return RGBColor.from_string(value.upper())


def set_cell_border(paragraph, color):
    p = paragraph._p
    p_pr = p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    borders.append(bottom)


def add_text(paragraph, text, *, bold=False, italic=False, size=None, color=None, font=None):
    run = paragraph.add_run(str(text or ""))
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = rgb(color)
    if font:
        run.font.name = font
        run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
    return run


def add_heading(doc, title, template):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(3)
    add_text(
        p,
        title,
        bold=True,
        size=template["heading_pt"],
        color=template["accent"],
        font=template["font"],
    )
    if template["section_rule"]:
        set_cell_border(p, template["accent"])
    return p


def add_contact(doc, profile, template):
    bits = [profile.get(k) for k in ("email", "phone", "location") if profile.get(k)]
    if not bits:
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if template["name_align"] == "center" else WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(8)
    add_text(p, " | ".join(bits), size=max(8, template["body_pt"] - 1), font=template["font"])


def add_entry(doc, title, dates, details, template):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(1)
    add_text(p, title, bold=True, size=template["body_pt"], font=template["font"])
    if dates:
        add_text(p, f"   {dates}", italic=True, size=max(8, template["body_pt"] - 1), font=template["font"])
    if details:
        for line in [x.strip() for x in str(details).splitlines() if x.strip()]:
            q = doc.add_paragraph()
            q.paragraph_format.left_indent = Inches(0.08)
            q.paragraph_format.space_after = Pt(2)
            add_text(q, line, size=template["body_pt"], font=template["font"])


def build_cv(doc, profile, template):
    if profile.get("summary"):
        add_heading(doc, "Summary", template)
        p = doc.add_paragraph()
        add_text(p, profile["summary"], size=template["body_pt"], font=template["font"])
    if profile.get("skills"):
        add_heading(doc, "Skills", template)
        p = doc.add_paragraph()
        add_text(p, ", ".join(profile["skills"]), size=template["body_pt"], font=template["font"])
    if profile.get("experience"):
        add_heading(doc, "Experience", template)
        for row in profile["experience"]:
            title = " · ".join(x for x in (row.get("title"), row.get("employer")) if x)
            add_entry(doc, title, row.get("dates"), row.get("details"), template)
    if profile.get("education"):
        add_heading(doc, "Education", template)
        for row in profile["education"]:
            title = " · ".join(x for x in (row.get("school"), row.get("degree")) if x)
            add_entry(doc, title, row.get("dates"), row.get("details"), template)


def build_cover(doc, profile, extra, template):
    if extra.get("role") or extra.get("company"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(10)
        target = " — ".join(x for x in (extra.get("role"), extra.get("company")) if x)
        add_text(p, target, bold=True, size=template["body_pt"], color=template["accent"], font=template["font"])
    for block in [x.strip() for x in str(extra.get("body") or "").splitlines() if x.strip()]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(7)
        add_text(p, block, size=template["body_pt"], font=template["font"])


def main():
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "document.json")
    target = Path(sys.argv[2] if len(sys.argv) > 2 else "output.docx")
    payload = json.loads(source.read_text(encoding="utf-8"))
    profile = payload["snapshot"]["profile"]
    extra = payload["snapshot"].get("extra", {})
    template = payload["template"]

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(template["margin_in"])
    section.bottom_margin = Inches(template["margin_in"])
    section.left_margin = Inches(template["margin_in"])
    section.right_margin = Inches(template["margin_in"])

    normal = doc.styles["Normal"]
    normal.font.name = template["font"]
    normal.font.size = Pt(template["body_pt"])
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), template["font"])

    name = doc.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER if template["name_align"] == "center" else WD_ALIGN_PARAGRAPH.LEFT
    name.paragraph_format.space_after = Pt(2)
    add_text(
        name,
        profile.get("full_name", ""),
        bold=True,
        size=template["name_pt"],
        color=template["accent"],
        font=template["font"],
    )
    add_contact(doc, profile, template)

    if payload["document_type"] == "cv":
        build_cv(doc, profile, template)
    else:
        build_cover(doc, profile, extra, template)

    doc.save(target)


if __name__ == "__main__":
    main()
