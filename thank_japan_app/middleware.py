# yourapp/middleware.py
from django.http import HttpResponsePermanentRedirect

class RedirectToWwwMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()
        if host == 'thankjapan.com':
            new_url = request.build_absolute_uri().replace('thankjapan.com', 'www.thankjapan.com')
            return HttpResponsePermanentRedirect(new_url)
        # /premium/ (and its language variants) show/charge a price based on
        # CF-IPCountry, which is only trustworthy coming through Cloudflare's
        # edge (see pricing.py). The raw Heroku domain stays reachable for
        # everything else — it's used deliberately for developer testing
        # (e.g. the GA4-exclusion check in premium_info-v2.html) — so only
        # this one path prefix gets sent back through www.thankjapan.com.
        if host.endswith('.herokuapp.com') and request.path.startswith('/premium/'):
            new_url = request.build_absolute_uri().replace(host, 'www.thankjapan.com')
            return HttpResponsePermanentRedirect(new_url)
        return self.get_response(request)


class EnsureLangCodeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if 'tj_lang_code' not in request.session:
            request.session['tj_lang_code'] = 'en'
        return self.get_response(request)


class GAOptOutMiddleware:
    """Lets a browser opt out of GA4 tracking permanently via ?ga_opt_out=1
    (and back in via ?ga_opt_out=0). IP-independent, so it works the same on
    desktop, tablet, and TWA (which shares the device's Chrome cookie jar)."""
    COOKIE_NAME = 'ga_opt_out'
    COOKIE_MAX_AGE = 315360000  # ~10 years

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        flag = request.GET.get('ga_opt_out')
        if flag == '1':
            response.set_cookie(
                self.COOKIE_NAME, '1',
                max_age=self.COOKIE_MAX_AGE,
                httponly=True, samesite='Lax', secure=request.is_secure(),
            )
        elif flag == '0':
            response.delete_cookie(self.COOKIE_NAME)
        return response