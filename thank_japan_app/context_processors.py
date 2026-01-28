# thank_japan_app/context_processors.py

from django.conf import settings

def google_analytics(request):
    return {
        'GA_TRACKING_ID': getattr(settings, 'GA_TRACKING_ID', '')
    }
    


def language_context(request):
    
    lang = request.GET.get('lang')
    
    if not lang:
        path = request.path
        if '/ja/' in path: lang = 'ja'
        elif '/zh-hant/' in path: lang = 'zh-hant'
        elif '/zh-cn/' in path: lang = 'zh-cn'
        elif '/ko/' in path: lang = 'ko'
        elif '/fr/' in path: lang = 'fr'
        elif '/de/' in path: lang = 'de'
        elif '/it/' in path: lang = 'it'
        elif '/es-es/' in path: lang = 'es-es'
        elif '/es-mx/' in path: lang = 'es-mx'
        elif '/pt/' in path: lang = 'pt'
        elif '/pt-br/' in path: lang = 'pt-br'
        elif '/vi/' in path: lang = 'vi'
        elif '/th/' in path: lang = 'th'
        elif '/en-in/' in path: lang = 'en-in'
        else:
            lang = 'en' 
            
    return {
        'lang_code': lang 
    }
