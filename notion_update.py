import os
import requests
import datetime
import re
import json

# Notion Configuration from Environment Variables (Aggressively Cleaned)
def clean_env_var(name):
    val = os.environ.get(name, "").strip()
    val = re.sub(rf"^{name}:\s*", "", val, flags=re.IGNORECASE)
    val = re.sub(rf"^Secret:\s*", "", val, flags=re.IGNORECASE)
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
        "UPDATE": ["UpdateAt", "Updated", "时间", "更新时间", "Last Updated"],
        "BUY_PRICE": ["Buy Price", "买入价", "成本价", "Cost Price"],
        "QUANTITY": ["Quantity", "数量", "持仓量"],
        "ROI_DETAILS": ["ROI Details", "盈亏详情", "状态", "ROI Detail"],
        "EXCHANGE_RATE": ["Exchange Rate", "汇率", "Rate"]
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
                print(f"  Matched {key:13} -> '{real_key}'")
                found = True
                break
        if not found:
            if key == "NAME":
                for k, v in props.items():
                    if v.get("type") == "title":
                        found_map[key] = k
                        print(f"  Matched {key:13} -> '{k}' (Title fallback)")
                        found = True
                        break
            if not found:
                print(f"  Warning: No match found for '{key}'")

    return found_map

def get_stock_code_by_name(name):
    """Search for stock code using Sina suggest API with A/H disambiguation."""
    print(f"  [Search] Looking for: '{name}'")
    try:
        # Determine hints from name
        is_h_hint = "H" in name.upper()
        is_a_hint = "A" in name.upper()
        # Remove trailing A/H for search but keep internal ones
        clean_name = re.sub(r'[A|H]$', '', name, flags=re.IGNORECASE).strip() 

        url = f"http://suggest3.sinajs.cn/suggest/type=&key={clean_name}"
        response = requests.get(url, timeout=10)
        content = response.content.decode('gbk')
        match = re.search(r'"([^"]+)"', content)
        if match:
            line = match.group(1)
            items = line.split(';')
            results = []
            for item in items:
                parts = item.split(',')
                if len(parts) > 3:
                    results.append({"name": parts[0], "code": parts[3]})

            if not results:
                print(f"  [Search] No results found for '{clean_name}'")
                return None

            # Filter for meaningful matches
            filtered = []
            for r in results:
                # If H hint, must be hk
                if is_h_hint and not r["code"].lower().startswith("hk"): continue
                # If A hint, must be sh/sz
                if is_a_hint and not r["code"].lower().startswith(("sh", "sz")): continue
                # Generally prefer sh/sz/hk over US/other
                if not (is_h_hint or is_a_hint) and not r["code"].lower().startswith(("sh", "sz", "hk")): continue
                filtered.append(r)

            if not filtered: filtered = results 

            # Pick the best match by name similarity
            final_code = None
            for f in filtered:
                if clean_name in f["name"]:
                    final_code = f["code"]
                    break
            
            if not final_code: final_code = filtered[0]["code"]
            
            print(f"  [Search] Result: {final_code}")
            return final_code
            
    except Exception as e:
        print(f"  [Search] Error for {name}: {e}")
    return None

def get_hkd_cny_rate():
    """Fetch real-time HKD to CNY exchange rate from Sina."""
    try:
        url = "http://hq.sinajs.cn/list=fx_shkdcny"
        headers = {'Referer': 'http://finance.sina.com.cn'}
        response = requests.get(url, headers=headers, timeout=10)
        content = response.content.decode('gbk')
        match = re.search(r'"([^"]+)"', content)
        if match:
            data = match.group(1).split(',')
            rate = float(data[1])
            print(f"  [FX] Current HKD/CNY Rate: {rate}")
            return rate
    except Exception as e:
        print(f"  [FX] Error: {e}")
    return 0.915

def get_stock_price_sina(code):
    """Fetch stock price from Sina Finance (Supports A and HK shares)."""
    code = code.lower()
    is_hk = code.startswith("hk")
    # Native HK quotes often don't need rt_ prefix
    url = f"http://hq.sinajs.cn/list={code}"
    
    try:
        headers = {'Referer': 'http://finance.sina.com.cn'}
        response = requests.get(url, headers=headers, timeout=10)
        content = response.content.decode('gbk')
        match = re.search(r'"([^"]+)"', content)
        if not match:
            print(f"    [API] No data found for code {code}")
            return None
            
        data = match.group(1).split(',')
        if len(data) < 4:
            print(f"    [API] Invalid response for {code}: {match.group(1)}")
            return None

        if is_hk:
            # For HK, index 6 is the current price
            if len(data) > 6:
                price = float(data[6])
                return price
        else:
            # For A-share, index 3 is current price
            price = float(data[3])
            return price
    except Exception as e:
        print(f"    [API] Error for {code}: {e}")
    return None

def fetch_notion_stocks():
    """Query Notion and extract stock list with cost data."""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    try:
        response = requests.post(url, headers=HEADERS, timeout=15)
        if response.status_code != 200: return []
        results = response.json().get("results", [])
        stocks = []
        name_key, code_key = PROP_MAP.get("NAME"), PROP_MAP.get("CODE")
        buy_key, qty_key = PROP_MAP.get("BUY_PRICE"), PROP_MAP.get("QUANTITY")

        for page in results:
            props = page.get("properties", {})
            
            # Extract basic info
            code, name = "", ""
            if code_key and code_key in props:
                p = props[code_key]
                if p.get("type") == "title": code = "".join([t["plain_text"] for t in p.get("title", [])])
                else: code = "".join([t["plain_text"] for t in p.get("rich_text", [])])

            if name_key and name_key in props:
                p = props[name_key]
                if p.get("type") == "title": name = "".join([t["plain_text"] for t in p.get("title", [])])
                else: name = "".join([t["plain_text"] for t in p.get("rich_text", [])])
            
            # Extract numerical info
            buy_price = props.get(buy_key, {}).get("number", 0) if buy_key else 0
            quantity = props.get(qty_key, {}).get("number", 0) if qty_key else 0
                
            stocks.append({
                "page_id": page["id"],
                "code": code.strip(),
                "name": name.strip(),
                "buy_price": float(buy_price or 0),
                "quantity": float(quantity or 0)
            })
        return stocks
    except Exception: return []

def update_notion_page(page_id, display_price, code=None, buy_price=0, quantity=0, rate=1.0):
    """
    Update Notion page.
    display_price: Native currency price (HKD for HK stocks, CNY for A-shares)
    rate: Exchange rate to CNY
    """
    url = f"https://api.notion.com/v1/pages/{page_id}"
    now = datetime.datetime.now().isoformat()
    update_props = {}
    
    if "PRICE" in PROP_MAP: update_props[PROP_MAP["PRICE"]] = {"number": display_price}
    if "UPDATE" in PROP_MAP: update_props[PROP_MAP["UPDATE"]] = {"date": {"start": now}}
    if "EXCHANGE_RATE" in PROP_MAP: update_props[PROP_MAP["EXCHANGE_RATE"]] = {"number": rate}
    if code and "CODE" in PROP_MAP: update_props[PROP_MAP["CODE"]] = {"rich_text": [{"text": {"content": code}}]}

    # ROI Details and Color logic
    if "ROI_DETAILS" in PROP_MAP and buy_price > 0:
        # Profit calculation: (Native Current - Native Buy) * Quantity * Rate
        profit_per_share_native = display_price - buy_price
        roi_pct = (profit_per_share_native / buy_price) * 100
        profit_total_cny = profit_per_share_native * quantity * rate
        
        sign = "+" if profit_per_share_native >= 0 else ""
        color = "red" if profit_per_share_native >= 0 else "green"
        status_text = f"{sign}{roi_pct:.2f}% ({sign}{profit_total_cny:,.2f} CNY)"
        
        update_props[PROP_MAP["ROI_DETAILS"]] = {
            "rich_text": [{
                "text": {"content": status_text},
                "annotations": {"color": color}
            }]
        }

    try:
        requests.patch(url, headers=HEADERS, json={"properties": update_props}, timeout=10)
    except Exception: pass

def find_database_by_name(target_name="Investments"):
    url = "https://api.notion.com/v1/search"
    payload = {"query": target_name, "filter": {"value": "database", "property": "object"}, "page_size": 5}
    try:
        response = requests.post(url, json=payload, headers=HEADERS)
        if response.status_code == 200:
            for db in response.json().get("results", []):
                title = db["title"][0].get("plain_text", "Untitled") if db.get("title") else "Untitled"
                if title.lower() == target_name.lower(): return db['id'], db.get("properties", {})
    except Exception: pass
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
            new_id, props = find_database_by_name("Investments")
            if not new_id: return False
            DATABASE_ID, db_props = new_id, props
        else: db_props = response.json().get("properties", {})

    PROP_MAP = fuzzy_map_properties(db_props)
    return "PRICE" in PROP_MAP

def main():
    print("--- Starting Notion Update Script ---")
    if not (NOTION_TOKEN and verify_database()): return

    print("2. Processing records...")
    entries = fetch_notion_stocks()
    hkd_rate = None
    success_count = 0

    for i, entry in enumerate(entries):
        name, code = entry["name"], entry["code"]
        print(f"[{i+1}/{len(entries)}] '{name or 'Untitled'}' ({code or 'SEARCHING'})...")
        actual_code = code or get_stock_code_by_name(name)
        if not actual_code: continue

        raw_price = get_stock_price_sina(actual_code)
        if raw_price is not None:
            rate = 1.0
            if actual_code.lower().startswith("hk"):
                if hkd_rate is None: hkd_rate = get_hkd_cny_rate()
                rate = hkd_rate
                print(f"    [HK-Mode] Price: {raw_price} HKD, Rate: {rate}")
            else:
                print(f"    [A-Mode] Price: {raw_price} CNY")

            update_notion_page(entry["page_id"], raw_price, 
                               code=(actual_code if not entry["code"] else None),
                               buy_price=entry["buy_price"], 
                               quantity=entry["quantity"], rate=rate)
            success_count += 1

    print(f"\n--- Finished: {success_count} updated ---")

if __name__ == "__main__":
    main()
