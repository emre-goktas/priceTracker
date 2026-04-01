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
            "teknosa": "/arama/?text=ram"
        }
    },
    {
        "name": "gpu",
        "keywords": ["RTX 40", "RTX 50", "RX 7000", "Ekran Kartı"],
        "paths": {
            "vatan": "/arama/ekran-karti",
            "amazon": "/s?k=ekran+kartı",
        }
    },
    {
        "name": "phone",
        "paths": {
            "vatan": "/cep-telefonu-modelleri",
        }
    }
]
