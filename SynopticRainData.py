import json
import csv
from datetime import datetime
from playwright.sync_api import sync_playwright
from gooey import Gooey, GooeyParser


def run_playwright(start_datetime: str, end_datetime: str, station_id: str) -> str:
    """
    Executes the Synoptic Data query using Playwright and returns the API response (JSON string).
    """
    with sync_playwright() as playwright:
        print("Launching browser")
        browser = playwright.chromium.launch(headless=True)   # Set to True for headless
        context = browser.new_context()
        page = context.new_page()

        print("Fetching page")
        page.goto("https://demos.synopticdata.com/query-builder/index.html")
        page.get_by_role("link", name=" Use the stations/precipitation service").click()

        page.get_by_role("textbox").nth(1).click()
        page.get_by_role("textbox").nth(1).fill(start_datetime)
        page.get_by_role("textbox").nth(2).click()
        page.get_by_role("textbox").nth(2).fill(end_datetime)

        page.get_by_role("combobox").first.select_option("intervals")
        page.get_by_role("textbox").nth(3).click()
        page.get_by_role("textbox").nth(3).fill("day")
        page.get_by_role("combobox").nth(1).select_option("local")
        page.locator(".form-control.js-queryarg-units").click()
        page.locator(".form-control.js-queryarg-units").fill("precip|in")
        page.locator(".form-control.js-queryarg-stid").click()
        page.locator(".form-control.js-queryarg-stid").fill(station_id)
        
        print("API request submitted")
        page.get_by_text("Make API Request").click()

        response_locator = page.locator(".hljs")
        response_locator.wait_for(state="visible", timeout=15000)
        response_text = response_locator.inner_text()
        
        print("Response received")

        context.close()
        browser.close()
        return response_text


def combine_date_time_to_required_format(date_str: str, time_str: str) -> str:
    """Converts date (YYYY-MM-DD) and time (HH:MM) to YYYYMMDDhhmm."""
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    try:
        time_obj = datetime.strptime(time_str, "%H:%M:%S")
    except ValueError:
        time_obj = datetime.strptime(time_str, "%H:%M")
    combined = datetime(
        date_obj.year, date_obj.month, date_obj.day,
        time_obj.hour, time_obj.minute
    )
    return combined.strftime("%Y%m%d%H%M")


def parse_and_write_csv(response_json: str, output_file: str):
    """
    Parses the JSON response and writes a CSV with:
      - first column: date (YYYY-MM-DD) extracted from first_report
      - for each station: a column "<station_id> 24 hour total"
    Observations are merged by date.
    """
    print("Parsing response")
    data = json.loads(response_json)
    stations = data.get("STATION", [])
    if not stations:
        raise ValueError("No STATION data found in response.")

    # Dictionary: date -> {station_id: total}
    date_entries = {}
    station_ids = set()

    for station in stations:
        stid = station.get("STID", "Unknown")
        station_ids.add(stid)
        observations = station.get("OBSERVATIONS", {})
        precip_list = observations.get("precipitation", [])
        for entry in precip_list:
            first_report = entry.get("first_report")
            if not first_report:
                continue
            # Extract date (first 10 characters: YYYY-MM-DD)
            date_key = first_report[:10]
            total = entry.get("total", "")
            if date_key not in date_entries:
                date_entries[date_key] = {}
            # If multiple entries for same date from same station, we overwrite with last;
            # but usually there's only one per day. To be safe, we could sum or keep last.
            date_entries[date_key][stid] = total

    if not date_entries:
        raise ValueError("No precipitation observations found in any station.")

    # Sort dates
    sorted_dates = sorted(date_entries.keys())
    sorted_stations = sorted(station_ids)

    print("Writing csv")
    # Write CSV
    with open(output_file, 'w', newline='') as csvfile:
        header = ["date"] + [f"{stid} 24 hour total" for stid in sorted_stations]
        writer = csv.DictWriter(csvfile, fieldnames=header)
        writer.writeheader()

        for dt in sorted_dates:
            row = {"date": dt}
            for stid in sorted_stations:
                row[f"{stid} 24 hour total"] = date_entries[dt].get(stid, "")
            writer.writerow(row)

    print(f"CSV written to: {output_file}")


@Gooey(
    program_name="Synoptic Data API Query",
    program_description="Fetch precipitation data and export to CSV",
    default_size=(700, 550),
    navigation='TABBED',
    tabbed_groups=[
        {
            'name': 'Query Parameters',
            'columns': 2,
        }
    ]
)
def main():
    parser = GooeyParser(description="Enter the query parameters")

    parser.add_argument(
        'start_date',
        widget='DateChooser',
        help='Start date (YYYY-MM-DD)',
        default='2026-01-01'
    )
    parser.add_argument(
        'start_time',
        widget='TimeChooser',
        help='Start time (HH:MM) – 24‑hour format',
        default='00:00'
    )
    parser.add_argument(
        'end_date',
        widget='DateChooser',
        help='End date (YYYY-MM-DD)',
        default='2026-01-31'
    )
    parser.add_argument(
        'end_time',
        widget='TimeChooser',
        help='End time (HH:MM) – 24‑hour format',
        default='23:59'
    )
    parser.add_argument(
        'station_id',
        widget='TextField',
        help='Station ID(s) – separate multiple with commas (e.g., DPKN5,WBB)',
        default='DPKN5'
    )
    parser.add_argument(
        'output_file',
        widget='FileSaver',
        help='Select where to save the CSV file',
        default='precipitation_data.csv'
    )

    args = parser.parse_args()
    
    # Ensure output file has .csv extension
    output_file = args.output_file
    if not output_file.lower().endswith('.csv'):
        output_file += '.csv'
        
    print(output_file)

    start = combine_date_time_to_required_format(args.start_date, args.start_time)
    end = combine_date_time_to_required_format(args.end_date, args.end_time)

    try:
        response_text = run_playwright(start, end, args.station_id)
        parse_and_write_csv(response_text, output_file)
        print("CSV export completed successfully!")
    except Exception as e:
        print(f"An error occurred: {e}")
        raise


if __name__ == "__main__":
    main()