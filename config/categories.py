"""
Category definitions for dynamic product targeting.
Each entry maps a site name to the specific URL path (slug) for that category.
"""

CATEGORIES: list[dict] = [
    {
        "name": "TV",        
        "paths": {
            "vatan": "/televizyon",          
            "hepsiburada": "/led-tv-televizyonlar-c-163192",
            "mediamarkt": "/tr/category/tv-goruntu-ve-ses-678536.html"
        }
    },
    {
        "name": "Printer",
        "paths": {
            "vatan": "/arama/yazici",
            "mediamarkt": "/tr/category/yazici-tarayici-797535.html",
            "hepsiburada": "/yazicilar-c-3013118"
        }
    },
    {
        "name": "Beyaz Esya",
        "paths": {
            "vatan": "/mutfak-urunleri",
            "hepsiburada": "/beyaz-esya-ankastreler-c-235604",
            "mediamarkt": "/tr/category/beyaz-esya-465707.html"
        }
    },
    {
        "name": "Bluetooth Kulaklik",
        "paths": {
            "vatan": "/bluetooth-kulaklik",
            "hepsiburada": "/bluetooth-kulakliklar-c-16218",
            "mediamarkt": "/tr/category/bluetooth-kulakliklar-795539.html"
        }
    }
]
