from __future__ import annotations

import json
import shutil
from pathlib import Path

from demo_content import REPOSITORY_FILES, SCREENSHOT_TEXT, SCREENSHOTS, documents, evaluation_cases
from demo_records import records
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo_data" / "shopflow"


def build_pdf(path: Path, title: str, pages: list[list[str | list[list[str]]]]) -> None:
    styles = getSampleStyleSheet()
    story = []
    for page_index, blocks in enumerate(pages):
        story.extend(
            [
                Paragraph(title, styles["Title"]),
                Paragraph("ShopFlow · internal demo document", styles["Normal"]),
                Spacer(1, 6 * mm),
            ]
        )
        for block in blocks:
            if isinstance(block, list):
                table = Table(block, repeatRows=1, colWidths=[48 * mm, 115 * mm])
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF6")),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ]
                    )
                )
                story.append(table)
            else:
                style = styles["Heading2"] if block.endswith(":") else styles["BodyText"]
                story.extend([Paragraph(block, style), Spacer(1, 3 * mm)])
        if page_index < len(pages) - 1:
            story.append(PageBreak())
    SimpleDocTemplate(str(path), pagesize=A4, title=title, author="ShopFlow").build(story)


def screenshot(path: Path, title: str, lines: list[str], accent: str) -> None:
    image = Image.new("RGB", (1000, 620), "#F7F9FC")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 55, 930, 565), radius=18, fill="white", outline="#CDD5DF", width=2)
    draw.rectangle((70, 55, 930, 125), fill=accent)
    draw.text((105, 78), title, fill="white", font=ImageFont.load_default(size=24))
    for index, line in enumerate(lines):
        draw.text(
            (110, 175 + index * 58), line, fill="#172B4D", font=ImageFont.load_default(size=18)
        )
    image.save(path, format="PNG", optimize=False)


def architecture(path: Path) -> None:
    image = Image.new("RGB", (1400, 800), "#F7F9FC")
    draw = ImageDraw.Draw(image)
    boxes = {
        "API gateway": (560, 60),
        "Order service": (250, 230),
        "Payment service": (550, 230),
        "Refund service": (850, 230),
        "Authentication service": (100, 470),
        "Upload service": (400, 470),
        "Worker / queue": (750, 470),
        "MongoDB": (1080, 470),
    }
    links = [
        ("API gateway", "Order service"),
        ("API gateway", "Payment service"),
        ("API gateway", "Authentication service"),
        ("API gateway", "Upload service"),
        ("Order service", "Payment service"),
        ("Payment service", "Refund service"),
        ("Payment service", "Worker / queue"),
        ("Refund service", "Worker / queue"),
        ("Order service", "MongoDB"),
        ("Payment service", "MongoDB"),
    ]
    for source, target in links:
        sx, sy = boxes[source]
        tx, ty = boxes[target]
        draw.line((sx + 100, sy + 45, tx + 100, ty + 45), fill="#7A869A", width=3)
    for label, (x, y) in boxes.items():
        draw.rounded_rectangle(
            (x, y, x + 220, y + 90), radius=12, fill="white", outline="#3457D5", width=3
        )
        draw.text((x + 18, y + 32), label, fill="#172B4D", font=ImageFont.load_default(size=20))
    draw.text(
        (50, 35),
        "ShopFlow service relationships",
        fill="#172B4D",
        font=ImageFont.load_default(size=28),
    )
    image.save(path, format="PNG", optimize=False)


def main() -> None:
    for folder in ("raw", "documents", "screenshots", "repository"):
        (DEMO / folder).mkdir(parents=True, exist_ok=True)
    (DEMO / "raw" / "records.json").write_text(json.dumps(records(), indent=2), encoding="utf-8")
    (DEMO / "raw" / "screenshot_metadata.json").write_text(
        json.dumps(SCREENSHOTS, indent=2), encoding="utf-8"
    )
    for filename, (title, pages) in documents().items():
        build_pdf(DEMO / "documents" / filename, title, pages)
    for filename, args in SCREENSHOT_TEXT.items():
        screenshot(DEMO / "screenshots" / filename, *args)
    architecture(DEMO / "architecture.png")
    repository = DEMO / "repository" / "shopflow_api"
    if repository.exists():
        shutil.rmtree(repository)
    repository.mkdir(parents=True)
    for filename, source in REPOSITORY_FILES.items():
        (repository / filename).write_text(source, encoding="utf-8")
    cases = "\n".join(json.dumps(case) for case in evaluation_cases()) + "\n"
    (DEMO / "evaluation_cases.jsonl").write_text(cases, encoding="utf-8")
    print(
        "Built 12 customers, 19 orders/payments, 24 tickets, 10 PDFs, 4 screenshots, one architecture diagram, demo code, and 20 evaluation cases."
    )


if __name__ == "__main__":
    main()
