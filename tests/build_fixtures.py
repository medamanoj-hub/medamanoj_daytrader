"""Build synthetic AMC monthly-portfolio workbooks (.xlsx) with the standard
library, shaped like real SEBI disclosures: title rows, an 'as on' date,
a header with ISIN and '% to NAV', equity rows, debt and cash rows, totals.

Usage: python3 tests/build_fixtures.py OUTDIR
"""

import os
import sys
import zipfile
from xml.sax.saxutils import escape


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_xml(rows):
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for i, row in enumerate(rows, 1):
        out.append('<row r="%d">' % i)
        for j, v in enumerate(row):
            if v is None or v == "":
                continue
            ref = "%s%d" % (_col(j), i)
            if isinstance(v, (int, float)):
                out.append('<c r="%s"><v>%s</v></c>' % (ref, v))
            else:
                out.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(str(v))))
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_xlsx(path, sheets):
    """sheets: list of (name, rows)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1)
                             for i in range(len(sheets))) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(n), i + 1, i + 1)
                             for i, (n, _) in enumerate(sheets)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
                             for i in range(len(sheets))) + "</Relationships>")
        for i, (_, rows) in enumerate(sheets):
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), _sheet_xml(rows))


HEADER = ["Name of the Instrument", "ISIN", "Industry / Rating", "Quantity",
          "Market/Fair Value (Rs. in Lakhs)", "% to Net Assets"]


def scheme(title, as_on, equity, fraction=False):
    rows = [["SAMPLE MUTUAL FUND"], [title], ["Monthly Portfolio Statement as on " + as_on], [], HEADER,
            ["Equity & Equity related"], ["(a) Listed / awaiting listing on Stock Exchanges"]]
    for name, isin, ind, qty, val, pct in equity:
        rows.append([name, isin, ind, qty, val, pct / 100 if fraction else pct])
    total_eq = sum(e[5] for e in equity)
    rows += [["Sub Total", "", "", "", "", total_eq / 100 if fraction else total_eq],
             ["Debt Instruments"],
             ["7.18% Government of India 2033", "IN0020230085", "SOV", 500000, 512.3, 0.9 / 100 if fraction else 0.9],
             ["Sample Finance Ltd NCD", "INE000A07AB1", "CRISIL AAA", 100, 101.2, 0.4 / 100 if fraction else 0.4],
             ["TREPS / Net Current Assets", "", "", "", "", (100 - total_eq - 1.3) / 100 if fraction else 100 - total_eq - 1.3],
             ["GRAND TOTAL", "", "", "", "", 1 if fraction else 100]]
    return rows


AUG = [("HDFC Bank Limited", "INE040A01034", "Banks", 1200000, 20500.0, 9.10),
       ("ICICI Bank Limited", "INE090A01021", "Banks", 1100000, 15400.0, 7.05),
       ("Reliance Industries Limited", "INE002A01018", "Petroleum Products", 400000, 11650.0, 5.30),
       ("Infosys Limited", "INE009A01021", "IT - Software", 600000, 9100.0, 4.20),
       ("ITC Limited", "INE154A01025", "Diversified FMCG", 1500000, 6200.0, 2.90),
       ("Axis Bank Limited", "INE238A01034", "Banks", 300000, 3300.0, 1.50)]

SEP = [("HDFC Bank Limited", "INE040A01034", "Banks", 1300000, 22260.0, 9.45),
       ("ICICI Bank Limited", "INE090A01021", "Banks", 1100000, 15380.0, 7.35),
       ("Reliance Industries Limited", "INE002A01018", "Petroleum Products", 380000, 11080.0, 5.40),
       ("Infosys Limited", "INE009A01021", "IT - Software", 600000, 9120.0, 4.65),
       ("ITC Limited", "INE154A01025", "Diversified FMCG", 1500000, 6180.0, 3.90),
       ("Larsen & Toubro Limited", "INE018A01030", "Construction", 150000, 5400.0, 2.10),
       ("Unlisted Sample Co", "INE999Z01011", "Misc", 1000, 10.0, 0.05)]


def build(outdir):
    os.makedirs(outdir, exist_ok=True)
    write_xlsx(os.path.join(outdir, "amc_2026-08.xlsx"),
               [("SAMPLEFLEXI", scheme("Sample Flexi Cap Fund", "August 31, 2026", AUG))])
    write_xlsx(os.path.join(outdir, "amc_2026-09.xlsx"),
               [("SAMPLELIQ", [["Sample Liquid Fund"], ["no equity here"]]),
                ("SAMPLEFLEXI", scheme("Sample Flexi Cap Fund", "30-Sep-2026", SEP, fraction=True))])


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "fixtures_out")
