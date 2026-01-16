import json
import time
import logging
import requests
import os
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# Configure logging
def setup_logging(log_file):
    logging.basicConfig(
        level=logging.ERROR,
        #level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler() # Also print to stdout for debugging if run manually
        ]
    )

def load_config():
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error("config.json not found!")
        exit(1)


def load_price_cache(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logging.warning(f"Could not load price cache {path}: {e}")
    return {}


def save_price_cache(path, cache):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f)
    except Exception as e:
        logging.error(f"Failed to save price cache {path}: {e}")


def is_cache_valid(entry, max_age_seconds=3600):
    try:
        ts = float(entry.get('timestamp', 0))
        return (time.time() - ts) < max_age_seconds
    except Exception:
        return False

def saveToFirestore(data, steam_id):
    cred = credentials.Certificate('steam-value-tracker-firebase-adminsdk-fbsvc-6177595f62.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    entries_ref = db.collection('inventory_values').document(steam_id).collection('entries')
    entries_ref.add({
        'value': data,
        'timestamp': firestore.SERVER_TIMESTAMP
    })

def get_inventory(steam_id, app_id, context_id, session):
    """
    Fetches the user's inventory with pagination.
    """
    url = f"https://steamcommunity.com/inventory/{steam_id}/{app_id}/{context_id}"
    # Use 1000 items per page to be safe (5000 sometimes causes 400 Bad Request)
    params = {
        "l": "english",
        "count": 1000
    }
    
    full_assets = []
    full_descriptions = []
    more_items = True
    start_assetid = None
    
    logging.info(f"Fetching inventory for {steam_id} (App: {app_id})...")

    while more_items:
        req_params = params.copy()
        if start_assetid:
            req_params['start_assetid'] = start_assetid
        
        try:
            logging.info(f"Requesting page... (start_assetid={start_assetid})")
            response = session.get(url, params=req_params)
            
            if response.status_code == 429:
                logging.error("Rate limited fetching inventory. Too many requests. Waiting 60s...")
                time.sleep(60)
                continue
            
            if response.status_code != 200:
                logging.error(f"Failed to fetch inventory. Status: {response.status_code}. Response: {response.text}")
                return None

            data = response.json()
            if not data.get('success'):
                logging.error("Steam API reported failure in fetching inventory.")
                return None
            
            assets = data.get('assets', [])
            descriptions = data.get('descriptions', [])
            
            full_assets.extend(assets)
            full_descriptions.extend(descriptions)
            
            if data.get('more_items'):
                start_assetid = data.get('last_assetid')
                time.sleep(2) # Be nice to the API
            else:
                more_items = False
                
        except Exception as e:
            logging.error(f"Exception fetching inventory: {e}")
            return None

    return {
        'assets': full_assets,
        'descriptions': full_descriptions,
        'success': True
    }

def get_item_price(market_hash_name, app_id, currency, session, price_cache=None, cache_path=None):
    """
    Fetches price for a single item.
    """
    url = "https://steamcommunity.com/market/priceoverview/"
    params = {
        "appid": app_id,
        "currency": currency,
        "market_hash_name": market_hash_name
    }
    
    # Check cache first (1 hour)
    try:
        key = market_hash_name
        entry = None
        if price_cache is not None:
            entry = price_cache.get(key)
        if entry and entry.get('appid') == str(app_id) and entry.get('currency') == str(currency) and is_cache_valid(entry, 3600):
            try:
                return float(entry.get('price', 0.0))
            except Exception:
                pass

        response = session.get(url, params=params)
        
        if response.status_code == 429:
            logging.warning(f"Rate limited on price check for {market_hash_name}. Waiting longer...")
            return "429"

        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                # Prefer lowest_price, fallback to median_price
                price_str = data.get('lowest_price', data.get('median_price'))
                if price_str:
                    # Clean string (e.g., "$1.23" -> 1.23 or "1,23€" -> 1.23)
                    # This implies basic cleaning; currency symbols vary wildly.
                    
                    # Simple hacky parser for common steam formats ($1.00, 1,00€, £1.00)
                    # Remove currency symbols and non-numeric chars except . and ,
                    clean_str = ''.join([c for c in price_str if c.isdigit() or c in ['.', ',']])
                    # Replace , with . if it looks like a decimal separator (European)
                    if ',' in clean_str and '.' not in clean_str:
                        clean_str = clean_str.replace(',', '.')
                    # If both exist, standard is usually 1,234.56 or 1.234,56
                    try:
                        price_val = float(clean_str)
                        # update cache
                        if price_cache is not None and cache_path:
                            price_cache[key] = {
                                'price': price_val,
                                'timestamp': time.time(),
                                'appid': str(app_id),
                                'currency': str(currency)
                            }
                            save_price_cache(cache_path, price_cache)
                        return price_val
                    except ValueError:
                        logging.warning(f"Could not parse price string: {price_str}")
                        if price_cache is not None and cache_path:
                            price_cache[key] = {
                                'price': 0.0,
                                'timestamp': time.time(),
                                'appid': str(app_id),
                                'currency': str(currency)
                            }
                            save_price_cache(cache_path, price_cache)
                        return 0.0
            return 0.0
        else:
            logging.warning(f"Failed to get price for {market_hash_name}. Status: {response.status_code}")
            return 0.0
    except Exception as e:
        logging.error(f"Error getting price for {market_hash_name}: {e}")
        return 0.0

def main():
    config = load_config()
    setup_logging(config.get('log_file', 'inventory_value.log'))
    
    steam_id = config['steam_id']
    app_id = config['app_id']
    context_id = config['context_id']
    currency = config['currency']
    sleep_interval = config.get('sleep_interval', 3)

    if steam_id == "YOUR_STEAM_ID_64":
        logging.error("Please configure your Steam ID in config.json")
        return

    session = requests.Session()
    # Spoof User-Agent and Referer to avoid strict bot blocking and 400 errors
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': f'https://steamcommunity.com/profiles/{steam_id}/inventory'
    })

    # Load price cache (JSON file) used for 1-hour caching of market prices
    cache_path = config.get('price_cache_file', 'price_cache.json')
    price_cache = load_price_cache(cache_path)

    inventory_data = get_inventory(steam_id, app_id, context_id, session)
    
    if not inventory_data:
        logging.error("Exiting due to inventory fetch failure.")
        return

    # Process Inventory
    # The inventory response separates 'assets' (instances) and 'descriptions' (item details)
    assets = inventory_data.get('assets', [])
    descriptions = inventory_data.get('descriptions', [])
    
    # Map classid_instanceid to market_hash_name
    # descriptions have 'classid' and optionally 'instanceid'
    # We need to link assets to descriptions to get the name
    
    desc_map = {}
    for desc in descriptions:
        # Key can be just classid usually, but instanceid helps for specific skins sometimes
        key = (desc['classid'], desc['instanceid'])
        # Also store by just classid as fallback if instanceid is '0' in asset but specific in description?
        # Usually asset.classid matches description.classid.
        desc_map[desc['classid']] = desc

    # Aggregate counts by market_hash_name
    item_counts = {}
    
    for asset in assets:
        classid = asset['classid']
        # instanceid = asset['instanceid'] # often not needed for name mapping if market_hash_name is consistent
        
        desc = desc_map.get(classid)
        if desc and desc.get('marketable', 0) == 1: # Only price marketable items
            name = desc['market_hash_name']
            item_counts[name] = item_counts.get(name, 0) + 1
        else:
            # Non-marketable item, skip or log
            pass

    logging.info(f"Found {len(item_counts)} unique marketable items.")
    
    total_value = 0.0
    # session created above

    current_index = 0
    total_items = len(item_counts)

    for name, count in item_counts.items():
        current_index += 1
        
        # Get Price (use cache)
        price = get_item_price(name, app_id, currency, session, price_cache=price_cache, cache_path=cache_path)
        
        if price == "429":
            logging.warning("Hit rate limit. Sleeping for 60 seconds...")
            time.sleep(60)
            price = get_item_price(name, app_id, currency, session, price_cache=price_cache, cache_path=cache_path) # Retry once
            if price == "429":
                price = 0.0 # Give up on this item
        
        item_total = price * count
        total_value += item_total
        
        logging.info(f"[{current_index}/{total_items}] {name}: {count} x {price} = {item_total:.2f}")
        
        # Sleep to avoid rate limits
        time.sleep(sleep_interval)
    
    # Persist cache after run
    try:
        save_price_cache(cache_path, price_cache)
    except Exception:
        pass

    # Save to Firestore
    try:
        saveToFirestore(total_value, steam_id)
    except Exception as e:
        logging.error(f"Error saving to Firestore: {e}")
    
    logging.info(f"--------------------------------------------------")
    logging.info(f"Total Inventory Value: {total_value:.2f} (Currency ID: {currency})")
    logging.info(f"Task Completed.")

if __name__ == "__main__":
    main()
