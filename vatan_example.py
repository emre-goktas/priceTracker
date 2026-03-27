import asyncio
import sqlite3
import random
import re
import json
import logging
from typing import List, Optional
from datetime import datetime
import aiohttp
import ollama

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Ayarlar
DB_FILE = "vatan_bot.db"
TARGET_URLS = [
    "https://www.vatanbilgisayar.com/arama/ram", # RAM kategorisi, en pahalıdan ucuza (örnek)
    # Buraya baska kategoriler de eklenebilir
]
DISCOUNT_THRESHOLD = 0.30  # %30 indirim hedefi
TELEGRAM_BOT_TOKEN = "8602871835:AAESMg7cLcD0Qp5UgmDLpwCaKWQXIlUdVMg"
TELEGRAM_CHAT_ID = "7673353516"
OLLAMA_MODEL = "qwen3:14b "  # veya llama3, bilgisayarınızda hangisi yüklüyse

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Veritabanı İşlemleri ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            name TEXT,
            url TEXT,
            lowest_price REAL,
            last_price REAL,
            last_checked TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def update_product_db(product_id: str, name: str, url: str, current_price: float) -> Optional[float]:
    """
    Ürünü DB'ye kaydeder veya günceller.
    Eğer ürün zaten varsa ve fiyatı (lowest_price) değerinden belli bir % düşükse,
    eski en düşük fiyatı döndürür (alarm için). Yoksa None döner.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("SELECT lowest_price, last_price FROM products WHERE product_id = ?", (product_id,))
    row = cursor.fetchone()
    
    old_lowest_price = None
    discount_detected = False

    if row:
        lowest_price = row[0]
        # Eğer yeni fiyat, tarihteki en düşük fiyatın (threshold) kadar altındaysa alarm ver.
        # Ya da basitçe: son fiyattan %30 düştüyse. Biz "tarihi en düşük fiyata göre" absürt bir indirim arıyoruz.
        if current_price < (lowest_price * (1 - DISCOUNT_THRESHOLD)):
            discount_detected = True
            old_lowest_price = lowest_price
            
        new_lowest = min(lowest_price, current_price)
        cursor.execute('''
            UPDATE products 
            SET last_price = ?, lowest_price = ?, last_checked = ? 
            WHERE product_id = ?
        ''', (current_price, new_lowest, datetime.now(), product_id))
    else:
        cursor.execute('''
            INSERT INTO products (product_id, name, url, lowest_price, last_price, last_checked)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (product_id, name, url, current_price, current_price, datetime.now()))
        
    conn.commit()
    conn.close()
    
    if discount_detected:
        return old_lowest_price
    return None

# --- Telegram Bildirimi ---
async def send_telegram_alert(name: str, url: str, old_price: float, new_price: float):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "BURAYA_TELEGRAM_BOT_TOKEN_YAZIN":
        logging.warning("Telegram Bot Token girilmediği için bildirim gönderilemedi.")
        return
        
    discount_percent = int(((old_price - new_price) / old_price) * 100)
    message = (
        f"🚨 **ABSÜRT İNDİRİM YAKALANDI!** 🚨\n\n"
        f"📦 {name}\n"
        f"📉 Eski Fiyat: {old_price} TL\n"
        f"🔥 Yeni Fiyat: {new_price} TL\n"
        f"🔻 İndirim: %{discount_percent}\n\n"
        f"🔗 [Hemen Satın Al]({url})"
    )
    
    url_req = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url_req, json=payload) as response:
            if response.status != 200:
                logging.error(f"Telegram mesajı gönderilemedi: {await response.text()}")
            else:
                logging.info(f"Telegram alarmı başarıyla gönderildi: {name}")

# --- AI Destekli Fiyat Çıkarımı (Yedek Plan) ---
async def extract_price_with_ai(html_snippet: str) -> Optional[float]:
    """
    Eğer HTML yapısı bozulmuşsa (örneğin fiyat etiketi class'ı değiştiyse),
    HTML'in bir parçasını yerel Ollama modeline verip fiyatı çekmesini isteriz.
    """
    prompt = f"""
    Sen bir veri çıkarma asistanısın. Aşağıdaki HTML kodu bir ürün sayfasına ait. 
    Bu ürünün TL cinsinden fiyatını bul.
    SADECE RAKAMI (nokta veya virgül kullanmadan, düz integer veya float olarak, örn: 15499) döndür. 
    Açıklama veya başka bir kelime yazma.
    
    HTML:
    {html_snippet}
    """
    
    try:
        logging.info("Ollama AI modeli ile fiyat tespiti deneniyor...")
        # ollama kütüphanesi senkron çalışır, bu yüzden event loop'u bloklamamak için
        # küçük veri setlerinde doğrudan çağırıyoruz (prototip için kabul edilebilir).
        # Gerçek ortamda asyncio.to_thread kullanılabilir.
        response = await asyncio.to_thread(
            ollama.chat,
            model=OLLAMA_MODEL, 
            messages=[{'role': 'user', 'content': prompt}]
        )
        result_text = response['message']['content'].strip()
        # Sadece rakamları ve noktayı temizle
        clean_price_str = re.sub(r'[^\d.]', '', result_text.replace(',', '.'))
        return float(clean_price_str)
    except Exception as e:
        logging.error(f"AI fiyat çıkarmada hata: {e}")
        return None

# --- Web Scraping ---
async def scrape_vatan(page, url):
    logging.info(f"Taranıyor: {url}")
    await page.goto(url, wait_until="networkidle", timeout=60000)
    
    # İnsan benzeri davranış: Aşağı kaydır
    await page.evaluate("window.scrollBy(0, 500)")
    await asyncio.sleep(random.uniform(1.5, 3.5))
    await page.evaluate("window.scrollBy(0, 1000)")
    await asyncio.sleep(random.uniform(1.0, 2.5))
    
    # Ürün kartlarını bul (Vatan bilgisayarın yapısı arama ve kategorilerde farklılık gösterebilir)
    # Arama sonuçları için 'a.product-list-link', kategoriler için '.product-list__item' kullanılır.
    # Her ikisini de desteklemek için virgüllü (OR) seçici kullanıyoruz.
    product_elements = await page.locator('.product-list__item, a.product-list-link').all()
    
    if not product_elements:
        logging.warning("Ürün kartları bulunamadı. Sayfanın yüklenmesi bekleniyor...")
        try:
            await page.wait_for_selector('.product-list__item, a.product-list-link', timeout=10000)
            product_elements = await page.locator('.product-list__item, a.product-list-link').all()
        except:
            logging.error("Zaman aşımı: Ürünler yüklenemedi. Site yapısı değişmiş olabilir.")
            return

    for el in product_elements:
        try:
            # İsmi h3 içinden veya .product-list__product-name class'ından al
            name_el = el.locator('.product-list__product-name, h3').first
            # Fiyatı .product-list__price veya .product-list__price-number içinden al
            price_el = el.locator('.product-list__price, .product-list__price-number').first
            
            # Linki elemanın kendisinden (eğer <a> ise) veya içindeki linkten al
            if await el.evaluate("node => node.tagName === 'A'"):
                url_suffix = await el.get_attribute('href')
            else:
                link_el = el.locator('a.product-list__link, a.product-list-link').first
                url_suffix = await link_el.get_attribute('href') if await link_el.count() else ""
            
            if not await name_el.count():
                continue
                
            name = (await name_el.inner_text()).strip()
            price_text = (await price_el.inner_text()).strip() if await price_el.count() else ""
            
            # Fiyatı temizle (Örn: "14.599 TL" -> 14599.0)
            clean_price = None
            if price_text:
                price_str = re.sub(r'[^\d]', '', price_text)
                if price_str:
                    clean_price = float(price_str)
                    
            full_url = url_suffix
            if url_suffix and url_suffix.startswith('/'):
                full_url = f"https://www.vatanbilgisayar.com{url_suffix}"
            
            # Benzersiz ID olarak URL'in son kısmını (slug) kullanabiliriz
            product_id = full_url.split('/')[-2] if full_url.endswith('/') else full_url.split('/')[-1]
            
            # --- AI FALLBACK ---
            if not clean_price:
                # Fiyat geleneksel yollarla bulunamadıysa AI'a gönder (Prototip gereği)
                html_content = await el.inner_html()
                clean_price = await extract_price_with_ai(html_content)
                
            if clean_price and product_id:
                logging.info(f"Ürün okundu: {name} - {clean_price} TL")
                old_lowest = update_product_db(product_id, name, full_url, clean_price)
                
                if old_lowest:
                    logging.critical(f"İNDİRİM ALARMI: {name} | Eski: {old_lowest} Yeni: {clean_price}")
                    await send_telegram_alert(name, full_url, old_lowest, clean_price)
                    
        except Exception as e:
            logging.error(f"Ürün işlenirken hata: {e}")

async def main():
    init_db()
    
    # Prototip için ücretsiz bir proxy kullanmak isterseniz buraya ekleyebilirsiniz.
    # Ancak ücretsiz proxy'ler çok dengesiz olduğu için lokal IP'nizle test etmeniz önerilir.
    # proxy_server = "http://ipadresi:port"
    
    async with async_playwright() as p:
        # Banlanmamak için headless=False (Tarayıcıyı göster) kullanmak bazen işe yarar.
        # Tam gizlilik için args içindeki parametreleri kullanıyoruz.
        browser = await p.chromium.launch(
            headless=True, # Sunucuda çalışacaksa True olmalı
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        
        # Playwright-stealth eklentisini sayfaya uygula (Bot korumalarını atlatmak için)
        await Stealth().apply_stealth_async(page)
        
        while True:
            for url in TARGET_URLS:
                await scrape_vatan(page, url)
                # Sayfalar arası bekleme (rastgele 10-20 saniye)
                await asyncio.sleep(random.uniform(10, 20))
                
            logging.info("Bir döngü tamamlandı. 15 dakika bekleniyor...")
            # Kategoriler arası uzun bekleme (banlanmamak için çok önemli)
            await asyncio.sleep(30)
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
