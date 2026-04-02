import pytest
from scrapers.mediamarkt import MediaMarktScraper

def test_parse_price_standard_turkish():
    assert MediaMarktScraper._parse_price("14.599,00 TL") == 14599.0
    assert MediaMarktScraper._parse_price("14.599,50") == 14599.5
    assert MediaMarktScraper._parse_price("2.589,–") == 2589.0

def test_parse_price_thousand_separator_only():
    assert MediaMarktScraper._parse_price("7.150") == 7150.0
    assert MediaMarktScraper._parse_price("46.979") == 46979.0
    assert MediaMarktScraper._parse_price("2.589") == 2589.0

def test_parse_price_no_thousands():
    assert MediaMarktScraper._parse_price("869") == 869.0
    assert MediaMarktScraper._parse_price("869,–") == 869.0
    assert MediaMarktScraper._parse_price("₺869,–") == 869.0

def test_parse_price_decimals():
    assert MediaMarktScraper._parse_price("7.15") == 7.15
    assert MediaMarktScraper._parse_price("10.99") == 10.99

def test_parse_price_with_currency():
    assert MediaMarktScraper._parse_price("₺ 46.979,00") == 46979.0
    assert MediaMarktScraper._parse_price("₺69.999,–") == 69999.0

def test_parse_price_invalid():
    assert MediaMarktScraper._parse_price("Tükendi") is None
    assert MediaMarktScraper._parse_price("") is None
