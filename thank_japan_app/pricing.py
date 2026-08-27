import ipaddress
from decimal import Decimal

# PayPal (Web) premium subscription pricing, adjusted per purchasing-power
# tier so the $5.00/month default isn't out of reach in lower-income markets.
# Detection is based on Cloudflare's CF-IPCountry header, which this app is
# already behind (see adapter.py for the existing use of the same header).
# These tiers mirror the per-country prices set on the Google Play Billing
# side, so the two payment paths stay roughly aligned.
DEFAULT_PRICE = Decimal('5.00')

# Cloudflare only sets CF-IPCountry accurately when a request actually comes
# through its edge. Requests that reach the Heroku app directly (bypassing
# Cloudflare) can set that header to anything, so it must only be trusted
# when the request's real connecting IP is one of Cloudflare's own — source:
# https://www.cloudflare.com/ips-v4/ and https://www.cloudflare.com/ips-v6/
# (ranges rarely change, but re-check periodically).
CLOUDFLARE_IP_RANGES = [ipaddress.ip_network(cidr) for cidr in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32',
    '2405:8100::/32', '2a06:98c0::/29', '2c0f:f248::/32',
)]


def _connecting_ip(request):
    """
    The IP that actually opened the TCP connection to this app, as Heroku's
    router sees it. Heroku appends that IP to the end of X-Forwarded-For
    before forwarding to the dyno, so the last entry is the one hop we can
    trust — every entry before it is client-supplied and freely spoofable.
    Falls back to REMOTE_ADDR for local/non-Heroku environments.
    """
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '')


def _request_is_from_cloudflare(request):
    try:
        ip = ipaddress.ip_address(_connecting_ip(request))
    except ValueError:
        return False
    return any(ip in network for network in CLOUDFLARE_IP_RANGES)


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
    country_code = None
    if _request_is_from_cloudflare(request):
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
