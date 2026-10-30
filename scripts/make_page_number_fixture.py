"""Make a deliberately unnumbered source page; never infer its label here."""
import json
from pathlib import Path

from reportlab.pdfgen import canvas


def main():
    output = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    source = output / "04_page_number_gap.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(420, 595), invariant=1)
    pdf.setTitle("Intentional printed page-number gap")
    pdf.setAuthor("Local EPUB test fixtures")
    pages = []
    for number in range(1, 6):
        fragments = [f"Native source page {number}: body content stays reflowable.",
                     "The original third page intentionally has no printed label."]
        pdf.setFont("Helvetica", 13)
        pdf.drawString(40, 490, fragments[0])
        pdf.drawString(40, 460, fragments[1])
        label = str(number + 19) if number != 3 else None
        if label is not None:
            pdf.setFont("Helvetica", 9)
            pdf.drawCentredString(210, 23, label)
        pages.append({"page": number, "expected_method": "text", "ordered_fragments": fragments,
                      "actual_printed_page_number": label})
        pdf.showPage()
    pdf.save()
    truth = {"fixture": source.name, "origin": "本项目自制；第3页原书实际没有印刷页码，不是OCR漏字样本", "page_count": 5,
             "pages": pages, "page_number_gap": {"page": 3, "suggested_label": "22", "actual_printed_label": None,
                                                 "requires_explicit_confirmation": True}}
    source.with_suffix(".truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
