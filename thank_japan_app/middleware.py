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


class HasAccountCookieMiddleware:
    """Marks this device as having a real account at least once, so the top
    page can offer "Login" instead of "Register" as its big CTA on a later
    visit (see daily_question_banner_status-style context processors for the
    read side: thank_japan_app.context_processors.login_cta_status).

    Set from middleware rather than inside player_login/player_register
    directly so it also catches social login (Google/X/Facebook via
    allauth) - allauth's OAuth callback view runs outside our own view code,
    but every successful login of any kind still comes through here as an
    authenticated request. Deliberately never cleared on logout: unlike
    tj_has_played (a UX nicety), this is meant to persist as a durable
    "this device belongs to someone with an account" signal."""
    COOKIE_NAME = 'tj_has_account'
    COOKIE_MAX_AGE = 34560000  # ~400 days, same cap used for tj_has_played

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated and request.COOKIES.get(self.COOKIE_NAME) != '1':
            response.set_cookie(
                self.COOKIE_NAME, '1',
                max_age=self.COOKIE_MAX_AGE,
                httponly=True, samesite='Lax', secure=request.is_secure(),
            )
        return response


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