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
    'KH': 'A', 'MM': 'A',
    # Tier B
    'NG': 'B', 'PK': 'B', 'BD': 'B', 'TZ': 'B',
    'KE': 'B', 'GH': 'B', 'LK': 'B', 'CI': 'B',
    'SN': 'B', 'CM': 'B',
    # Tier C
    'ZA': 'C', 'IN': 'C',
}

# Display-only local-currency prices. The actual PayPal charge is always
# price_usd (USD) - these only change the human-readable price_display, for
# markets where a native-currency figure reads far more naturally than a
# USD one. Amounts are fixed, rounded approximations of the USD tier price
# at roughly the exchange rate in effect when set (checked 2026-08-30), not
# a live/dynamic conversion - revisit if a currency moves a lot.
# Keyed by country code, then by price_tier (None = the untiered default
# price, i.e. DEFAULT_PRICE).
LOCAL_CURRENCY_DISPLAY = {
    'JP': {None: '¥800'},
    # Tier A ($1.75)
    'ID': {'A': 'Rp27.000'},
    'PH': {'A': '₱99'},
    'VN': {'A': '44.000₫'},
    'EG': {'A': 'EGP 88'},
    'MA': {'A': 'MAD 17'},
    'MN': {'A': '₮6,000'},
    'PY': {'A': '₲13,500'},
    'BO': {'A': 'Bs12'},
    'KH': {'A': '៛7,200'},
    'MM': {'A': 'Ks3,700'},
    # Tier B ($1.25)
    'NG': {'B': '₦1,750'},
    'PK': {'B': '₨350'},
    'BD': {'B': '৳150'},
    'TZ': {'B': 'TSh3,250'},
    'KE': {'B': 'KSh160'},
    'GH': {'B': 'GH₵14'},
    'LK': {'B': 'Rs375'},
    'CI': {'B': '750F'},
    'SN': {'B': '750F'},
    'CM': {'B': '750F'},
    # Tier C ($2.50)
    'ZA': {'C': 'R45'},
    'IN': {'C': '₹220'},
}


def detect_cf_country(request):
    """
    The Cloudflare-verified two-letter country code for this request, or
    None if it can't be trusted (didn't come through Cloudflare's edge, or
    Cloudflare didn't set the header). Shared by get_premium_price and
    get_top_page_seo_override - both keyed by the same country signal.
    """
    if not _request_is_from_cloudflare(request):
        return None
    return (request.META.get('HTTP_CF_IPCOUNTRY') or '').upper() or None


def get_premium_price(request):
    """
    PayPal premium price to show/charge this request's user, based on the
    Cloudflare-detected country. Falls back to the default USD price when
    the country can't be determined (e.g. a request that bypasses
    Cloudflare, such as local development) or isn't in a discounted tier.

    Returns a dict meant to be merged straight into a template context:
    price_usd (e.g. "1.75", always what's actually billed to PayPal),
    price_display (e.g. "$1.75", or a local-currency string for countries
    in LOCAL_CURRENCY_DISPLAY - display only), price_tier, detected_country.
    """
    country_code = detect_cf_country(request)
    tier = COUNTRY_TIERS.get(country_code)
    price = TIER_PRICES[tier] if tier else DEFAULT_PRICE
    price_usd = f'{price:.2f}'

    price_display = f'${price_usd}'
    local_display = LOCAL_CURRENCY_DISPLAY.get(country_code)
    if local_display and tier in local_display:
        price_display = local_display[tier]

    return {
        'price_usd': price_usd,
        'price_display': price_display,
        'price_tier': tier,
        'detected_country': country_code,
    }


# Top-page (English, "/") <title>/<meta name="description"> for 28 countries
# that don't have one of the 15 fully-translated locales, per country-of-
# visitor (same CF-IPCountry signal as get_premium_price above, entirely
# independent of it - this never touches COUNTRY_TIERS/pricing). The page
# content itself stays English; only these two SERP-facing tags change, in a
# "{local phrase} (Learn Japanese) | ThankJapan" bilingual format meant to
# raise CTR while making it clear before the click that the page itself is
# in English (avoids a bounce-rate hit from a mismatched-language promise).
# NG/GH/ZA deliberately have no entry (and so fall through to the English
# default below) - English is already the de facto search language there,
# with no single other local language dominant enough to add real CTR value.
#
# Confidence note: id/fil/fr/hi/ar/nl/pl/ru/sv/no/da/el/tr/de are native-
# level-checked. my/km/mn/ur/bn/sw/si (Myanmar/Cambodia/Mongolia/Pakistan/
# Bangladesh/Tanzania+Kenya/Sri Lanka) are best-effort machine-assisted
# translations, not yet native-speaker-verified - flagged for a follow-up
# review pass, per 2026-09-04 agreement with the site owner to ship now and
# revisit if something looks off.
_SEO_TAIL_EN = "Learn Japanese vocabulary through fun, visual quizzes on ThankJapan - free to start!"

TOP_PAGE_SEO_CONTENT = {
    'id': {
        'title': "Belajar Bahasa Jepang (Learn Japanese) | ThankJapan",
        'description': f"Belajar kosakata Jepang lewat kuis visual seru. {_SEO_TAIL_EN}",
    },
    'fil': {
        'title': "Matuto ng Hapon (Learn Japanese) | ThankJapan",
        'description': f"Matuto ng bokabularyo ng Hapon gamit ang masayang visual quiz. {_SEO_TAIL_EN}",
    },
    'fr': {
        'title': "Apprendre le Japonais (Learn Japanese) | ThankJapan",
        'description': f"Apprenez le vocabulaire japonais avec des quiz visuels amusants. {_SEO_TAIL_EN}",
    },
    'hi': {
        'title': "जापानी सीखें (Learn Japanese) | ThankJapan",
        'description': f"विजुअल क्विज़ के ज़रिए जापानी शब्दावली सीखें। {_SEO_TAIL_EN}",
    },
    'ar': {
        'title': "تعلم اليابانية (Learn Japanese) | ThankJapan",
        'description': f"تعلم مفردات اليابانية من خلال اختبارات مرئية ممتعة. {_SEO_TAIL_EN}",
    },
    'nl': {
        'title': "Japans Leren (Learn Japanese) | ThankJapan",
        'description': f"Leer Japanse woordenschat met leuke visuele quizzen. {_SEO_TAIL_EN}",
    },
    'pl': {
        'title': "Naucz się japońskiego (Learn Japanese) | ThankJapan",
        'description': f"Ucz się japońskiego słownictwa dzięki zabawnym quizom wizualnym. {_SEO_TAIL_EN}",
    },
    'ru': {
        'title': "Учить японский (Learn Japanese) | ThankJapan",
        'description': f"Изучайте японскую лексику с помощью увлекательных визуальных квизов. {_SEO_TAIL_EN}",
    },
    'sv': {
        'title': "Lär dig japanska (Learn Japanese) | ThankJapan",
        'description': f"Lär dig japanska ord med roliga visuella quiz. {_SEO_TAIL_EN}",
    },
    'no': {
        'title': "Lær japansk (Learn Japanese) | ThankJapan",
        'description': f"Lær japanske ord med morsomme visuelle quizer. {_SEO_TAIL_EN}",
    },
    'da': {
        'title': "Lær japansk (Learn Japanese) | ThankJapan",
        'description': f"Lær japanske ord med sjove visuelle quizzer. {_SEO_TAIL_EN}",
    },
    'el': {
        'title': "Μάθε Ιαπωνικά (Learn Japanese) | ThankJapan",
        'description': f"Μάθε ιαπωνικό λεξιλόγιο με διασκεδαστικά οπτικά κουίζ. {_SEO_TAIL_EN}",
    },
    'tr': {
        'title': "Japonca Öğren (Learn Japanese) | ThankJapan",
        'description': f"Eğlenceli görsel quizlerle Japonca kelime öğren. {_SEO_TAIL_EN}",
    },
    'de': {
        'title': "Japanisch lernen (Learn Japanese) | ThankJapan",
        'description': f"Lerne japanisches Vokabular mit spannenden visuellen Quiz. {_SEO_TAIL_EN}",
    },
    # --- best-effort, not yet native-verified (see confidence note above) ---
    'my': {
        'title': "ဂျပန်စာလေ့လာမယ် (Learn Japanese) | ThankJapan",
        'description': f"ပျော်စရာကောင်းသော visual quiz များဖြင့် ဂျပန်စကားလုံးများကို လေ့လာပါ။ {_SEO_TAIL_EN}",
    },
    'km': {
        'title': "រៀនភាសាជប៉ុន (Learn Japanese) | ThankJapan",
        'description': f"រៀនវាក្យសព្ទភាសាជប៉ុនតាមរយៈកម្រងសំណួររូបភាពដ៏សប្បាយរីករាយ។ {_SEO_TAIL_EN}",
    },
    'mn': {
        'title': "Япон хэл сурах (Learn Japanese) | ThankJapan",
        'description': f"Хөгжилтэй визуал асуулт хариултын тусламжтайгаар япон үгсийн санг сур. {_SEO_TAIL_EN}",
    },
    'ur': {
        'title': "جاپانی سیکھیں (Learn Japanese) | ThankJapan",
        'description': f"دلچسپ بصری کوئز کے ذریعے جاپانی الفاظ سیکھیں۔ {_SEO_TAIL_EN}",
    },
    'bn': {
        'title': "জাপানি ভাষা শিখুন (Learn Japanese) | ThankJapan",
        'description': f"মজার ভিজ্যুয়াল কুইজের মাধ্যমে জাপানি শব্দভাণ্ডার শিখুন। {_SEO_TAIL_EN}",
    },
    'sw': {
        'title': "Jifunze Kijapani (Learn Japanese) | ThankJapan",
        'description': f"Jifunze msamiati wa Kijapani kupitia maswali ya picha yenye kufurahisha. {_SEO_TAIL_EN}",
    },
    'si': {
        'title': "ජපන් භාෂාව ඉගෙන ගන්න (Learn Japanese) | ThankJapan",
        'description': f"විනෝදජනක දෘශ්‍ය ප්‍රශ්නාවලිය හරහා ජපන් වචන මාලාව ඉගෙන ගන්න. {_SEO_TAIL_EN}",
    },
}

TOP_PAGE_SEO_COUNTRY_LANG = {
    'ID': 'id', 'PH': 'fil',
    'CI': 'fr', 'SN': 'fr', 'CM': 'fr',
    'IN': 'hi',
    'EG': 'ar', 'MA': 'ar', 'AE': 'ar', 'SA': 'ar', 'OM': 'ar',
    'NL': 'nl', 'PL': 'pl', 'RU': 'ru', 'SE': 'sv', 'NO': 'no',
    'DK': 'da', 'GR': 'el', 'TR': 'tr', 'CH': 'de',
    'MM': 'my', 'KH': 'km', 'MN': 'mn', 'PK': 'ur', 'BD': 'bn',
    'TZ': 'sw', 'KE': 'sw', 'LK': 'si',
    # NG, GH, ZA intentionally absent - see module comment above.
}

DEFAULT_TOP_PAGE_TITLE = "The Ultimate Visual Guide to Japan: Play, Rank & Learn | ThankJapan"
DEFAULT_TOP_PAGE_DESCRIPTION = (
    "Build your Japanese vocabulary through stunning visuals and cultural insights. "
    "Master essential words in 15 languages with interactive quiz games. "
    "Ditch the boring textbooks start your journey from Traveler to Master for free!"
)


def get_top_page_seo_override(request):
    """
    <title>/<meta name="description"> for the English top page ("/"),
    swapped to a bilingual "{local} (Learn Japanese) | ThankJapan" pair when
    the visitor's Cloudflare-detected country is one of the 28 above; the
    existing English copy otherwise (undetected country, or a country not in
    the list - including every one of the 15 fully-translated locales, which
    never call this at all since they have their own dedicated top-page view
    and template untouched by this).

    Always returns a complete dict (seo_title/seo_description), so the
    template never needs a None-check or its own fallback text.
    """
    country_code = detect_cf_country(request)
    lang = TOP_PAGE_SEO_COUNTRY_LANG.get(country_code)
    content = TOP_PAGE_SEO_CONTENT.get(lang)
    if not content:
        return {
            'seo_title': DEFAULT_TOP_PAGE_TITLE,
            'seo_description': DEFAULT_TOP_PAGE_DESCRIPTION,
        }
    return {
        'seo_title': content['title'],
        'seo_description': content['description'],
    }
