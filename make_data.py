from pathlib import Path

out = Path("data/invoices")
out.mkdir(parents=True, exist_ok=True)

invoices = {
    "acme_2026-03.txt": """INVOICE
Vendor: Acme Supplies Pvt Ltd
Invoice No: INV-ACM-1041
Invoice Date: 2026-03-02
Amount: INR 42,300.00
Due Date: 2026-04-01
""",
    "acme_2026-04.txt": """INVOICE
Vendor: Acme Supplies Pvt Ltd
Invoice No: INV-ACM-1087
Invoice Date: 2026-04-03
Amount: INR 48,500.00
Due Date: 2026-05-03
""",
    "globex_2026-04_a.txt": """INVOICE
Vendor: Globex Corp
Invoice No: INV-GLX-220
Invoice Date: 2026-04-10
Amount: INR 15,750.50
Due Date: 2026-05-10
""",
    "globex_2026-04_b.txt": """INVOICE
Vendor: Globex Corp
Invoice No: INV-GLX-231
Invoice Date: 2026-04-10
Amount: INR 22,000.00
Due Date: 2026-05-10
""",
    "initech_2026-04.txt": """INVOICE
Vendor: Initech Ltd
Invoice No: INV-INI-077
Invoice Date: 2026-04-12
Amount: INR 9,200.00
Due Date: Net 30 (see contract)
""",
}

for name, text in invoices.items():
    (out / name).write_text(text, encoding="utf-8")
print("Created", len(invoices), "files in", out)
