# check_alerts.py
import requests

URL = "https://alerts.org.ua/poltavska-oblast/"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

try:
    resp = requests.get(URL, headers=headers, timeout=15)
    print("Status code:", resp.status_code)
    print("Final URL:", resp.url)
except Exception as e:
    print("Error:", e)
