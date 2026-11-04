"""Builds the demo dataset: one tender and three vendors with deliberately different characteristics.

  Alpha Precision Works  - technically strong, commercially competitive
  Beta Fabricators       - lowest price, but wrong material, loose tolerance and no AS9100
  Gamma Aerotech         - fully compliant, highest price, slower delivery

Usage: python build_samples.py [output_dir]
"""
import csv
import os
import sys

import docx
import openpyxl
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "files")


def make_docx(path, title, sections):
    d = docx.Document()
    d.add_heading(title, 0)
    for heading, lines in sections:
        d.add_heading(heading, 1)
        for line in lines:
            d.add_paragraph(line)
    d.save(path)


def make_pdf(path, pages):
    c = canvas.Canvas(path, pagesize=A4)
    for lines in pages:
        y = 800
        for line in lines:
            c.drawString(50, y, line)
            y -= 20
        c.showPage()
    c.save()


def make_xlsx(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Commercial"
    for r in rows:
        ws.append(r)
    wb.save(path)


def make_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def build(out=OUT):
    os.makedirs(out, exist_ok=True)
    p = lambda *a: os.path.join(out, *a)

    make_docx(p("Tender_Hydraulic_Fitting_Housing.docx"), "Tender: Hydraulic Fitting Housing", [
        ("Technical Requirements", [
            "Material: SS304 (Mandatory)",
            "Tolerance: ±0.05 mm maximum (Mandatory)",
            "Wall Thickness: minimum 2.5 mm",
            "Surface Finish: Ra 0.8 µm maximum",
            "Certification: AS9100 (Mandatory)",
            "Inspection and Testing: Dye penetrant test",
            "Manufacturing Process: CNC machining",
        ]),
        ("Commercial Requirements", [
            "Quantity: 200 nos",
            "Delivery: maximum 45 days",
            "Warranty: minimum 24 months",
            "Quotation Validity: minimum 60 days",
            "Payment Terms: 30 days from receipt",
            "Estimated Budget: Rs. 12,00,000",
        ]),
    ])

    # --- Alpha: strong and competitive -------------------------------------------------------------------------
    d = "Alpha Precision Works"
    os.makedirs(p(d), exist_ok=True)
    make_docx(p(d, "Technical_Quotation.docx"), "Technical Quotation", [("Offer", [
        "Material: SS304", "Tolerance: ±0.03 mm", "Wall Thickness: 2.8 mm", "Surface Finish: Ra 0.6 µm",
        "Inspection and Testing: Dye penetrant test and hydrostatic test", "Manufacturing Process: CNC machining"])])
    make_xlsx(p(d, "Commercial_Quotation.xlsx"), [
        ["Parameter", "Details"], ["Quantity", "200 nos"], ["Unit Price", "Rs. 5,200"], ["Total Price", "Rs. 10,40,000"],
        ["Freight", "Rs. 20,000"], ["Taxes", "GST extra at 18%"], ["Delivery", "35 days"], ["Warranty", "24 months"],
        ["Quotation Validity", "90 days"], ["Payment Terms", "30 days from receipt"]])
    make_pdf(p(d, "ISO_AS9100_Certificate.pdf"), [["Certificate of Registration", "Alpha Precision Works is certified to AS9100 Rev D.", "Valid until 2028."]])

    # --- Beta: cheapest but deviating --------------------------------------------------------------------------
    d = "Beta Fabricators"
    os.makedirs(p(d), exist_ok=True)
    make_docx(p(d, "Technical_Quotation.docx"), "Technical Quotation", [("Offer", [
        "Material: SS316", "Tolerance: ±0.08 mm", "Wall Thickness: 2.5 mm", "Surface Finish: Ra 0.9 µm",
        "Certification: ISO 9001:2015 only. AS9100 - Not certified",
        "Inspection and Testing: Dye penetrant test", "Manufacturing Process: CNC machining"])])
    make_csv(p(d, "Commercial_Quotation.csv"), [
        ["Parameter", "Details"], ["Quantity", "200 nos"], ["Unit Price", "Rs. 4,400"], ["Total Price", "Rs. 8,80,000"],
        ["Freight", "Rs. 60,000"], ["Taxes", "GST inclusive"], ["Delivery", "30 days"], ["Warranty", "12 months"],
        ["Quotation Validity", "45 days"], ["Payment Terms", "100% advance"]])

    # --- Gamma: compliant, expensive ---------------------------------------------------------------------------
    d = "Gamma Aerotech"
    os.makedirs(p(d), exist_ok=True)
    make_pdf(p(d, "Technical_Quotation.pdf"), [[
        "Technical Quotation", "Material: SS304", "Tolerance: +/-0.04 mm", "Wall Thickness: 3.0 mm", "Surface Finish: Ra 0.8 um"], [
        "Certification: AS9100 Rev D and ISO 9001:2015", "Inspection and Testing: X-ray and dye penetrant test",
        "Manufacturing Process: CNC machining"]])
    make_docx(p(d, "Commercial_Quotation.docx"), "Commercial Quotation", [("Price Bid", [
        "Quantity: 200 nos", "Total Price: Rs. 11,80,000", "Freight: Included", "Taxes: GST extra at 18%",
        "Delivery: 50 days", "Warranty: 36 months", "Quotation Validity: 60 days", "Payment Terms: 30 days from receipt"])])
    make_csv(p(d, "Product_Datasheet.csv"), [["Parameter", "Details"], ["Material", "SS304"], ["Wall Thickness", "3.0 mm"]])
    return out


if __name__ == "__main__":
    print(build())
