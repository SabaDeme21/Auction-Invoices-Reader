import pdfplumber
import os
import re
from datetime import datetime
import pandas as pd
import gspread
import logging

# ==========================
# LOGGING SETUP
# ==========================

logging.getLogger('pdfminer').setLevel(logging.ERROR)

# ==========================
# GOOGLE SHEETS SETUP
# ==========================

gc = gspread.service_account(r"H:\My Drive\globalautobase.json")
sh = gc.open('Auction Invoices').worksheet('Data_Read_By_PY')

# ==========================
# CONFIG
# ==========================

FOLDER_PATH = r"C:\Users\User\Desktop\Auction"
OUTPUT_DATA = "auction_final_invoice_reader.csv"
ERROR_LOG = "auction_errors_invoices.csv"
RUN_LOG = "auction_run_log.csv"
JUNK_PREFIXES = [
    "DO NOT USE",
    "PAID IN FULL",
    "REMAINING",
    "TOTAL",
    "WHOLESALE PAID IN FULL TOTAL",
    "ACCOUNT CREDIT",
    "VEHICLE EXCISE TAX"
]

HARD_EXCLUDES = [
    "PAYMENT",
    "BALANCE"
]

# ==========================
# HELPERS
# ==========================

def detect_company(text):
    if re.search(r"\bCOPART\b", text, re.IGNORECASE):
        return "COPART"
    if re.search(r"\bIAAI\b|\bIAA\b", text, re.IGNORECASE):
        return "IAAI"
    if re.search(r'\bMANHEIM\b', text.upper(), re.IGNORECASE):
        return 'MANHEIM'
    return None


def time_transform(raw_date):
    if not raw_date:
        return None

    raw_date = raw_date.strip().split()[0].replace('.', '/')
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def normalize_charge_type(raw_type: str) -> str | None:
    t = raw_type.upper().strip()

    # Remove junk prefixes
    for junk in JUNK_PREFIXES:
        if t.startswith(junk):
            t = t[len(junk):].strip()

    # Hard exclusions
    for bad in HARD_EXCLUDES:
        if bad in t:
            return None

    # Normalize spacing
    t = re.sub(r"\s+", " ", t)

    return t.title() if t else None

# ==========================
# COPART PARSER
# ==========================

def copart_parser(text, company):
    invoice_date = sale_date = invoice_number = vin = None

    m = re.search(r"Bill of Sale Date:\s*(\d{2}[/-]\d{2}[/-]\d{2,4})", text)
    if m:
        invoice_date = time_transform(m.group(1))

    vin_match = re.search(r"\bVIN:\s*([A-Za-z0-9]{6,20})\b",text)

    if not vin_match:
        raise ValueError("VIN not found")

    vin = vin_match.group(1).upper()

    m = re.search(r"\bLOT#:\s*(\d{8})\b", text)
    if m:
        invoice_number = m.group(1)

    m = re.search(r"\bSale:\s*(\d{2}[/-]\d{2}[/-]\d{2,4})\b", text)
    if m:
        sale_date = time_transform(m.group(1))

    line_re = re.compile(
        r'(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})\s+'
        r'(?P<type>[A-Za-z0-9*._ \-]+?)'
        r'(?P<amount>-?\$(?:\d+(?:,\d{3})*(?:\.\d{2})?))'
    )

    rows = []
    for line in text.splitlines():
        m = line_re.search(line)
        if not m:
            continue

        charge_type = m.group("type").strip().upper()

        if charge_type.startswith("PAYMENT"):
            continue

        amount = float(m.group("amount").replace("$", "").replace(",", ""))
        rows.append([
            'Copart', invoice_date, sale_date,
            invoice_number, vin, m.group("type").strip(), amount
        ])

    df = pd.DataFrame(rows, columns=[
        "Company", "Invoice Date", "Sale Date",
        "Invoice Number", "VIN", "Charge Type", "Amount"
    ])

    df['Charge Type'] = df['Charge Type'].str.replace(r'\s*\*$', '', regex=True)

    df = df.groupby(
        ["Company", "Invoice Date", "Sale Date",
         "Invoice Number", "VIN", "Charge Type"],
        as_index=False
    )["Amount"].sum()

    df["Full Amount"] = df.groupby("VIN")["Amount"].transform("sum")

    net_due = re.search(r"Net\sDue\s*\(\s*USD\s*\)\s*[:$]?\s*([0-9,]+\.?[0-9]*)", text)
    if net_due:
        net_due_amount = float(net_due.group(1).replace(",", ""))

    if net_due_amount > 0:
        raise ValueError(f"Unexpected positive Net Due amount: {net_due_amount}")

    return df

# ==========================
# IAAI PARSER
# ==========================

def iaai_parser(text, company):
    
    # ==========================
    # VIN (11–17 chars)
    # ==========================

    vin = None
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if "VIN" in line.upper():
            for nxt in lines[i+1:]:
                m = re.search(r"\b[A-HJ-NPR-Z0-9]{11,17}\b", nxt)
                if m:
                    vin = m.group(0)
                    break
            break

    if not vin:
        raise ValueError("VIN not found")

    # ==========================
    # DATES & INVOICE
    # ==========================

    invoice_date = sale_date = invoice_number = None

    m = re.search(r"Receipt Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.I)
    if m:
        invoice_date = time_transform(m.group(1))

    m = re.search(r"Sale Date\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text, re.I)
    if m:
        sale_date = time_transform(m.group(1))

    m = re.search(r"Receipt\s*#\s*(\d{8})", text, re.I)
    if m:
        invoice_number = m.group(1)

    # ==========================
    # CHARGES
    # ==========================

    line_re = re.compile(
        r'(?P<type>[A-Za-z ][A-Za-z ]+?)\s+'
        r'(?P<amount>-?\$(?:\d+(?:,\d{3})*(?:\.\d{2})?))'
    )

    rows = []

    for line in text.splitlines():
        m = line_re.search(line)
        if not m:
            continue

        raw_type = m.group("type").strip()
        charge_type = normalize_charge_type(raw_type)

        if not charge_type:
            continue

        amount = float(m.group("amount").replace("$", "").replace(",", ""))

        rows.append([
            'IAAI',
            invoice_date,
            sale_date,
            invoice_number,
            vin,
            charge_type,
            amount
        ])

    # ==========================
    # DATAFRAME
    # ==========================

    df = pd.DataFrame(rows, columns=[
        "Company",
        "Invoice Date",
        "Sale Date",
        "Invoice Number",
        "VIN",
        "Charge Type",
        "Amount"
    ])

    df = df.groupby(
        ["Company", "Invoice Date", "Sale Date",
         "Invoice Number", "VIN", "Charge Type"],
        as_index=False
    )["Amount"].sum()

    df["Full Amount"] = df.groupby("VIN")["Amount"].transform("sum")
    return df

def manhaim_parser(text, company):
    vin = None
    vin_match = re.search(r'\bVIN\s+([A-HJ-NRP-Z0-9]{17})\b', text)
    if vin_match:
        vin = vin_match.group(1)

    buy_date = None
    invoice_date = None
    invoice_date_match = re.search(r'\bINVOICE DATE\s*([0-9]{2}[-/][A-Z]{3}[-/][0-9]{4})\b', text)
    if invoice_date_match:
        invoice_date = time_transform(invoice_date_match.group(1))
        buy_date = time_transform(invoice_date_match.group(1))

    invoice_number = None
    invoice_number_match = re.search(r'\bINVOICE\s*#\s*(\d+)\b', text.upper())
    if invoice_number_match:
        invoice_number = invoice_number_match.group(1)

    line_re = re.compile(
        r'''
        ^\d{2}[-/][A-Z]{3}[-/]\d{4}      # date
        \s+\d+                           # numeric ID
        \s+[0-9-]+                       # code
        \s+(?P<description>.+?)          # description (lazy!)
        (?P<amount>\(?\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?\)?)  # FIRST dollar amount only
        ''',
        re.VERBOSE
    )

    rows = []
    for line in text.splitlines():
        m = line_re.search(line)
        if not m:
            continue

        charge_type = m.group("description")
        raw_amount = m.group("amount")
        negative = raw_amount.startswith('(') and raw_amount.endswith(')')
        amount = float(m.group("amount").replace('$', '').replace(',', '').replace('(', '').replace(')', ''))

        if negative:
            amount = -amount
        
        rows.append([
            'Manheim',
            invoice_date,
            buy_date,
            invoice_number,
            vin,
            charge_type,
            amount
        ])

    df = pd.DataFrame(rows, columns=[
        "Company",
        "Invoice Date",
        "Sale Date",
        "Invoice Number",
        "VIN",
        "Charge Type",
        "Amount"
    ])

    df["Full Amount"] = df.groupby("VIN")["Amount"].transform("sum")

    return df

def google_sheet_updater(df):
    if df.empty:
        print("No data to update to Google Sheets")

    sh.clear()
    data = df[['Company', 'Invoice Date', 'Sale Date', 'Invoice Number', 'VIN', 'Charge Type', 'Amount', 'Full Amount']].values.tolist()
    columns = ['Company', 'Invoice Date', 'Sale Date', 'Invoice Number', 'VIN', 'Charge Type', 'Amount', 'Full Amount']
    sh.update([columns] + data)

# ==========================
# MAIN PROCESS
# ==========================

PARSERS = {"COPART": copart_parser, "IAAI": iaai_parser, "MANHEIM": manhaim_parser}

pdf_files = [f for f in os.listdir(FOLDER_PATH) if f.lower().endswith(".pdf")]
total = len(pdf_files)

all_dfs = []
error_rows = []
run_log = []

start_run = datetime.now()
print(f"STARTED AT: {start_run}")

for idx, filename in enumerate(pdf_files, 1):
    file_path = os.path.join(FOLDER_PATH, filename)
    file_start = datetime.now()
    status = "COMPLETED"
    error_msg = None

    name, ext = os.path.splitext(filename)

    try:
        with pdfplumber.open(file_path) as pdf:
            all_text = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                all_text.append(page_text)

            # Merge all text into a single string
            full_text = "\n".join(all_text)

            #print(full_text)
            #print("NO TEXT")

            company = detect_company(full_text)
            if not company:
                raise ValueError("Company not detected")

            parser = PARSERS[company]
            df = parser(full_text, company)
            df["Source File"] = filename
            all_dfs.append(df)
        new_filename = f"{name}_Done{ext}"

    except Exception as e:
        status = "ERROR"
        error_msg = str(e)
        error_rows.append({"file": filename, "error": error_msg})
        new_filename = f"{name}_Error{ext}"

    new_file_path = os.path.join(FOLDER_PATH, new_filename)
    os.rename(file_path, new_file_path)

    file_end = datetime.now()
    duration = (file_end - file_start).total_seconds()

    run_log.append({
        "file": filename,
        "file_path": file_path,
        "status": status,
        "error": error_msg,
        "started_at": file_start.strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": file_end.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_seconds": round(duration, 3)
    })

    percent = (idx / total) * 100
    print(f"[{idx}/{total}] {percent:.2f}% | {status} | {filename} | Time: {duration:.2f}s")

# ==========================
# OUTPUT
# ==========================

final_df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
final_df.to_csv(OUTPUT_DATA, index=False, encoding='utf-8-sig')
pd.DataFrame(error_rows).to_csv(ERROR_LOG, index=False, encoding='utf-8-sig')
pd.DataFrame(run_log).to_csv(RUN_LOG, index=False, encoding='utf-8-sig')

#google_sheet_updater(final_df)

end_run = datetime.now()
print(f"\nFINISHED AT: {end_run}")
print(f"TOTAL DURATION: {(end_run - start_run)}")
print(f"SUCCESS: {len(all_dfs)} | ERRORS: {len(error_rows)}")
