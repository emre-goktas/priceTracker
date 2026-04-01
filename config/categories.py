"""
Category definitions for dynamic product targeting.
Each entry maps a site name to the specific URL path (slug) for that category.
"""

CATEGORIES: list[dict] = [
    {
        "name": "ram",
        "keywords": ["DDR4", "DDR5", "16GB", "32GB"],
        "paths": {
            "vatan": "/arama/ram",
            "amazon": "/s?k=ram",
            "teknosa": "/arama/?text=ram",
            "hepsiburada": "/bellek-ramler-c-47",
            "mediamarkt": "/tr/search.html?query=ram"
        }
    },
    {
        "name": "gpu",
        "keywords": ["RTX 40", "RTX 50", "RX 7000", "Ekran Kartı"],
        "paths": {
            "vatan": "/arama/ekran-karti",
            "amazon": "/s?k=ekran+kartı",
            "hepsiburada": "/ara?q=ekran+karti"
        }
    },
    {
        "name": "phone",
        "paths": {
            "vatan": "/cep-telefonu-modelleri",
            "hepsiburada": "/cep-telefonlari-c-371965",
            "mediamarkt": "/tr/category/_ak%C4%B1ll%C4%B1-telefonlar-504171.html"
        }
    },
    {
        "name": "toys",
        "paths": {
            "vatan": "/playstation",
            "hepsiburada": "/oyuncaklar-c-23031884"
        }
    }
]
