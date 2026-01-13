import os
import requests
import datetime
import re

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
        response = requests.get(url)
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
                    return first_item[3] # e.g., sh600000
    except Exception as e:
        print(f"Error searching code for {name}: {e}")
    return None

def get_stock_price_sina(code):
    """
    Fetch stock price from Sina Finance API.
    code format: sh600000, sz000001
    """
    try:
        url = f"http://hq.sinajs.cn/list={code.lower()}"
        headers = {'Referer': 'http://finance.sina.com.cn'}
        response = requests.get(url, headers=headers)
        content = response.content.decode('gbk')
        
        data = content.split('"')[1].split(',')
        if len(data) > 3:
            return float(data[3])
    except Exception as e:
        print(f"Error fetching price for {code}: {e}")
    return None

def fetch_notion_stocks():
    """
    Query Notion database for all stocks.
    """
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    response = requests.post(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Error querying Notion: {response.text}")
        return []
    
    results = response.json().get("results", [])
    stocks = []
    for page in results:
        props = page.get("properties", {})
        
        # Get Stock Code
        code_prop = props.get(PROP_STOCK_CODE, {})
        code_content = ""
        if code_prop:
            if code_prop.get("type") == "title":
                code_content = "".join([t["plain_text"] for t in code_prop.get("title", [])])
            elif code_prop.get("type") == "rich_text":
                code_content = "".join([t["plain_text"] for t in code_prop.get("rich_text", [])])

        # Get Stock Name (Fallback if code is missing)
        name_prop = props.get(PROP_NAME, {})
        name_content = ""
        if name_prop:
            if name_prop.get("type") == "title":
                name_content = "".join([t["plain_text"] for t in name_prop.get("title", [])])
            elif name_prop.get("type") == "rich_text":
                name_content = "".join([t["plain_text"] for t in name_prop.get("rich_text", [])])
            
        stocks.append({
            "page_id": page["id"],
            "code": code_content.strip(),
            "name": name_content.strip()
        })
    return stocks

def update_notion_page(page_id, price, props_config, code=None):
    """
    Update the price and other properties if they exist in the database.
    """
    url = f"https://api.notion.com/v1/pages/{page_id}"
    now = datetime.datetime.now().isoformat()
    
    # Core update: Price
    update_props = {}
    if PROP_PRICE in props_config:
        update_props[PROP_PRICE] = {"number": price}
    
    # Optional update: Time
    if PROP_UPDATE_AT in props_config:
        update_props[PROP_UPDATE_AT] = {"date": {"start": now}}
    
    # Optional update: Stock Code (only if it was missing and we found it)
    if code and (PROP_STOCK_CODE in props_config):
        prop_type = props_config[PROP_STOCK_CODE].get("type")
        if prop_type == "rich_text":
            update_props[PROP_STOCK_CODE] = {"rich_text": [{"text": {"content": code}}]}
        elif prop_type == "title":
            update_props[PROP_STOCK_CODE] = {"title": [{"text": {"content": code}}]}

    if not update_props:
        print(f"Nothing to update for page {page_id}")
        return

    data = {"properties": update_props}
    response = requests.patch(url, headers=HEADERS, json=data)
    if response.status_code == 200:
        print(f"Updated page {page_id} with price {price}")
    else:
        print(f"Failed to update page {page_id}: {response.text}")

def main():
    if not NOTION_TOKEN or not DATABASE_ID:
        print("Error: NOTION_TOKEN or DATABASE_ID not found in environment variables.")
        return

    # 1. Fetch database schema to see which properties exist
    db_url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
    db_resp = requests.get(db_url, headers=HEADERS)
    if db_resp.status_code != 200:
        print(f"Error fetching database info: {db_resp.text}")
        return
    props_config = db_resp.json().get("properties", {})

    print("Fetching entries from Notion...")
    entries = fetch_notion_stocks()
    print(f"Found {len(entries)} entries.")

    for entry in entries:
        code = entry["code"]
        name = entry["name"]
        new_code_found = None

        if not code:
            if name:
                print(f"Searching code for name: {name}...")
                code = get_stock_code_by_name(name)
                if code:
                    print(f"Found code {code} for {name}")
                    new_code_found = code
                else:
                    print(f"Could not find code for {name}")
                    continue
            else:
                print(f"Skipping entry {entry['page_id']} (no code and no name)")
                continue

        print(f"Fetching price for {code}...")
        price = get_stock_price_sina(code)
        if price:
            update_notion_page(entry["page_id"], price, props_config, code=new_code_found)
        else:
            print(f"Could not get price for {code}")

if __name__ == "__main__":
    main()
