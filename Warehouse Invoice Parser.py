import pdfplumber
import pandas as pd
import re
import os
from datetime import datetime
import gspread
import logging
import warnings
from gspread_dataframe import get_as_dataframe, set_with_dataframe

# ==========================
# LOGGING SETUP
# ==========================

logging.getLogger('pdfminer').setLevel(logging.ERROR)
pd.set_option('mode.chained_assignment', None)

# ==========================
# GOOGLE SHEETS SETUP
# ==========================

gc = gspread.service_account(r"H:\My Drive\globalautobase.json")
sh = gc.open('Warehouse Invoices').worksheet('Data_Read_By_PY')

# ===========================
# CONFIG
# ===========================

FOLDER_PATH = r"I:\My Drive\GlobalAutoBase-ShareFolder\Warehouse Invoices For Read [PDF]"
FOLDER_PATH = r"C:\Users\User\Desktop\war"
OUTPUT_DATA = "warehouse_final_invoice_reader.csv"
ERROR_LOG = "warehouse_errors_invoices.csv"
RUN_LOG = "warehouse_run_log.csv"
OUTPUT_DATA = "warehouse_final_invoice_reader1.csv"
ERROR_LOG = "warehouse_errors_invoices1.csv"
RUN_LOG = "warehouse_run_log1.csv"

def detect_company(text):
    if re.search(r'ATLANTIC', text.upper(), re.IGNORECASE):
        return 'ATLANTIC'
    elif re.search(r"W8 SHIPPING", text.upper(), re.IGNORECASE):
        return 'W8 SHIPPING'
    elif re.search(r"Automoby LLC", text.upper(), re.IGNORECASE):
        return 'INDIGO [AUTOMOBY]'
    elif re.search(r"INDIGOCARS GE LLC", text.upper(), re.IGNORECASE):
        return 'INDIGO [GEORGIA]'
    elif re.search(r"PAZA MOTORS", text.upper(), re.IGNORECASE):
        return 'PAZA MOTORS'
    elif re.search(r'TRT', text.upper(), re.IGNORECASE):
        return 'TRT'
    return None

def find_atlantic_invoice_table(tables):
    required = {'DATE', 'DUE DATE'}

    for table in tables:
        if not table or not table[0]:
            continue

        header = [c.replace('\n', ' ').strip().upper() for c in table[0] if c]
        if required.issubset(set(header)):
            return table
        
def find_atlantic_invoice_price_table(tables):
    required = {'DESCRIPTION OF CHARGES', 'QUANTITY', 'PRICE', 'AMOUNT'}
    match_table = []

    for table in tables:
        if not table or not table[0]:
            continue

        header = [c.replace('\n', ' ').strip().upper() for c in table[0] if c]
        if required.issubset(set(header)):
            match_table.append(table)
    
    return match_table

def time_transform(raw_date):
    if not raw_date:
        return None
    
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", 
                "%d-%b-%Y", "%d-%b-%y", "%b-%d-%Y", "%b-%d-%y", 
                "%b/%d/%Y", "%b/%d/%y", "%B %d, %Y", "%B %d, %y"):
        try:
            return datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

def w8_container_modify(container):
    container = container.upper()
    
    # collapse repeated chars if too long
    collapsed = re.sub(r'(.)\1+', r'\1', container) if len(container) > 11 else container

    # extract valid 4 letters + 7 digits
    match = re.search(r'[A-Z]{4}\d{7}', collapsed)
    return match.group(0) if match else collapsed

def w8_invoice_table(tables):
    required = {'DESCRIPTION', 'AUCTION', 'OTHER', 'DELIVERY', 'SHIPPING', 'STORAGE', 'TOTAL'}
    match_table = []

    for table in tables:
        if not table or not table[0]:
            continue

        header = [c.replace('\n', '').strip().upper() for c in table[0] if c]
        if required.issubset(set(header)):
            match_table.append(table)

    return match_table

# ==========================
# ATLANTIC PARSER
# ==========================

def atlantic_parser(text, table, company, filename):
    date_table = find_atlantic_invoice_table(table)
    if not date_table:
        raise ValueError("Atlantic invoice table not found")
    df = pd.DataFrame(date_table[1:], columns=date_table[0])
    df['Company'] = 'Atlantic'
    df = df[['Company', 'Date', 'Due Date', 'Number']]
    df['Date'] = df['Date'].apply(time_transform)
    df['Due Date'] = df['Due Date'].apply(time_transform)

    container_number = None
    container_number_match = re.search(r'Container\s*No\.?\s*[:\-]?\s*([A-Z]{4}\d{7})', text, re.IGNORECASE)
    if container_number_match:
        container_number = container_number_match.group(1)

    df['Container'] = container_number

    price_tables = find_atlantic_invoice_price_table(table)
    if not price_tables:
        raise ValueError("Atlantic price table not found")

    vin_pattern = re.compile(r'\b([A-HJ-NPR-Z0-9]{12,17})\b')

    price_dfs = []
    carry_current_vin = None  # <-- remember VIN between tables

    for ptable in price_tables:
        pdf = pd.DataFrame(ptable[1:], columns=ptable[0])
        pdf = pdf[['Description of Charges', 'Quantity', 'Amount']]

        current_vin = carry_current_vin  # start with previous vin
        vin_list = []

        for desc in pdf['Description of Charges'].fillna(''):
            vin_match = vin_pattern.search(desc)
            if vin_match:
                current_vin = vin_match.group(1)
                carry_current_vin = current_vin  # update carry current vin
            vin_list.append(current_vin)

        compressed = [vin_list[0]]
        for v in vin_list[1:]:
            if v != compressed[-1]:
                compressed.append(v)

        compressed = [v for v in compressed if v is not None]

        pdf['VIN'] = vin_list

        # Remove VIN description rows
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            pdf = pdf.loc[~pdf['Description of Charges'].str.contains(vin_pattern, regex=True)]
            pdf = pdf.loc[pdf['Quantity'].notna() & (pdf['Quantity'].str.strip() != "")]

        # Convert amounts
        pdf['Amount'] = (pdf['Amount'].str.replace('USD', '', regex=False)
                                        .str.replace(',', '', regex=False)
                                        .str.strip()
                                        .astype(float))
        
        price_dfs.append(pdf)

    ocean_freight_service = pdf.loc[pdf['Description of Charges'].str.contains('Ocean Freight', case=False, na=False)]

    if ocean_freight_service['VIN'].isna().all():
        vin_list = [vin for vin in vin_list if vin is not None]
        vin = set(vin_list)
        
        # Extract the scalar value for 'Amount' (assuming it's the same for all rows)
        vin_price = ocean_freight_service['Amount'].iloc[0] / len(vin)
        
        new_df = []
        for v in vin:
            new_df.append({'Description of Charges': 'Ocean Freight Service', 'Quantity': 1, 'Amount': vin_price, 'VIN': v})
        
        #price_df = price_df.loc[price_df['Description of Charges'] != 'Ocean Freight Service']
        new_df = pd.DataFrame(new_df)
    
        price_df = pd.concat(price_dfs + [new_df], ignore_index=True)
        price_df = price_df.loc[price_df['VIN'].notna()]
    else:
        price_df = pd.concat(price_dfs, ignore_index=True)

    price_df['Container Number'] = container_number
    price_df['Company'] = 'Atlantic'
    price_df['Invoice Date'] = df.at[0, 'Date']
    price_df['Due Date'] = df.at[0, 'Due Date']
    price_df['Invoice Number'] = df.at[0, 'Number']

    df = price_df[['Company', 'Invoice Date', 'Due Date', 'Invoice Number', 'Container Number', 
                   'VIN', 'Description of Charges', 'Amount']].copy()
    
    df = (
    df.groupby([
        'Company', 'Invoice Date', 'Due Date', 'Invoice Number',
        'Container Number', 'VIN', 'Description of Charges'
    ], as_index=False)
    .agg({'Amount': 'sum'})
    )

    df['SST'] = ''
    df['შენიშვნა'] = ''

    df = df[['Company', 'Invoice Date', 'Due Date', 'Invoice Number',
        'Container Number', 'VIN', 'Description of Charges', 'SST', 'შენიშვნა', 'Amount']]

    df = df.assign(Full_Amount = df.groupby(['VIN'])['Amount'].transform('sum'))
    return df

# ==========================
# W8 PARSER
# ==========================

def w8_america_parser(text, table, company, filename):
    invoice_number = None
    invoice_number_match = re.search(r'\bNUMBER\s+([A-Z0-9]+)\b', text.upper(), re.IGNORECASE)
    if invoice_number_match:
        invoice_number = invoice_number_match.group(1)

    raw = filename.upper()
    container_number = re.split(r'[_\.]', raw)[0]

    invoice_date = None
    invoice_date_match = re.search(r'(?:DATE|DUE DATE)\s+(\d{4}[-/]\d{2}[-/]\d{2})', text.upper(), re.IGNORECASE)
    if invoice_date_match:
        invoice_date = invoice_date_match.group(1)

    due_date = None

    price_table = w8_invoice_table(table)
    header = [h.replace('\n', '').strip() if h else None for h in price_table[0][0]]
    data = price_table[0][1:]
    flat_rows = []

    for row in data:
        # split each cell by \n
        split_cells = [str(c).split('\n') if c not in [None, ''] else [""] for c in row]
        # compute maximum subrows
        max_len = max(len(cell) for cell in split_cells)
        
        # build subrows
        for i in range(max_len):
            flat_rows.append([
                split_cells[col_idx][i] if i < len(split_cells[col_idx]) else ""
                for col_idx in range(len(row))
            ])

    df = pd.DataFrame(flat_rows, columns=header)
    df = df.loc[(df['Description'].notna())&(df['Description']!='')]
    vins = [v[1:] for v in re.findall(r'#[A-HJ-NPR-Z0-9]{6,17}', text)]
    df['VIN'] = None

    for i in range(len(vins)):
        df.loc[i, 'VIN'] = vins[i]

    df = df[['VIN', 'Other', 'Delivery', 'Shipping', 'Storage', 'EVfee', 'Add.Services']]
    
    df = df.loc[df['VIN'].notna()]
    df = df.melt(
    id_vars='VIN',
    value_vars=['Other', 'Delivery', 'Shipping', 'Storage', 'EVfee', 'Add.Services'],
    var_name='Description of Charges',
    value_name='Amount')
    df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce')
    df = df.loc[df['Amount']!=0]

    df['Invoice Date'] = invoice_date
    df['Due Date'] = due_date
    df['Invoice Number'] = invoice_number
    df['Container Number'] = container_number
    df['Company'] = 'W8 America'
    df['SST'] = ''
    df['შენიშვნა'] = ''

    df = df[['Company', 'Invoice Date', 'Due Date', 'Invoice Number',
        'Container Number', 'VIN', 'Description of Charges', 'SST', 'შენიშვნა', 'Amount']]
        
    df = df.assign(Full_Amount = df.groupby('VIN')['Amount'].transform('sum'))

    return df

# ==========================
# INDIGO PARSER
# ==========================

def indigo_parser(text, table, company, filename):
    invoice_date = None
    invoice_date_match = re.search(r'\bDate:\s+([A-Za-z]+\s+\d{2},\s+\d{4})\b', text, re.IGNORECASE)
    if invoice_date_match:
        invoice_date = time_transform(invoice_date_match.group(1))

    invoice_number = None
    invoice_number_match = re.search(r'Invoice\s+ID:\s*([A-Za-z0-9-]+)', text, re.IGNORECASE)
    if invoice_number_match:
        invoice_number = invoice_number_match.group(1)

    vin_code_match = re.compile(r'\b[A-HJ-NPR-Z0-9]{17}\b')
    container_code_match = re.compile(r'\b[A-Z]{3,5}[0-9]\w{5,7}\b')
    price_match = re.compile(r'(\d+\.\d{2})\s*USD$')

    cars = []
    buffer = ""

    for line in text.splitlines():
        buffer += '' + line.strip()
        vin = vin_code_match.search(buffer)
        container = container_code_match.search(buffer)
        price = price_match.findall(buffer)

        if vin and container and len(price) >= 1:
            cars.append({
                'VIN': vin.group(0),
                'Container': container.group(0),
                'Price': price[-1]
            })
        buffer = ""

    df = pd.DataFrame(cars, columns=['VIN', 'Container', 'Price'])

    df['Due Date'] = None
    if company == 'INDIGO [AUTOMOBY]':
        df['შენიშვნა'] = ''
        df['Company'] = 'Indigo [Automoby]'
    else:
        df['შენიშვნა'] = 'ქართული ინვოისი!'
        df['Company'] = 'Indigo [Georgia]'

    df['Invoice Date'] = invoice_date
    df['Invoice Number'] = invoice_number
    df['SST'] = ''
    df['Service Type'] = 'Transportation Fee'

    df = df.rename(columns={'Price': 'Amount', 'Container': 'Container Number', 'Service Type': 'Description of Charges'})

    df = df.assign(Full_Amount = df.groupby('VIN')['Amount'].transform('sum'))
    df = df[['Company', 'Invoice Date', 'Due Date', 'Invoice Number', 'Container Number', 
             'VIN', 'Description of Charges', 'SST', 'შენიშვნა', 'Amount', 'Full_Amount']]

    return df

def paza_parser(text, table, company, filename):
    def date_table(tables):
        required = {'DATE', 'INVOICE #'}
        match_table = []

        for tabl in tables:
            if not tabl or not tabl[0]:
                continue

            header = [c.replace('\n', '').strip().upper() for c in tabl[0] if c]
            if required.issubset(set(header)):
                match_table.append(tabl)
            
        if not match_table:
            raise ValueError("Paza date table not found")

        match_table_df = pd.DataFrame(match_table[0][1:], columns=match_table[0][0])
        return match_table_df
    
    def container_table(tables):
        required = {'SAILING DATE', 'ARRIVAL DATE', 'BOOKING#', 'CONTAINER#'}
        match_table = []

        for tabl in tables:
            if not tabl or not tabl[0]:
                continue
            
            header = [c.replace('\n', '').strip().upper() for c in tabl[0] if c]
            
            if required.issubset(set(header)):
                match_table.append(tabl)

        if not match_table:
            raise ValueError("Paza container table not found")
        
        cont_table = match_table[0][0] 
        cont_values = match_table[0][1]
        containers = pd.DataFrame([cont_values], columns=cont_table)

        car_table = match_table[0][2]
        car_table_list = [c for c in car_table if c is not None]

        car_values = match_table[0][3:]
        car_values_list = []
        for v in car_values:
            list_values = []
            for val in v:
                if val is not None and val != '':
                    list_values.append(val)
            car_values_list.append(list_values)

        car_desc = [li for li in car_values_list if len(li) == 3]

        car_info = pd.DataFrame(car_desc, columns=car_table_list)

        return containers, car_info
    
    date_df = date_table(table)
    container_df = container_table(table)[0]
    car_df = container_table(table)[1]
    car_df['Amount'] = car_df['Amount'].str.replace(',', '').astype(float)
    
    cont_date = date_df['Date'].apply(time_transform).iloc[0]
    due_date = date_df['Date'].apply(time_transform).iloc[0]
    invoice_number = date_df['Invoice #'].iloc[0]
    container_number = container_df['Container#'].iloc[0]
    desc_info = car_df.loc[car_df['Amount'] > 0].copy()
    car_info = car_df.loc[car_df['Amount'] == 0].copy()
    car_info['VIN'] = car_info['Description'].str.extract(r'\b([A-HJ-NPR-Z0-9]{17})\b')
    car_info = car_info[['VIN']]
    desc_info['Each VIN'] = desc_info['Amount'] / len(car_info)
    desc_info = desc_info[['Description', 'Each VIN']]

    car_info['Company'] = company.lower().title()
    car_info['Invoice Date'] = cont_date
    car_info['Due Date'] = due_date
    car_info['Invoice Number'] = invoice_number
    car_info['Container Number'] = container_number
    car_info['SST'] = ''
    car_info['შენიშვნა'] = ''
    car_info['Full_Amount'] = desc_info['Each VIN'].iloc[0] if len(desc_info) == 1 else desc_info['Each VIN'].iloc[0] * len(car_info)

    if len(desc_info) == 1:
        car_info = car_info.assign(
            **{
                'Description of Charges': desc_info['Description'].iloc[0],
                'Amount': desc_info['Each VIN'].iloc[0]
            }
        )
    else:
        # Duplicate car_info for every description
        car_info = pd.concat([car_info] * len(desc_info), ignore_index=True)
        
        # Create repeated descriptions and amounts
        repeated_desc = desc_info.loc[desc_info.index.repeat(len(car_info) // len(desc_info))].reset_index(drop=True)
        
        car_info['Description of Charges'] = repeated_desc['Description'].values
        car_info['Amount'] = repeated_desc['Each VIN'].values

    car_info['Description of Charges'] = car_info['Description of Charges'].str.replace('\n', ' ').str.strip()

    df = car_info[['Company', 'Invoice Date', 'Due Date', 'Invoice Number', 'Container Number', 'VIN', 'Description of Charges', 'SST', 'შენიშვნა', 'Amount', 'Full_Amount']].copy()

    print(df)

    return df

def trt_parser(text, table, company, filename):
    vin_pattern = re.findall(r'\b[A-HJ-NPR-Z0-9]{17}\b', text.upper(), re.IGNORECASE)
    if vin_pattern:
        vin = vin_pattern
    
    vin = list(set(vin))

    price_pattern = re.search(r'[TRANSPORTATION, T RANSPORTATION]\s*\$\s*([\d,\.]+)', text.upper(), re.IGNORECASE)
    if price_pattern:
        price = price_pattern.group(0)
        if 'N' in price:
            price = price.replace('N', '')
        if ' ' in price:
            price = price.replace(' ', '')
        if '$' in price:
            price = price.replace('$', '')
        if ',' in price:
            price = price.replace(',', '.')

    container_pattern = re.search(r'\b[A-Z]{4}\d{7}\b', text.upper(), re.IGNORECASE)
    if container_pattern:
        container = container_pattern.group(0)

    date_pattern = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\s+(\d{6,})\b', text.upper(), re.IGNORECASE)
    if date_pattern:
        date = time_transform(date_pattern.group(1))
        invoice_number = date_pattern.group(2)

    price = float(price) / len(vin)

    final_df = []

    for v in vin:
        final_df.append({
            'Company': company,
            'Invoice Date': date,
            'Due Date': date,
            'Invoice Number': invoice_number,
            'Container Number': container,
            'VIN': v,
            'Description of Charges': 'Transportation',
            'SST': '',
            'შენიშვნა': '',
            'Amount': price,
            'Full_Amount': price
        })
    
    df = pd.DataFrame(final_df)                                          

    return df

def google_sheet_updater(df):
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    if df.empty:
        print("No data to update to Google Sheets")

    sh.clear()
    data = df[['Company', 'Invoice Date', 'Due Date', 'Invoice Number', 'Container Number', 
                   'VIN', 'Description of Charges', 'SST', 'შენიშვნა', 'Amount', 'Full_Amount']].values.tolist()
    columns = ['Company', 'Invoice Date', 'Due Date', 'Invoice Number', 'Container Number', 
                   'VIN', 'Description of Charges', 'SST', 'შენიშვნა', 'Amount', 'Full_Amount']
    set_with_dataframe(sh, pd.DataFrame(data, columns=columns), include_index=False, include_column_header=True)

# ==========================
# MAIN PROCESS
# ==========================

parsers = {
    'ATLANTIC': atlantic_parser,
    'W8 SHIPPING': w8_america_parser,
    'INDIGO [AUTOMOBY]': indigo_parser,
    'INDIGO [GEORGIA]': indigo_parser,
    'PAZA MOTORS': paza_parser,
    'TRT': trt_parser
}

pdf_files = [f for f in os.listdir(FOLDER_PATH) if f.lower().endswith('.pdf')]
total = len(pdf_files)

all_dfs = []
error_rows = []
run_logs = []

start_run = datetime.now()
print(f"STARTED AT: {start_run}\n")

for idx, filename in enumerate(pdf_files, 1):
    file_path = os.path.join(FOLDER_PATH, filename)
    file_start = datetime.now()   
    status = "COMPLETED"
    error_msg = None    

    name, ext = os.path.splitext(filename)

    try:
        with pdfplumber.open(file_path) as pdf:
            all_text = []
            all_tables = []

            for page in pdf.pages:
                page_text = page.extract_text() or ""
                all_text.append(page_text)

                page_tables = page.extract_tables() or []
                all_tables.extend(page_tables)

        # Merge all text into a single string

        full_text = "\n".join(all_text)

        company = detect_company(full_text)
        if not company:
            raise ValueError("Company not detected")

        parser = parsers[company]
        df = parser(full_text, all_tables, company, filename)

        print(df)
        df['Source File'] = filename
        all_dfs.append(df)

        new_filename = f'{name}_Done{ext}'
    except Exception as e:
        status = "ERROR"
        error_msg = str(e)
        error_rows.append({'file': filename, 'error': error_msg})
        new_filename = f'{name}_Error{ext}'

    new_file_path = os.path.join(FOLDER_PATH, new_filename)
    os.rename(file_path, new_file_path)

    file_end = datetime.now()
    duration = (file_end - file_start).total_seconds()

    run_logs.append({
        'file': filename,
        'file path': file_path,
        'status': status,
        'error': error_msg,
        'start_time': file_start.strftime("%Y-%m-%d %H:%M:%S"),
        'end_time': file_end.strftime("%Y-%m-%d %H:%M:%S"),
        'duration_seconds': round(duration, 3)
    })

    percent = (idx / total) * 100
    print(f"[{idx}/{total}] {percent:.2f}% | {status} | {filename} | Time: {duration:.2f}s")


df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
df.to_csv(OUTPUT_DATA, index=False, encoding='utf-8-sig')
pd.DataFrame(error_rows).to_csv(ERROR_LOG, index=False, encoding='utf-8-sig')
pd.DataFrame(run_logs).to_csv(RUN_LOG, index=False, encoding='utf-8-sig')

#google_sheet_updater(df)

end_run = datetime.now()
print(f"\nFINISHED AT: {end_run}")
print(f"TOTAL DURATION: {(end_run - start_run)}")
print(f"SUCCESS: {len(all_dfs)} | ERRORS: {len(error_rows)}")
