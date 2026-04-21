Auction Invoice PDF Reader

This project is a Python-based script designed to read auction invoices from PDF files and process them into structured data for further analysis. It supports multiple auction companies including Copart, IAAI, and Manheim. The extracted data is organized into a CSV file and can be uploaded to Google Sheets for easy access and analysis.

Key Features:
1)Extracts invoice data from PDF files using pdfplumber.
2)Supports multiple auction companies: Copart, IAAI, and Manheim.
3)Data includes fields such as invoice date, sale date, VIN, charge type, and amount.
4)Converts text from PDF into a structured DataFrame using regular expressions.
5)Outputs processed data to a CSV file and logs errors or issues.
6)Google Sheets integration to automatically update the extracted data.

Technologies Used:
1)Python: The main programming language.
2)pdfplumber: For extracting text from PDFs.
3)pandas: For data manipulation and cleaning.
4)gspread: For updating Google Sheets with the extracted data.
5)re: For regex-based text processing.
6)logging: For logging errors and run status.
7)datetime: For tracking run time and timestamps.

Files:
1)auction_final_invoice_reader.csv: The final output CSV file containing the processed invoice data.
2)auction_errors_invoices.csv: Logs errors encountered during the extraction process.
3)auction_run_log.csv: Logs the details of each run (duration, status, and errors).

Configuration:
1)Set the FOLDER_PATH variable to the directory containing the PDF files.
2)The script detects the company (Copart, IAA, or Manheim) based on the invoice data.
Update the google_sheet_updater function to upload data to your Google Sheets (currently commented out for testing).
Usage:
Set up your environment with the required dependencies (see requirements.txt).
Place the PDF files in the specified FOLDER_PATH.
Run the script, and it will process the invoices, outputting results to CSV and optionally updating Google Sheets.
