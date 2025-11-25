import requests
from bs4 import BeautifulSoup

UNLOCKER_USER = "brd-customer-hl_af9d374d-zone-web_unlocker"
UNLOCKER_PASS = "41lvtwmav2y8"
UNLOCKER_HOST = "brd.superproxy.io"
UNLOCKER_PORT = "33335"

WEB_UNLOCKER_PROXY = {
    'http': f'http://{UNLOCKER_USER}:{UNLOCKER_PASS}@{UNLOCKER_HOST}:{UNLOCKER_PORT}',
    'https': f'http://{UNLOCKER_USER}:{UNLOCKER_PASS}@{UNLOCKER_HOST}:{UNLOCKER_PORT}'
}

def test_unlocker():
    url = "https://energy-ua.info/cherga/2-1"
    
    print("=" * 60)
    print("Web Unlocker Test")
    print("=" * 60)
    
    try:
        response = requests.get(url, proxies=WEB_UNLOCKER_PROXY, timeout=60)
        
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            container = soup.find('div', class_='periods_items')
            
            if container:
                print("SUCCESS! Data received!")
                items = container.find_all('span')
                for item in items[:5]:
                    print(f"  {item.get_text(strip=True)}")
            else:
                print("No data container found")
        else:
            print(f"Error: {response.status_code}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_unlocker()