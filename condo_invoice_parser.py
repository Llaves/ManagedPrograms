import PyPDF2
import re
import csv
import sys
import os
from pathlib import Path

def extract_invoice_data(pdf_path):
    """Extract data from condo invoice PDF"""
    
    # Initialize data dictionary with all columns
    data = {
        'Rental Days': 0,
        'Homeowner Days': 0,
        'Income': 0.0,
        'Expenses': 0.0,
        'Assoc Fee': 0.0,
        'Commissions': 0.0,
        'TV': 0.0,
        'Electric': 0.0,
        'Common Electric': 0.0,
        'Internet': 0.0,
        'Common Propane': 0.0,
        'HO Propane': 0.0,
        'Water': 0.0,
        'Cleaning': 0.0,
        'Misc Expenses': 0.0
    }
    
    # Read PDF
    with open(pdf_path, 'rb') as file:
        pdf_reader = PyPDF2.PdfReader(file)
        text = ''
        for page in pdf_reader.pages:
            text += page.extract_text()
    
    # Extract rental and homeowner days
    rental_match = re.search(r'Rental Days\s+(\d+)', text)
    if rental_match:
        data['Rental Days'] = int(rental_match.group(1))
    
    homeowner_match = re.search(r'Homeowner Days\s+(\d+)', text)
    if homeowner_match:
        data['Homeowner Days'] = int(homeowner_match.group(1))

    guest_match = re.search(r'Guest Days\s+(\d+)', text)
    if guest_match:
        data['Homeowner Days'] += int(guest_match.group(1))
    
    # Extract Monthly Common Area Assessment (Association Fee)
    assoc_fee_match = re.search(r'Monthly Common Area Assessment.*?(\d+\.\d{2})\s+(\d+\.\d{2})', text)
    if assoc_fee_match:
        data['Assoc Fee'] = float(assoc_fee_match.group(2))
    
    # Extract Internet Service
    internet_match = re.search(r'Internet Service.*?(\d+\.\d{2})', text)
    if internet_match:
        data['Internet'] = float(internet_match.group(1))
    
    # Extract Satellite TV
    tv_match = re.search(r'Satellite TV.*?(\d+\.\d{2})', text)
    if tv_match:
        data['TV'] = float(tv_match.group(1))
    
    # Extract Gas Billing (HO usage = HO Propane)
    ho_gas_match = re.search(r'Gas Billing HO usage.*?(\d+\.\d{2})', text)
    if ho_gas_match:
        data['HO Propane'] = float(ho_gas_match.group(1))
    
    # Extract Gas Billing (Common area = Common Propane)
    common_gas_match = re.search(r'Gas Billing Common area usage.*?(\d+\.\d{2})', text)
    if common_gas_match:
        data['Common Propane'] = float(common_gas_match.group(1))
    
    # Extract Electric Billing HO usage
    ho_electric_match = re.search(r'Electric Billing HO usage.*?(\d+\.\d{2})', text)
    if ho_electric_match:
        data['Electric'] = float(ho_electric_match.group(1))
    
    # Extract Electric Billing Common area
    common_electric_match = re.search(r'Electric Billing Common area usage.*?(\d+\.\d{2})', text)
    if common_electric_match:
        data['Common Electric'] = float(common_electric_match.group(1))
    
    # Extract Water/Sewer/Trash
    water_match = re.search(r'Water/Sewer/Trash.*?(\d+\.\d{2})', text)
    if water_match:
        data['Water'] = float(water_match.group(1))
    
    income_pattern = re.findall(r'HOA Portion of Income.*?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s+0\.25', text)
    total_income = 0.0
    for match in income_pattern:
        amount = float(match.replace(',', ''))
        total_income += amount
    data['Income'] = total_income

    data['Commissions'] = round(data['Income'] * 0.25, 2) 

    # Extract Cleaning Fee - may have multiple entries
    cleaning_pattern = re.findall(r'Cleaning Fee.*?(\d{1,3}(?:,\d{3})*\.\d{2})', text, re.DOTALL)
    total_cleaning = 0.0
    for match in cleaning_pattern:
        amount = float(match.replace(',', ''))
        total_cleaning += amount
        print (f"Found Cleaning Fee: {amount}")
    data['Cleaning'] = total_cleaning
    
    # Extract Expense from Total entry
    expense_match = re.search(r'^\$(\d{1,3}(?:,\d{3})*\.\d{2})$', text, re.MULTILINE)
    if expense_match:
        string_value = expense_match.group(1).replace(',', '')  
        data['Expenses'] = float(string_value)  
    else:
        print("Warning: Could not find Total Expenses in the invoice.")

    # Calculate Misc Expenses
    data['Misc Expenses'] = round(data['Expenses'] - (
        data['Assoc Fee'] + data['Commissions'] + data['TV'] + data['Electric'] +
        data['Common Electric'] + data['Internet'] + data['Common Propane'] +
        data['HO Propane'] + data['Water'] + data['Cleaning']), 2)
    
    
    return data

def write_to_csv(data, output_path):
    """Write extracted data to CSV file"""
    
    columns = [
        'Rental Days', 'Homeowner Days', 'Income', 'Expenses', 'Assoc Fee',
        'Commissions', 'TV', 'Electric', 'Common Electric', 'Internet',
        'Common Propane', 'HO Propane', 'Water', 'Cleaning', 'Misc Expenses'
    ]
    
    with open(output_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=columns)
        writer.writeheader()
        writer.writerow(data)
    
    print(f"CSV file created successfully: {output_path}")

def main():
    # Check for command line argument
    if len(sys.argv) < 2:
        print("Usage: python script.py <input_pdf_file>")
        print("Example: python script.py 'Jan 2025.pdf'")
        sys.exit(1)
    
    # Get input PDF path from command line
    pdf_path = sys.argv[1]
    
    # Check if file exists
    if not os.path.exists(pdf_path):
        print(f"Error: File '{pdf_path}' not found.")
        sys.exit(1)
    
    # Generate output CSV path (same location and name, but .csv extension)
    path_obj = Path(pdf_path)
    output_path = path_obj.with_suffix('.csv')
    
    # Extract data and write to CSV
    print(f"Reading invoice from: {pdf_path}")
    data = extract_invoice_data(pdf_path)
    write_to_csv(data, output_path)
    
    # Print summary
    print("\nExtracted Data:")
    for key, value in data.items():
        print(f"{key}: {value}")

if __name__ == "__main__":
    main()
