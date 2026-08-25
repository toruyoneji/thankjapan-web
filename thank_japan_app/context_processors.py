# thank_japan_app/context_processors.py

from django.conf import settings
from django.utils import timezone
import os


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

    if not lang:
        lang = request.session.get('tj_lang_code')

    if lang:
        request.session['tj_lang_code'] = lang
    else: 
        lang = 'en'
            
    return {
        'lang_code': lang 
    }
    

def review_prompt_status(request):
    """Standing eligibility for the 'reviewed 10 words' in-app review prompt trigger.
    The score/accuracy trigger is handled separately per-request in the game_result view."""
    from .views import is_android_twa

    WORD_COUNT_THRESHOLD = 10

    if not is_android_twa(request):
        return {'review_prompt_wordcount_ready': False}

    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return {'review_prompt_wordcount_ready': False}
        if profile.review_prompt_completed:
            return {'review_prompt_wordcount_ready': False}
        if profile.review_prompt_dismissed_until and profile.review_prompt_dismissed_until > timezone.localdate():
            return {'review_prompt_wordcount_ready': False}
        return {'review_prompt_wordcount_ready': profile.viewed_word_count >= WORD_COUNT_THRESHOLD}

    viewed_count = len(request.session.get('viewed_word_ids', []))
    return {'review_prompt_wordcount_ready': viewed_count >= WORD_COUNT_THRESHOLD}


def firebase_keys(request):
    return {
        'FIREBASE_API_KEY': os.environ.get('FIREBASE_API_KEY', ''),
        'FIREBASE_PROJECT_ID': os.environ.get('FIREBASE_PROJECT_ID', ''),
        'FIREBASE_MESSAGING_SENDER_ID': os.environ.get('FIREBASE_MESSAGING_SENDER_ID', ''),
        'FIREBASE_APP_ID': os.environ.get('FIREBASE_APP_ID', ''),
        'FIREBASE_VAPID_KEY': os.environ.get('FIREBASE_VAPID_KEY', ''),
    }