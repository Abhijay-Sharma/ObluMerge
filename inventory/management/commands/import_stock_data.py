
import pandas as pd
from datetime import datetime
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model
from inventory.models import InventoryItem, DailyStockData

User = get_user_model()
# Define unit and voucher types for mapping
PRODUCT_UNITS = {
    'no': 'Number',
    'pcs': 'Pieces',
    'kg': 'Kilograms',
    'set': 'Set'
}

VOUCHER_TYPES = {
    'sale': 'Sale',
    'purc': 'Purchase',
    'c/note': 'Credit Note',  # Added lowercase version
    'd/note': 'Debit Note',  # Added lowercase version
    'stk jrnl': 'Stock Journal'  # Added lowercase version
}


def clean_and_convert_data(file_path):
    # Load the Excel file
    df = pd.read_excel(file_path, engine='openpyxl')

    # Clean up the data
    df.columns = df.columns.str.strip()  # Strip any extra spaces from column names

    # Ensure correct date format (convert to datetime if needed)
    df['Date'] = pd.to_datetime(df['Date'], format='%d-%b-%y')

    # Try to get the user 'infected'
    try:
        user = User.objects.get(username='infected')
    except User.DoesNotExist:
        print("Error: User 'infected' not found. Please create the user manually.")
        return  # Exit if the user is not found

    # Loop over the rows to process each one
    unique_error = 0
    for index, row in df.iterrows():
        # Clean and prepare the data for saving
        product_name = row['Account Name'].strip() if isinstance(row['Account Name'], str) else str(
            row['Account Name'])  # Remove leading/trailing spaces
        voucher_type = row['Voucher Type'].strip().lower() if isinstance(row['Voucher Type'], str) else row[
            'Voucher Type'].lower()  # Normalize voucher type
        unit = row['In Qty'].split()[1].strip().lower() if pd.notnull(row['In Qty']) and isinstance(row['In Qty'],
                                                                                                    str) else 'no'

        try:
            # Create or get the InventoryItem (Product) and set the 'infected' user
            product, created = InventoryItem.objects.get_or_create(
                name=product_name,
                defaults={'user': user}  # Set the 'infected' user here
            )

            # Clean and convert other fields
            date = row['Date']

            # Check if In Qty, In Amt, Out Qty, and other values are strings or numeric before calling .strip() or converting
            inwards_quantity = float(row['In Qty'].split()[0].strip()) if isinstance(row['In Qty'], str) and pd.notnull(
                row['In Qty']) else row['In Qty'] if pd.notnull(row['In Qty']) else 0
            inwards_value = float(row['In Amt']) if isinstance(row['In Amt'], (str, float)) and pd.notnull(
                row['In Amt']) else 0
            outwards_quantity = float(row['Out Qty'].split()[0].strip()) if isinstance(row['Out Qty'],
                                                                                       str) and pd.notnull(
                row['Out Qty']) else row['Out Qty'] if pd.notnull(row['Out Qty']) else 0
            outwards_value = float(row['Out Amt']) if isinstance(row['Out Amt'], (str, float)) and pd.notnull(
                row['Out Amt']) else 0
            closing_quantity = float(row['Closing Qty'].split()[0].strip()) if isinstance(row['Closing Qty'],
                                                                                          str) and pd.notnull(
                row['Closing Qty']) else row['Closing Qty'] if pd.notnull(row['Closing Qty']) else 0
            closing_value = float(row['Closing Amt']) if isinstance(row['Closing Amt'], (str, float)) and pd.notnull(
                row['Closing Amt']) else 0

            # Check if the voucher type is valid
            if voucher_type not in VOUCHER_TYPES:
                print(f"Error processing row {index + 1}: Invalid Voucher Type: {row['Voucher Type']}")
                continue  # Skip this row and continue with the next one

            # Create the DailyStockData entry
            DailyStockData.objects.create(
                product=product,
                date=date,
                inwards_quantity=inwards_quantity,
                inwards_value=inwards_value,
                outwards_quantity=outwards_quantity,
                outwards_value=outwards_value,
                closing_quantity=closing_quantity,
                closing_value=closing_value,
                unit=unit,
                voucher_type=voucher_type
            )

            print(f"Processed {product_name} for {date} - Voucher: {voucher_type}")

        except Exception as e:
            print(f"Error processing row {index + 1}: {e}")
            error=f"Error processing row {index + 1}: {e}"
            if "UNIQUE" in error:
                unique_error += 1
    print(f"Unique error: {unique_error}")



# Example usage
file_path = "/home/ubuntu/ObluMerge/inventory/management/commands/tally_new_23-26_with-1multiplied.xlsx"
clean_and_convert_data(file_path)

