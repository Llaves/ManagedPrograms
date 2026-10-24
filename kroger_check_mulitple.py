import base64
import os
import smtplib
from email.message import EmailMessage
import requests

CLIENT_ID = os.environ.get("KROGER_CLIENT_ID")
CLIENT_SECRET = os.environ.get("KROGER_CLIENT_SECRET")

# --- Email Configuration ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT")

# Set your local postal code
ZIP_CODE = "87123"
ITEMS_FILE = "kroger_items_list.txt"


def load_items_to_check(filepath=ITEMS_FILE):
  """Reads the list of search terms from a text file."""
  if not os.path.exists(filepath):
    print(f"Warning: {filepath} not found. Returning empty list.")
    return []

  items = []
  with open(filepath, "r", encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      # Ignore empty lines and comment lines starting with #
      if line and not line.startswith("#"):
        items.append(line)
  return items


def send_email_notification(subject, body):
  """Sends an email using Gmail's SMTP server."""
  if not GMAIL_USER or not GMAIL_APP_PASSWORD or not EMAIL_RECIPIENT:
    print(
        "Email credentials (GMAIL_USER, GMAIL_APP_PASSWORD, EMAIL_RECIPIENT) not"
        " fully configured. Skipping email."
    )
    return

  msg = EmailMessage()
  msg.set_content(body)
  msg["Subject"] = subject
  msg["From"] = GMAIL_USER
  msg["To"] = EMAIL_RECIPIENT

  try:
    print("Connecting to Gmail SMTP server...")
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
      server.starttls()
      server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
      server.send_message(msg)
    print("Email notification sent successfully!")
  except Exception as e:
    print(f"Failed to send email: {e}")


def get_kroger_token():
  auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
  b64_auth = base64.b64encode(auth_str.encode()).decode()

  url = "https://api.kroger.com/v1/connect/oauth2/token"
  headers = {
      "Content-Type": "application/x-www-form-urlencoded",
      "Authorization": f"Basic {b64_auth}",
  }
  data = {"grant_type": "client_credentials", "scope": "product.compact"}

  response = requests.post(url, headers=headers, data=data)
  return response.json().get("access_token")


def get_smiths_store_id(headers, zip_code):
  """Queries stores near the zip code and finds the correct Smith's ID."""
  store_url = f"https://api.kroger.com/v1/locations?filter.zipCode.near={zip_code}&filter.limit=10"
  response = requests.get(store_url, headers=headers)

  if response.status_code != 200:
    print(f"Error fetching locations: {response.status_code} {response.text}")
    return None

  stores = response.json().get("data", [])

  for store in stores:
    chain = store.get("chain", "").upper()
    name = store.get("name", "").upper()

    if "SMITHS" in chain or "SMITH" in name:
      store_id = store.get("locationId")
      store_name = store.get("name")
      print(f"Found Smith's Store: {store_name} (ID: {store_id})")
      return store_id

  print("No explicit Smith's chain found near this zip code.")
  return None


def check_item_sale(headers, store_id, search_term):
  """Searches for a specific item term at the store and checks for a promo price."""
  product_url = f"https://api.kroger.com/v1/products?filter.term={requests.utils.quote(search_term)}&filter.locationId={store_id}"
  response = requests.get(product_url, headers=headers)

  if response.status_code != 200:
    print(f"Error searching for '{search_term}': {response.status_code}")
    return None

  products = response.json().get("data", [])

  for item in products:
    description = item.get("description", "")
    for sub_item in item.get("items", []):
      price_info = sub_item.get("price", {})
      regular = price_info.get("regular")
      promo = price_info.get("promo")

      if promo and regular and promo < regular:
        print(f"{description} | Regular: ${regular} Promo: ${promo}")
      else:
        print(f"{description} No promos")

      if promo and regular and promo < regular:
        return {
            "description": description,
            "promo": promo,
            "regular": regular,
            "search_term": search_term,
        }

  return None


def main():
  items_to_check = load_items_to_check()
  if not items_to_check:
    print("No items found to check.")
    return

  token = get_kroger_token()
  if not token:
    print("Authentication failed.")
    return

  headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

  # 1. Fetch store identification ONCE
  store_id = get_smiths_store_id(headers, ZIP_CODE)
  if not store_id:
    print(f"Could not find a valid Smith's store near zip code {ZIP_CODE}.")
    return

  # 2. Iterate through multiple items
  sales_found = []
  for term in items_to_check:
    print(f"\nSearching for: '{term}'...")
    sale_info = check_item_sale(headers, store_id, term)
    if sale_info:
      sales_found.append(sale_info)

  # 3. Send email depending on whether active promos were found
  if sales_found:
    print(f"\n🚨 {len(sales_found)} active sale(s) found!")

    summary_lines = []
    for s in sales_found:
      summary_lines.append(
          f"- {s['description']}: ON SALE for ${s['promo']} (Reg."
          f" ${s['regular']})"
      )

    email_body = (
        "The following items on your tracking list are currently on sale at"
        " Smith's:\n\n"
        + "\n".join(summary_lines)
    )
    send_email_notification("🚨 Smith's Active Promo Alert!", email_body)
  else:
    print("\nNo active sales found for any items on the list.")
    email_body = (
        "No active sales were found for any of the items on your tracking"
        " list at Smith's today."
    )
    send_email_notification("Smith's Promo Alert: No Sales Found", email_body)


if __name__ == "__main__":
  main()