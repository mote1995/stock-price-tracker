import os
import requests
import datetime

# Notion Configuration from Environment Variables
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("DATABASE_ID")

# Property Names in Notion (Customize these if needed)
PROP_STOCK_CODE = "StockCode"
PROP_PRICE = "Price"
PROP_UPDATE_AT = "UpdateAt"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

def get_stock_price_sina(code):
    """
    Fetch stock price from Sina Finance API.
    code format: sh600000, sz000001
    """
    try:
        url = f"http://hq.sinajs.cn/list={code.lower()}"
        # Sina API requires specific referer
        headers = {'Referer': 'http://finance.sina.com.cn'}
        response = requests.get(url, headers=headers)
        # Handle encoding (GBK)
        content = response.content.decode('gbk')
        
        # Parse: var hq_str_sh600000="浦发银行,10.00,10.01,..."
        data = content.split('"')[1].split(',')
        if len(data) > 3:
            return float(data[3]) # data[3] is current price
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
        
        # Get Stock Code (support both Rich Text and Title)
        code_prop = props.get(PROP_STOCK_CODE, {})
        code_type = code_prop.get("type")
        code_content = ""
        
        if code_type == "title":
            code_content = "".join([t["plain_text"] for t in code_prop.get("title", [])])
        elif code_type == "rich_text":
            code_content = "".join([t["plain_text"] for t in code_prop.get("rich_text", [])])
            
        if code_content:
            stocks.append({
                "page_id": page["id"],
                "code": code_content.strip()
            })
    return stocks

def update_notion_price(page_id, price):
    """
    Update the price and update time in Notion.
    """
    url = f"https://api.notion.com/v1/pages/{page_id}"
    now = datetime.datetime.now().isoformat()
    
    data = {
        "properties": {
            PROP_PRICE: {"number": price},
            PROP_UPDATE_AT: {"date": {"start": now}}
        }
    }
    
    response = requests.patch(url, headers=HEADERS, json=data)
    if response.status_code == 200:
        print(f"Updated page {page_id} with price {price}")
    else:
        print(f"Failed to update page {page_id}: {response.text}")

def main():
    if not NOTION_TOKEN or not DATABASE_ID:
        print("Error: NOTION_TOKEN or DATABASE_ID not found in environment variables.")
        return

    print("Fetching stocks from Notion...")
    stocks = fetch_notion_stocks()
    print(f"Found {len(stocks)} stocks.")

    for stock in stocks:
        code = stock["code"]
        print(f"Processing {code}...")
        price = get_stock_price_sina(code)
        if price:
            update_notion_price(stock["page_id"], price)
        else:
            print(f"Could not get price for {code}")

if __name__ == "__main__":
    main()
