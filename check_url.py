import logging
import requests

logging.basicConfig(level=logging.INFO)

URL = "https://www.poe.pl.ua/disconnection/power-outages/"

def main():
    logging.info("Starting Container")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
    }

    session = requests.Session()
    resp = session.get(URL, headers=headers, timeout=20, allow_redirects=True)

    logging.info(f"Status code: {resp.status_code}")
    logging.info(f"Final URL: {resp.url}")

if __name__ == "__main__":
    main()
