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
        return self.get_response(request)
