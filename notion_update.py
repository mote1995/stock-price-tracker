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
DATABASE_ID = clean_env_var("DATABASE_ID")

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

def main():
    print("--- Starting Notion Update Script ---")
    if not NOTION_TOKEN or not DATABASE_ID:
        print("Error: NOTION_TOKEN or DATABASE_ID is empty after cleaning.")
        return

    # 1. Fetch database schema
    print(f"1. Verifying Database Connection (ID: {DATABASE_ID[:5]}...)...")
    db_url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
    try:
        db_resp = requests.get(db_url, headers=HEADERS, timeout=10)
        if db_resp.status_code != 200:
            print(f"ERROR: Cannot access database. Status: {db_resp.status_code}, Response: {db_resp.text}")
            return
        
        db_data = db_resp.json()
        props_config = db_data.get("properties", {})
        print(f"SUCCESS: Connected to database '{db_data.get('title', [{}])[0].get('plain_text', 'Untitled')}'")
        print(f"Properties found: {', '.join(props_config.keys())}")
        
        if PROP_NAME not in props_config:
            print(f"WARNING: Column '{PROP_NAME}' not found. Please check column names!")
        if PROP_PRICE not in props_config:
            print(f"WARNING: Column '{PROP_PRICE}' not found. No prices will be updated.")

    except Exception as e:
        print(f"ERROR: Exception while verifying database: {e}")
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
