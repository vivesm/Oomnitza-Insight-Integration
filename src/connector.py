import os
import sys
import logging
import requests
import datetime
import base64

# --- 1. Configure Logging ---
logging.basicConfig(
   level=logging.INFO,
   format='%(asctime)s - %(levelname)s - %(message)s',
   stream=sys.stdout,
)

# --- 2. Load and Validate Credentials from Environment Variables ---
logging.info("Loading credentials from environment variables.")

try:
    from dotenv import load_dotenv
    load_dotenv()
    logging.info("Loaded environment variables from .env file.")
except ImportError:
    logging.info("python-dotenv not installed; skipping .env loading.")

def get_env_str(var_name):
    value = os.environ.get(var_name)
    if isinstance(value, bytes):
        value = value.decode()
    elif value is not None and not isinstance(value, str):
        value = str(value)
    return value

OOMNITZA_URL = get_env_str("OOMNITZA_URL")
OOMNITZA_API_TOKEN = get_env_str("OOMNITZA_API_TOKEN")
INSIGHT_CLIENT_ID = get_env_str("INSIGHT_CLIENT_ID")
INSIGHT_CLIENT_KEY = get_env_str("INSIGHT_CLIENT_KEY")
INSIGHT_CLIENT_SECRET = get_env_str("INSIGHT_CLIENT_SECRET")
INSIGHT_URL = get_env_str("INSIGHT_URL")

REQUIRED_VARS = {
   "OOMNITZA_URL": OOMNITZA_URL,
   "OOMNITZA_API_TOKEN": OOMNITZA_API_TOKEN,
   "INSIGHT_CLIENT_ID": INSIGHT_CLIENT_ID,
   "INSIGHT_CLIENT_KEY": INSIGHT_CLIENT_KEY,
   "INSIGHT_CLIENT_SECRET": INSIGHT_CLIENT_SECRET,
   "INSIGHT_URL": INSIGHT_URL,
}

missing_vars = [k for k, v in REQUIRED_VARS.items() if not v]
if missing_vars:
   logging.error(f"Missing required environment variables: {', '.join(missing_vars)}. Exiting.")
   sys.exit(1)

logging.info("All required credentials loaded successfully.")

# --- Set Insight order creation dates to yesterday ---
yesterday = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
os.environ['INSIGHT_ORDER_CREATION_DATE_FROM'] = yesterday
os.environ['INSIGHT_ORDER_CREATION_DATE_TO'] = yesterday
logging.info(f"Set INSIGHT_ORDER_CREATION_DATE_FROM and TO to {yesterday}")

def get_insight_assets():
    """
    Fetch asset procurement data from the Insight API.
    Handles authentication, pagination, and error logging.
    Returns a list of asset records (raw JSON from Insight).
    """
    logging.info("Starting fetch from Insight API.")
    
    # Use Basic Authentication with Base64 encoded credentials
    credentials = f"{INSIGHT_CLIENT_KEY}:{INSIGHT_CLIENT_SECRET}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    headers = {
        'Authorization': f'Basic {encoded_credentials}',
        'Accept': 'application/json',
    }
    # Debug: log header types and values
    for k, v in headers.items():
        logging.info(f"Header {k}: type={type(v)}, value={'***' if k == 'Authorization' else v}")
    assets = []
    page = 1
    per_page = 100
    while True:
        # Build the POST body as required by the Insight API (example structure, adjust as needed)
        payload = {
            "MT_Status2Request": {
                "StatusRequest": [
                    {
                        "ClientID": INSIGHT_CLIENT_ID,
                        "TrackingData": os.environ.get('INSIGHT_TRACKING_DATA', ''),
                        "OrderCreationDateFrom": os.environ.get('INSIGHT_ORDER_CREATION_DATE_FROM', ''),
                        "OrderCreationDateTo": os.environ.get('INSIGHT_ORDER_CREATION_DATE_TO', '')
                    }
                ]
            }
        }
        try:
            # Changed from GET to POST, and from params= to json=
            response = requests.post(
                url=INSIGHT_URL,
                json=payload,  # 'json=' sends data in the request body, which is correct
                headers=headers,
                timeout=30
            )
        except requests.RequestException as e:
            logging.error(f"Request to Insight API failed: {e}")
            break
        if response.status_code != 200:
            logging.error(f"Insight API returned {response.status_code}: {response.text}")
            break
        data = response.json()
        # Adjust this extraction as needed based on actual Insight API response structure
        page_assets = data.get('assets', [])
        assets.extend(page_assets)
        logging.info(f"Fetched {len(page_assets)} assets from page {page}.")
        # If the API supports pagination, add logic here; otherwise, break after one request
        break
    logging.info(f"Total assets fetched from Insight: {len(assets)}.")
    return assets

def transform_insight_to_oomnitza(insight_record):
    """
    Transform a single Insight asset record to the Oomnitza API schema.
    Applies field mapping and data cleansing as per PRD.
    Returns a dict ready for Oomnitza ingestion.
    """
    try:
        order = insight_record.get('order', {})
        line_item = insight_record.get('lineItem', {})
        product = line_item.get('product', {})
        tracking = order.get('trackingInfo', {})
        # Field mapping and transformation
        model = product.get('description', '')
        if model:
            model = model.strip()[:255]
        purchase_date = order.get('purchaseDate')
        if purchase_date:
            # Convert ISO 8601 to YYYY-MM-DD
            purchase_date = purchase_date[:10]
        unit_price = line_item.get('unitPrice')
        if unit_price:
            try:
                unit_price = float(str(unit_price).replace('$', '').replace(',', '').strip())
            except Exception:
                unit_price = None
        return {
            'PO_NUMBER': order.get('purchaseOrderNumber'),
            'SERIAL_NUMBER': line_item.get('serialNumber'),
            'SKU': product.get('sku'),
            'MODEL': model,
            'PURCHASE_DATE': purchase_date,
            'PURCHASE_PRICE': unit_price,
            'SHIPPING_CARRIER': tracking.get('carrier'),
            'TRACKING_NUMBER': tracking.get('trackingNumber'),
        }
    except Exception as e:
        logging.error(f"Error transforming record: {e}")
        return None

def push_to_oomnitza(asset):
    """
    Push a single asset to Oomnitza using SERIAL_NUMBER as the primary key.
    If the asset exists, update it; otherwise, create it.
    Returns True if successful, False otherwise.
    """
    headers = {
        'Authorization': f'Bearer {OOMNITZA_API_TOKEN}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
    serial = asset.get('SERIAL_NUMBER')
    if not serial:
        logging.warning("Asset missing SERIAL_NUMBER, skipping.")
        return False
    # 1. Check if asset exists
    search_url = f"{OOMNITZA_URL}/api/assets?serial_number={serial}"
    try:
        resp = requests.get(search_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logging.error(f"Oomnitza search failed for {serial}: {resp.status_code} {resp.text}")
            return False
        results = resp.json().get('assets', [])
        if results:
            # Update existing asset
            asset_id = results[0].get('id')
            if not asset_id:
                logging.error(f"Oomnitza asset found but missing ID for serial {serial}.")
                return False
            update_url = f"{OOMNITZA_URL}/api/assets/{asset_id}"
            r = requests.patch(update_url, headers=headers, json=asset, timeout=30)
            if r.status_code in (200, 204):
                logging.info(f"Updated asset {serial} in Oomnitza.")
                return True
            else:
                logging.error(f"Failed to update asset {serial}: {r.status_code} {r.text}")
                return False
        else:
            # Create new asset
            create_url = f"{OOMNITZA_URL}/api/assets"
            r = requests.post(create_url, headers=headers, json=asset, timeout=30)
            if r.status_code in (200, 201):
                logging.info(f"Created asset {serial} in Oomnitza.")
                return True
            else:
                logging.error(f"Failed to create asset {serial}: {r.status_code} {r.text}")
                return False
    except requests.RequestException as e:
        logging.error(f"Oomnitza API error for {serial}: {e}")
        return False

def test_env_var_types():
    print("TEST: INSIGHT_CLIENT_KEY type:", type(INSIGHT_CLIENT_KEY), "value:", INSIGHT_CLIENT_KEY)
    print("TEST: INSIGHT_CLIENT_SECRET type:", type(INSIGHT_CLIENT_SECRET), "value:", INSIGHT_CLIENT_SECRET)

if __name__ == "__main__":
    """
    This script is deprecated. Please use the official Oomnitza connector runner:

        python connector.py upload insight

    and configure your Insight integration in connectors/insight.py.

    All custom logic has been removed. See README.md for setup instructions.
    """
    logging.info("Starting Insight to Oomnitza data sync.")
    test_env_var_types()
    raw_assets = get_insight_assets()
    transformed_assets = []
    for record in raw_assets:
        asset = transform_insight_to_oomnitza(record)
        if asset and asset.get('SERIAL_NUMBER'):
            transformed_assets.append(asset)
        else:
            logging.warning("Skipping asset due to transformation error or missing SERIAL_NUMBER.")
    logging.info(f"Pushing {len(transformed_assets)} assets to Oomnitza.")
    success_count = 0
    for asset in transformed_assets:
        if push_to_oomnitza(asset):
            success_count += 1
    logging.info(f"Successfully created/updated {success_count} assets in Oomnitza.")
    if success_count < len(transformed_assets):
        logging.warning(f"Failed to process {len(transformed_assets) - success_count} assets. See error logs for details.")
    logging.info("Data sync finished.")
