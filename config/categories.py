"""
Category definitions for dynamic product targeting.
Each entry specifies the category name, search keywords, and target sites.
"""

CATEGORIES: list[dict] = [
    {
        "category": "ram",
        "keywords": ["DDR4", "DDR5", "16GB", "32GB"],
        "sites": ["vatan", "amazon", "teknosa"],
    },
    {
        "category": "gpu",
        "keywords": ["RTX 40", "RTX 50", "RX 7000", "Ekran Kartı"],
        "sites": ["vatan", "amazon"],
    },
]
