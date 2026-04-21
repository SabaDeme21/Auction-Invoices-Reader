Auction Invoice PDF Reader

This project is a Python-based script designed to read auction invoices from PDF files and process them into structured data for further analysis. It supports multiple auction companies including Copart, IAAI, and Manheim. The extracted data is organized into a CSV file and can be uploaded to Google Sheets for easy access and analysis.

Key Features:
Extracts invoice data from PDF files using pdfplumber.
Supports multiple auction companies: Copart, IAAI, and Manheim.
Data includes fields such as invoice date, sale date, VIN, charge type, and amount.
Converts text from PDF into a structured DataFrame using regular expressions.
Outputs processed data to a CSV file and logs errors or issues.
Google Sheets integration to automatically update the extracted data.
Technologies Used:
Python: The main programming language.
pdfplumber: For extracting text from PDFs.
pandas: For data manipulation and cleaning.
gspread: For updating Google Sheets with the extracted data.
re: For regex-based text processing.
logging: For logging errors and run status.
datetime: For tracking run time and timestamps.
Files:
auction_final_invoice_reader.csv: The final output CSV file containing the processed invoice data.
auction_errors_invoices.csv: Logs errors encountered during the extraction process.
auction_run_log.csv: Logs the details of each run (duration, status, and errors).
Configuration:
Set the FOLDER_PATH variable to the directory containing the PDF files.
The script detects the company (Copart, IAA, or Manheim) based on the invoice data.
Update the google_sheet_updater function to upload data to your Google Sheets (currently commented out for testing).
Usage:
Set up your environment with the required dependencies (see requirements.txt).
Place the PDF files in the specified FOLDER_PATH.
Run the script, and it will process the invoices, outputting results to CSV and optionally updating Google Sheets.
