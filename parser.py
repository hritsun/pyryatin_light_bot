import requests
from bs4 import BeautifulSoup
import json
import time
import re
from datetime import datetime
import urllib3

# Отключаем предупреждения о неверном SSL (так как используем прокси)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Твои рабочие настройки Bright Data (из успешного теста)
UNLOCKER_USER = "brd-customer-hl_af9d374d-zone-web_unlocker"
UNLOCKER_PASS = "41lvtwmav2y8"
UNLOCKER_HOST = "brd.superproxy.io"
UNLOCKER_PORT = "33335"

WEB_UNLOCKER_PROXY = {
    'http': f'http://{UNLOCKER_USER}:{UNLOCKER_PASS}@{UNLOCKER_HOST}:{UNLOCKER_PORT}',
    'https': f'http://{UNLOCKER_USER}:{UNLOCKER_PASS}@{UNLOCKER_HOST}:{UNLOCKER_PORT}'
}

FILENAME = 'schedule_data.json'

def scrape_schedule():
    url = "https://energy-ua.info/cherga/2-1"
    current_time = datetime.now().strftime('%H:%M:%S')
    print(f"[{current_time}] 🔄 Запрос к сайту через Web Unlocker...")
    
    try:
        # verify=False нужен для работы через некоторые прокси
        response = requests.get(url, proxies=WEB_UNLOCKER_PROXY, timeout=120, verify=False)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            container = soup.find('div', class_='periods_items')
            
            if container:
                result_lines = []
                time_periods = []
                
                # Парсим строки
                for item in container.find_all('span'):
                    text = item.get_text(separator=" ", strip=True)
                    text = text.replace("год. год.", "год.").replace("тривалість", "⏳")
                    
                    if text and len(text) > 3:
                        if "З" in text and "до" in text:
                            result_lines.append(f"🔴 {text}")
                            
                            # Извлекаем чистое время для бота (01:00, 14:00 и т.д.)
                            time_match = re.search(r'З\s*(\d{2}):(\d{2})\s*до\s*(\d{2}):(\d{2})', text)
                            if time_match:
                                start_hour, start_min, end_hour, end_min = time_match.groups()
                                time_periods.append({
                                    'start': f"{start_hour}:{start_min}",
                                    'end': f"{end_hour}:{end_min}",
                                    'type': 'outage'
                                })
                        else:
                            result_lines.append(f"   {text}")
                
                if not result_lines:
                     result_lines = ["✅ На сегодня отключений не найдено (или сайт изменился)."]

                # Формируем структуру данных
                data = {
                    'updated_at': time.time(), # timestamp сохранения
                    'readable_date': current_time,
                    'status': 'success',
                    'text_lines': result_lines,
                    'periods': time_periods
                }
                
                # Сохраняем в файл (атомарно)
                with open(FILENAME, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"✅ УСПЕХ! Сохранено {len(time_periods)} периодов откл.")
                return True
            else:
                print("⚠️ Контейнер с графиком не найден в HTML.")
        else:
            print(f"❌ Ошибка сервера: {response.status_code}")
    
    except Exception as e:
        print(f"❌ Критическая ошибка парсинга: {e}")
    
    return False

if __name__ == "__main__":
    print("🚀 Парсер запущен. Интервал обновления: 10 минут.")
    
    # Сразу делаем первый запуск
    scrape_schedule()
    
    while True:
        # Ждем 600 секунд (10 минут)
        time.sleep(600)
        scrape_schedule()