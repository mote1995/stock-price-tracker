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

# Global Property Map (will be populated during verification)
PROP_MAP = {}

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def fuzzy_map_properties(props):
    """Dynamically map properties based on synonyms and whitespace cleaning."""
    mapping_rules = {
        "NAME": ["Investment", "Name", "名称", "投资项目", "股票名称"],
        "PRICE": ["Current Price", "Price", "价格", "现价", "当前价格"],
        "CODE": ["StockCode", "代码", "Code", "股票代码", "Security Code"],
        "UPDATE": ["UpdateAt", "Updated", "时间", "更新时间", "Last Updated"]
    }
    
    found_map = {}
    actual_keys = {k.strip().lower(): k for k in props.keys()}
    
    print("\n--- Fuzzy Property Mapping Results ---")
    for key, synonyms in mapping_rules.items():
        found = False
        for syn in synonyms:
            if syn.lower() in actual_keys:
                real_key = actual_keys[syn.lower()]
                found_map[key] = real_key
                print(f"  Matched {key:6} -> '{real_key}'")
                found = True
                break
        if not found:
            # Fallback for NAME: look for ANY 'title' type
            if key == "NAME":
                for k, v in props.items():
                    if v.get("type") == "title":
                        found_map[key] = k
                        print(f"  Matched {key:6} -> '{k}' (Auto-detected Title column)")
                        found = True
                        break
            if not found:
                print(f"  Warning: Could not find a match for '{key}'")

    return found_map

def get_stock_code_by_name(name):
    """Search for stock code using Sina suggest API."""
    try:
        url = f"http://suggest3.sinajs.cn/suggest/type=&key={name}"
        response = requests.get(url, timeout=10)
        content = response.content.decode('gbk')
        match = re.search(r'"([^"]+)"', content)
        if match:
            line = match.group(1)
            items = line.split(';')
            if items:
                first_item = items[0].split(',')
                if len(first_item) > 3:
                    code = first_item[3]
                    print(f"  [Search] Found code '{code}' for '{name}'")
                    return code
    except Exception as e:
        print(f"  [Search] Error: {e}")
    return None

def get_stock_price_sina(code):
    """Fetch stock price from Sina Finance API."""
    try:
        url = f"http://hq.sinajs.cn/list={code.lower()}"
        headers = {'Referer': 'http://finance.sina.com.cn'}
        response = requests.get(url, headers=headers, timeout=10)
        content = response.content.decode('gbk')
        data = content.split('"')[1].split(',')
        if len(data) > 3:
            price = float(data[3])
            print(f"    [API] Price: {price}")
            return price
    except Exception as e:
        print(f"    [API] Error for {code}: {e}")
    return None

def fetch_notion_stocks():
    """Query Notion database for all stocks using dynamic property names."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    try:
        response = requests.post(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            print(f"  [Fetch] Error: {response.text}")
            return []
        
        results = response.json().get("results", [])
        stocks = []
        name_key = PROP_MAP.get("NAME")
        code_key = PROP_MAP.get("CODE")

        for page in results:
            props = page.get("properties", {})
            
            # Extract Code
            code_content = ""
            if code_key and code_key in props:
                p = props[code_key]
                p_type = p.get("type")
                if p_type == "title":
                    code_content = "".join([t["plain_text"] for t in p.get("title", [])])
                elif p_type in ["rich_text", "text"]:
                    code_content = "".join([t["plain_text"] for t in p.get("rich_text", [])])

            # Extract Name
            name_content = ""
            if name_key and name_key in props:
                p = props[name_key]
                p_type = p.get("type")
                if p_type == "title":
                    name_content = "".join([t["plain_text"] for t in p.get("title", [])])
                elif p_type in ["rich_text", "text"]:
                    name_content = "".join([t["plain_text"] for t in p.get("rich_text", [])])
                
            stocks.append({
                "page_id": page["id"],
                "code": code_content.strip(),
                "name": name_content.strip()
            })
        return stocks
    except Exception as e:
        print(f"  [Fetch] Exception: {e}")
        return []

def update_notion_page(page_id, price, code=None):
    """Update properties using dynamic map."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    now = datetime.datetime.now().isoformat()
    
    update_props = {}
    if "PRICE" in PROP_MAP:
        update_props[PROP_MAP["PRICE"]] = {"number": price}
    
    if "UPDATE" in PROP_MAP:
        update_props[PROP_MAP["UPDATE"]] = {"date": {"start": now}}
    
    if code and "CODE" in PROP_MAP:
        update_props[PROP_MAP["CODE"]] = {"rich_text": [{"text": {"content": code}}]}

    try:
        response = requests.patch(url, headers=HEADERS, json={"properties": update_props}, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

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
    except Exception as e:
        print(f"Search error: {e}")
    return None, None

def verify_database():
    global DATABASE_ID, PROP_MAP
    print(f"1. Verifying Database Connection...")
    
    db_props = {}
    if DATABASE_ID.upper() == "AUTO":
        new_id, props = find_database_by_name("Investments")
        if not new_id: return False
        DATABASE_ID, db_props = new_id, props
    else:
        url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
        response = requests.get(url, headers=HEADERS)
        if response.status_code != 200:
            print("Direct access failed. Trying auto-search...")
            new_id, props = find_database_by_name("Investments")
            if not new_id: return False
            DATABASE_ID, db_props = new_id, props
        else:
            db_props = response.json().get("properties", {})

    PROP_MAP = fuzzy_map_properties(db_props)
    if "PRICE" not in PROP_MAP:
        print("CRITICAL: Price column not found.")
        return False
    return True

def main():
    print("--- Starting Notion Update Script ---")
    if not NOTION_TOKEN: return
    
    if not verify_database():
        print("Exiting due to verification failure.")
        return

    print("2. Fetching records from Notion...")
    entries = fetch_notion_stocks()
    print(f"Found {len(entries)} records.")

    success_count = 0
    for i, entry in enumerate(entries):
        name, code = entry["name"], entry["code"]
        print(f"[{i+1}/{len(entries)}] Processing '{name or 'Untitled'}' (Code: {code or 'SEARCHING'})...")
        
        actual_code = code
        new_code_to_save = None
        
        if not actual_code:
            if name:
                actual_code = get_stock_code_by_name(name)
                if actual_code: new_code_to_save = actual_code
            else: continue

        if actual_code:
            price = get_stock_price_sina(actual_code)
            if price is not None:
                if update_notion_page(entry["page_id"], price, code=new_code_to_save):
                    success_count += 1

    print(f"\n--- Script Finished: {success_count} records updated ---")

if __name__ == "__main__":
    main()
