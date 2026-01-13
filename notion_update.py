import os
import requests
import datetime
import re
import json

# Notion Configuration from Environment Variables (Aggressively Cleaned)
def clean_env_var(name):
    val = os.environ.get(name, "").strip()
    # Remove common copy-paste prefixes
    val = re.sub(rf"^{name}:\s*", "", val, flags=re.IGNORECASE)
    val = re.sub(rf"^Secret:\s*", "", val, flags=re.IGNORECASE)
    # Remove any remaining newlines or carriage returns
    val = re.sub(r"[\r\n]", "", val)
    return val.strip()

NOTION_TOKEN = clean_env_var("NOTION_TOKEN")
DATABASE_ID = clean_env_var("DATABASE_ID").replace("-", "")

# Property Names in Notion
PROP_NAME = "Investment"
PROP_PRICE = "Current Price"
PROP_STOCK_CODE = "StockCode"
PROP_UPDATE_AT = "UpdateAt"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def get_stock_code_by_name(name):
    """
    Search for stock code using Sina suggest API.
    """
    try:
        url = f"http://suggest3.sinajs.cn/suggest/type=&key={name}"
        response = requests.get(url, timeout=10)
        content = response.content.decode('gbk')
        # Format: var suggestdata_171584...="浦发银行,11,600000,sh600000,浦发银行,,浦发银行,99";
        match = re.search(r'"([^"]+)"', content)
        if match:
            line = match.group(1)
            items = line.split(';')
            if items:
                # Pick the first match
                first_item = items[0].split(',')
                if len(first_item) > 3:
                    code = first_item[3]
                    print(f"  [Search] Found code '{code}' for '{name}'")
                    return code
        print(f"  [Search] No code found for '{name}' in Sina Suggest.")
    except Exception as e:
        print(f"  [Search] Error searching code for {name}: {e}")
    return None

def get_stock_price_sina(code):
    """
    Fetch stock price from Sina Finance API.
    """
    try:
        url = f"http://hq.sinajs.cn/list={code.lower()}"
        headers = {'Referer': 'http://finance.sina.com.cn'}
        response = requests.get(url, headers=headers, timeout=10)
        content = response.content.decode('gbk')
        
        data = content.split('"')[1].split(',')
        if len(data) > 3:
            price = float(data[3])
            print(f"  [API] Current price for {code}: {price}")
            return price
        else:
            print(f"  [API] Unexpected response format for {code}: {content[:50]}...")
    except Exception as e:
        print(f"  [API] Error fetching price for {code}: {e}")
    return None

def fetch_notion_stocks():
    """
    Query Notion database for all stocks.
    """
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    try:
        response = requests.post(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"  [Fetch] Error querying Notion: {response.text}")
            return []
        
        results = response.json().get("results", [])
        stocks = []
        for page in results:
            props = page.get("properties", {})
            
            # Get Stock Code
            code_prop = props.get(PROP_STOCK_CODE, {})
            code_content = ""
            if code_prop:
                p_type = code_prop.get("type")
                if p_type == "title":
                    code_content = "".join([t["plain_text"] for t in code_prop.get("title", [])])
                elif p_type == "rich_text":
                    code_content = "".join([t["plain_text"] for t in code_prop.get("rich_text", [])])

            # Get Stock Name
            name_prop = props.get(PROP_NAME, {})
            name_content = ""
            if name_prop:
                p_type = name_prop.get("type")
                if p_type == "title":
                    name_content = "".join([t["plain_text"] for t in name_prop.get("title", [])])
                elif p_type == "rich_text":
                    name_content = "".join([t["plain_text"] for t in name_prop.get("rich_text", [])])
                
            stocks.append({
                "page_id": page["id"],
                "code": code_content.strip(),
                "name": name_content.strip()
            })
        return stocks
    except Exception as e:
        print(f"  [Fetch] Exception during Notion fetch: {e}")
        return []

def update_notion_page(page_id, price, props_config, code=None):
    """
    Update the price and other properties if they exist in the database.
    """
    url = f"https://api.notion.com/v1/pages/{page_id}"
    now = datetime.datetime.now().isoformat()
    
    update_props = {}
    if PROP_PRICE in props_config:
        update_props[PROP_PRICE] = {"number": price}
    
    if PROP_UPDATE_AT in props_config:
        update_props[PROP_UPDATE_AT] = {"date": {"start": now}}
    
    if code and (PROP_STOCK_CODE in props_config):
        prop_info = props_config[PROP_STOCK_CODE]
        prop_type = prop_info.get("type")
        if prop_type == "rich_text":
            update_props[PROP_STOCK_CODE] = {"rich_text": [{"text": {"content": code}}]}
        elif prop_type == "title":
            update_props[PROP_STOCK_CODE] = {"title": [{"text": {"content": code}}]}

    if not update_props:
        print(f"  [Update] Skipping update for page {page_id} (No valid columns found to update)")
        return

    try:
        data = {"properties": update_props}
        response = requests.patch(url, headers=HEADERS, json=data, timeout=10)
        if response.status_code == 200:
            print(f"  [Update] Success! Page {page_id} updated with price {price}")
        else:
            print(f"  [Update] Failed! Status {response.status_code}, Body: {response.text}")
    except Exception as e:
        print(f"  [Update] Exception during update: {e}")

def find_database_by_name(target_name="Investments"):
    """Search for a database by name if the ID provided is wrong."""
    print(f"\nSearching for database named '{target_name}'...")
    url = "https://api.notion.com/v1/search"
    payload = {
        "query": target_name,
        "filter": {"value": "database", "property": "object"},
        "page_size": 5
    }
    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        if response.status_code == 200:
            results = response.json().get("results", [])
            for db in results:
                title = "Untitled"
                if db.get("title"):
                    title = db["title"][0].get("plain_text", "Untitled")
                if title.lower() == target_name.lower():
                    print(f"FOUND: Successfully matched '{target_name}' (ID: {db['id']})")
                    return db['id'], db.get("properties", {})
            
            if results:
                print(f"No exact match for '{target_name}', but found these:")
                for db in results:
                    t = db["title"][0].get("plain_text", "Untitled") if db.get("title") else "Untitled"
                    print(f"  - '{t}' (ID: {db['id']})")
        else:
            print(f"Search failed: {response.status_code}")
    except Exception as e:
        print(f"Search error: {e}")
    return None, None

def verify_database():
    global DATABASE_ID
    print(f"1. Verifying Database Connection...")
    
    # If ID is "AUTO", jump straight to search
    if DATABASE_ID.upper() == "AUTO":
        print("DATABASE_ID set to 'AUTO'. Searching...")
        new_id, props = find_database_by_name("Investments")
        if new_id:
            DATABASE_ID = new_id
            print(f"SUCCESS: Auto-detected database '{new_id}'")
            return True, props
        print("ERROR: Could not auto-detect 'Investments' database.")
        return False, {}

    # Try direct access first
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
    try:
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            data = response.json()
            title = data['title'][0]['plain_text'] if data.get('title') else "Untitled"
            print(f"SUCCESS: Connected to database: '{title}' (ID: {DATABASE_ID})")
            
            # Verify columns exist
            props = data.get("properties", {})
            print(f"Database Properties found: {list(props.keys())}")
            
            missing = []
            if PROP_NAME not in props: missing.append(PROP_NAME)
            if PROP_PRICE not in props: missing.append(PROP_PRICE)
            
            if missing:
                print(f"WARNING: Missing properties: {missing}")
                print(f"Tip: Ensure your columns are named exactly: '{PROP_NAME}' and '{PROP_PRICE}'")
            return True, props
        
        # If direct access fails (404/400), try to find it by name as a fallback
        print(f"Direct access to ID '{DATABASE_ID}' failed (Status {response.status_code}). Attempting auto-detection...")
        new_id, props = find_database_by_name("Investments")
        if new_id:
            DATABASE_ID = new_id
            print(f"SUCCESS: Auto-detected database '{new_id}'")
            return True, props
            
        print(f"ERROR: Cannot access database. Status: {response.status_code}, Response: {response.text}")
        return False, {}
    except Exception as e:
        print(f"ERROR during connection: {str(e)}")
        return False, {}

def main():
    print("--- Starting Notion Update Script ---")
    if not NOTION_TOKEN:
        print("CRITICAL ERROR: NOTION_TOKEN not set in environment!")
        return
    if not DATABASE_ID:
        print("CRITICAL ERROR: DATABASE_ID not set in environment! Set it to 'AUTO' to attempt auto-detection.")
        return

    # 1. Verify database connection and properties
    db_ok, props_config = verify_database()
    if not db_ok:
        print("Exiting due to database verification failure.")
        return

    # 2. Fetch entries
    print("2. Fetching records from Notion...")
    entries = fetch_notion_stocks()
    print(f"Found {len(entries)} records.")

    # 3. Process each entry
    for i, entry in enumerate(entries):
        name = entry["name"]
        code = entry["code"]
        print(f"[{i+1}/{len(entries)}] Processing '{name}' (Code: {code or 'MISSING'})...")
        
        new_code_found = None
        if not code:
            if name:
                code = get_stock_code_by_name(name)
                if code:
                    new_code_found = code
                else:
                    continue
            else:
                print("  Skipping: Both Name and Code are empty.")
                continue

        price = get_stock_price_sina(code)
        if price is not None:
            update_notion_page(entry["page_id"], price, props_config, code=new_code_found)
        else:
            print(f"  Skipping: Failed to get price for {code}")

    print("--- Script Finished ---")

if __name__ == "__main__":
    main()
