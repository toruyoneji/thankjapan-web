from decimal import Decimal

# PayPal (Web) premium subscription pricing, adjusted per purchasing-power
# tier so the $5.00/month default isn't out of reach in lower-income markets.
# Detection is based on Cloudflare's CF-IPCountry header, which this app is
# already behind (see adapter.py for the existing use of the same header).
# These tiers mirror the per-country prices set on the Google Play Billing
# side, so the two payment paths stay roughly aligned.
DEFAULT_PRICE = Decimal('5.00')

TIER_PRICES = {
    'A': Decimal('1.75'),
    'B': Decimal('1.25'),
    'C': Decimal('2.50'),
}

COUNTRY_TIERS = {
    # Tier A
    'ID': 'A', 'PH': 'A', 'VN': 'A', 'EG': 'A',
    'MA': 'A', 'MN': 'A', 'PY': 'A', 'BO': 'A',
    # Tier B
    'NG': 'B', 'PK': 'B', 'BD': 'B', 'TZ': 'B',
    'KE': 'B', 'GH': 'B', 'LK': 'B', 'CI': 'B',
    'SN': 'B', 'CM': 'B',
    # Tier C
    'ZA': 'C', 'IN': 'C',
}


def get_premium_price(request):
    """
    PayPal premium price to show/charge this request's user, based on the
    Cloudflare-detected country. Falls back to the default USD price when
    the country can't be determined (e.g. a request that bypasses
    Cloudflare, such as local development) or isn't in a discounted tier.

    Returns a dict meant to be merged straight into a template context:
    price_usd (e.g. "1.75"), price_display (e.g. "$1.75"), price_tier,
    detected_country.
    """
    country_code = (request.META.get('HTTP_CF_IPCOUNTRY') or '').upper() or None
    tier = COUNTRY_TIERS.get(country_code)
    price = TIER_PRICES[tier] if tier else DEFAULT_PRICE
    price_usd = f'{price:.2f}'

    return {
        'price_usd': price_usd,
        'price_display': f'${price_usd}',
        'price_tier': tier,
        'detected_country': country_code,
    }
