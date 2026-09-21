from django.shortcuts import render, redirect, get_object_or_404, HttpResponse, HttpResponseRedirect
from django.views import View
from django.views.generic import ListView, DetailView, FormView, TemplateView
from django.views.generic.edit import FormView
from .models import ThankJapanModel, Player, Profile, ThankJapanPremium
from django.db.models import F
from django.contrib import messages
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.conf import settings
from .forms import ContactForm, UsernameForm
from .tile_split import build_tiles, classify_route
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import Http404
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.contrib.auth import authenticate, logout, login as auth_login, logout as auth_logout
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import urlencode
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView
from .context_processors import language_context
from .models import WeeklyScore, ThankJapanBackgroundModel, FCMDevice, DailyQuestion, PremiumRenewalReward, FREE_PREVIEW_PREMIUM_CATEGORIES, DailyActivitySummary, WordReviewLog
from .pricing import get_premium_price, get_top_page_seo_override
from google.oauth2 import service_account
from googleapiclient.discovery import build
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from firebase_admin import messaging
from .firebase_utils import get_firebase_credentials
from allauth.socialaccount.views import ConnectionsView
from .achievements import check_and_unlock_achievements, get_achievement_progress, STREAK_ACHIEVEMENT_CODES
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import logging
import random
import re, itertools
import json
import paypalrestsdk
import requests
import time
import json
import os
import firebase_admin










logger = logging.getLogger(__name__)

def robots_txt(request):
    content = """User-agent: *

Disallow: /game/play/
Disallow: /game/result/
Disallow: /game/start/
Disallow: /login/
Disallow: /register/
Disallow: /account/
Disallow: /thank-you/
Disallow: /verify-android-subscription/

Sitemap: https://www.thankjapan.com/sitemap.xml
"""
    return HttpResponse(content, content_type="text/plain")


def strip_parentheses(text):
    return re.sub(r'\(.*?\)', '', text).strip()

def extract_base_name(text):
    return re.sub(r'\(.*?\)', '', text).strip()

def normalize_romaji(text):
    if not text:
        return ""
    text = text.lower().strip()
    
    text = re.sub(r'[^a-z0-9\-]', '', text)
    
    text = re.sub(r'(a)\-', r'aa', text)
    text = re.sub(r'(i)\-', r'ii', text)
    text = re.sub(r'(u)\-', r'uu', text)
    text = re.sub(r'(e)\-', r'ee', text)
    text = re.sub(r'(o)\-', r'oo', text)
    
    return text

def normalize_consonants(text):
    if not text:
        return ""
    text = text.lower().strip()
    text = normalize_romaji(text)
    
    replacements = [
        ('tsu', 'tu'),
        ('fu', 'hu'),
        ('shi', 'si'),
        ('chi', 'ti'),
        ('ji', 'zi'),
        ('shu', 'syu'),
        ('sha', 'sya'),
        ('sho', 'syo'),
        ('cho', 'tyo'),
        ('cha', 'tya'),
        ('chu', 'tyu'),
        ('jyu', 'zyu'),
        ('jya', 'zya'),
        ('jyo', 'zyo'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)

    return text

def normalize_for_judge(text):
    if not text:
        return ""

    text = text.lower().strip()

    text = text.replace('wa', 'ha')
    text = text.replace('n-', 'nn') 
    
    
    text = re.sub(r'a\-', 'aa', text)
    text = re.sub(r'i\-', 'ii', text)
    text = re.sub(r'u\-', 'uu', text)
    text = re.sub(r'e\-', 'ee', text)
    text = re.sub(r'o\-', 'oo', text)
    text = text.replace('ou', 'oo')


    text = re.sub(r'[^a-z0-9]', '', text)

    repls = [
        ('tsu','tu'),('fu','hu'),('shi','si'),('chi','ti'),('ji','zi'),
        ('sha','sya'),('shu','syu'),('sho','syo'),
        ('cha','tya'),('chu','tyu'),('cho','tyo'),
        ('jya','zya'),('jyu','zyu'),('jyo','zyo'),
        ('sh','sy'),('ch','ty'),('jy','zy')
    ]
    for old, new in repls:
        text = text.replace(old, new)

    text = ''.join(ch for ch, _ in itertools.groupby(text))

    return text


# --- Google Play jadge

def verify_google_play_purchase(purchase_token):
    
    credentials_dict = getattr(settings, 'GOOGLE_PLAY_KEY_DICT', None)
    if not credentials_dict:
        return None

    scopes = ['https://www.googleapis.com/auth/androidpublisher']
    credentials = service_account.Credentials.from_service_account_info(
        credentials_dict, scopes=scopes
    )
    
    
    service = build('androidpublisher', 'v3', credentials=credentials)

    try:
        
        purchase = service.purchases().subscriptions().get(
            packageName=settings.PACKAGE_NAME,
            subscriptionId=settings.GOOGLE_PLAY_PRODUCT_ID,
            token=purchase_token
        ).execute()
        return purchase
    except Exception as e:
        print(f"Google Play API Error: {e}")
        return None

@csrf_exempt
def verify_android_subscription(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            purchase_token = data.get('purchaseToken')

            if not purchase_token:
                return JsonResponse({'status': 'error', 'message': 'No token provided'}, status=400)

            credentials_dict = getattr(settings, 'GOOGLE_PLAY_KEY_DICT', None)
            
            if not credentials_dict:
                return JsonResponse({'status': 'error', 'message': 'Server setup error'}, status=500)

            scopes = ['https://www.googleapis.com/auth/androidpublisher']
            credentials = service_account.Credentials.from_service_account_info(credentials_dict, scopes=scopes)
            service = build('androidpublisher', 'v3', credentials=credentials)

            purchase_info = service.purchases().subscriptions().get(
                packageName=settings.PACKAGE_NAME,
                subscriptionId=settings.GOOGLE_PLAY_PRODUCT_ID,
                token=purchase_token
            ).execute()

            if purchase_info:
                
                if purchase_info.get('acknowledgementState') == 0: 
                    service.purchases().subscriptions().acknowledge(
                        packageName=settings.PACKAGE_NAME,
                        subscriptionId=settings.GOOGLE_PLAY_PRODUCT_ID,
                        token=purchase_token,
                        body={}
                    ).execute()


                expiry_time_ms = int(purchase_info.get('expiryTimeMillis', 0))
                import time
                current_time_ms = int(time.time() * 1000)

                if expiry_time_ms > current_time_ms:
                    user = request.user
                    if user.is_authenticated:
                        # paymentState 2 == "Free trial" per the Play Developer API v3
                        # purchases.subscriptions schema.
                        is_trial = purchase_info.get('paymentState') == 2
                        profile = user.profile

                        if is_trial and profile.trial_used:
                            logger.warning(
                                f"User {user.id} attempted to reuse an already-consumed "
                                f"free trial via Google Play (token={purchase_token})"
                            )
                            return JsonResponse(
                                {'status': 'error', 'message': 'trial already used'}, status=400
                            )

                        profile.is_premium = True
                        profile.premium_expires_at = timezone.now() + timedelta(
                            milliseconds=expiry_time_ms - current_time_ms
                        )
                        profile.google_play_purchase_token = purchase_token
                        profile.is_trial = is_trial
                        if is_trial:
                            profile.trial_used = True
                        profile.save()
                        return JsonResponse({'status': 'success', 'expiry': expiry_time_ms})
            
            return JsonResponse({'status': 'error', 'message': '検証に失敗したか、期限切れです'}, status=400)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

#android google play
def is_android_twa(request):
    # Session flag (set by mark_twa below, from the client's document.referrer
    # check) takes priority: X-Requested-With is only added by Chrome when the
    # Digital Asset Links handshake for the Trusted Web Activity succeeds, so
    # a real device can be genuinely running inside the app and still never
    # send it (assetlinks.json fingerprint mismatch, verification hiccup,
    # etc). document.referrer == 'android-app://<package>' is a weaker but
    # independent signal - Chrome sets it whenever the page was opened via
    # the app's Android intent, regardless of asset-link verification - so it
    # catches cases the header alone misses. Once true for a session it stays
    # true; there's no legitimate way to go from real TWA back to plain
    # browser mid-session.
    if request.session.get('is_twa'):
        debug_line = f"[TWA-CHECK] path={request.path} source=session result=True UA={request.META.get('HTTP_USER_AGENT', '')!r}"
        print(debug_line, flush=True)
        logger.info(debug_line)
        return True

    x_requested_with = request.META.get('HTTP_X_REQUESTED_WITH')
    result = x_requested_with == settings.PACKAGE_NAME

    debug_line = f"[TWA-CHECK] path={request.path} X-Requested-With={x_requested_with!r} expected={settings.PACKAGE_NAME!r} result={result} UA={request.META.get('HTTP_USER_AGENT', '')!r}"
    print(debug_line, flush=True)
    logger.info(debug_line)

    return result


@require_POST
def mark_twa(request):
    """Called once by the client (see includes/twa_referrer_check.html) when
    document.referrer indicates the page was opened via the Android app's
    Trusted Web Activity intent. See is_android_twa() above for why this
    exists alongside the X-Requested-With check rather than replacing it.
    No @login_required: guests browsing /premium/ etc. need this too."""
    request.session['is_twa'] = True
    return JsonResponse({'status': 'success'})


@login_required
@require_POST
def add_missing_email(request):
    """Lets a user who signed up via a social provider without an email
    (e.g. X, which never returns one) add one voluntarily from the
    dismissible banner. Not required to keep using the site."""
    email = (request.POST.get('email') or '').strip()

    if not email:
        return JsonResponse({'status': 'error', 'message': 'required'}, status=400)

    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({'status': 'error', 'message': 'invalid'}, status=400)

    email_taken = (
        User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists()
        or Player.objects.filter(email__iexact=email).exclude(username=request.user.username).exists()
    )
    if email_taken:
        return JsonResponse({'status': 'error', 'message': 'taken'}, status=400)

    request.user.email = email
    request.user.save(update_fields=['email'])

    player = Player.objects.filter(username=request.user.username).first()
    if player:
        player.email = email
        player.save(update_fields=['email'])

    request.session['email_prompt_dismissed'] = True
    return JsonResponse({'status': 'success'})


@login_required
@require_POST
def dismiss_email_prompt(request):
    request.session['email_prompt_dismissed'] = True
    return JsonResponse({'status': 'success'})


@require_POST
def dismiss_daily_question_banner(request):
    # No @login_required: the Daily Question (and its top-page reminder banner)
    # is available to guests too, matching the rest of the feature.
    request.session[DAILY_QUESTION_BANNER_DISMISSED_KEY] = timezone.localdate().isoformat()
    return JsonResponse({'status': 'success'})


@login_required
def dismiss_trial_ended_popup(request):
    # @login_required (not @require_POST too - matches dismiss_daily_question_banner's
    # leniency): only logged-in users can ever have trial_used=True in the
    # first place, so this can't meaningfully be called by a guest anyway.
    # Persisted on Profile (not the session) - this popup is meant to be shown
    # at most once ever, not once per session. Called from both the "yes" and
    # "no" buttons (see top-main-v2.html), so responding either way silences
    # it permanently.
    profile = request.user.profile
    profile.trial_ended_popup_dismissed = True
    profile.save(update_fields=['trial_ended_popup_dismissed'])
    return JsonResponse({'status': 'success'})


@login_required
@require_POST
def toggle_daily_question_notify(request):
    # Phase 2 step (1): persists the on/off preference only. The actual
    # browser-permission / FCM-token flow (soft ask -> native prompt -> token
    # save) is wired up in front of this endpoint in the next step; for now
    # this just records the user's choice.
    profile = request.user.profile
    profile.daily_question_notify = request.POST.get('enabled') == 'true'
    profile.save(update_fields=['daily_question_notify'])
    return JsonResponse({'status': 'success', 'enabled': profile.daily_question_notify})


#category: urls:
CATEGORY_URL_MAP = {
    'culture': 'culturepage',
    'food': 'foodpage',
    'cook': 'cookpage',
    'fashion': 'fashionpage',
    'nature': 'naturepage',
    'animal': 'animalpage',
    'sports': 'sportspage',
    'householditems': 'householditemspage',
    'appliances': 'appliancespage',
    'building': 'buildingpage',
    'flower': 'flowerpage',
    'work': 'workpage',
    'live': 'livepage',
    'body': 'bodypage',
    'dailyactions' : 'dailyactionspage',
    'DailyConversation': 'dailyconversation',
    'BusinessJapanese': 'businessjapanese',
    'LivingInJapan': 'living_in_japan_page',
    'MedicalEmergency': 'medical_emergency',
    'RealEstateRules': 'real_estate_rules',
    'TourismEtiquette': 'tourism_etiquette',
    'Prefectures': 'prefectures',
    'Entertainment': 'entertainment',
    'slang': 'slang',
    'RomanceRelationships': 'romance_relationships',
}


#new-privacy-policy

@login_required
@require_POST
def update_policy_agreement(request):
    profile = request.user.profile
    profile.privacy_policy_version = "2026-03"
    profile.save()
    return JsonResponse({'status': 'success'})


#password send


class CustomPasswordResetView(PasswordResetView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        lang = self.request.GET.get('lang') or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = lang
        return context

    def get_success_url(self):
       
        lang = self.request.GET.get('lang') or self.request.session.get('tj_lang_code', 'en')
        return reverse('password_reset_done') + f"?lang={lang}"

    def form_valid(self, form):
        
        lang = self.request.GET.get('lang') or self.request.session.get('tj_lang_code', 'en')
            
        opts = {
            'use_https': self.request.is_secure(),
            'token_generator': self.token_generator,
            'from_email': self.from_email,
            'email_template_name': self.email_template_name,
            'subject_template_name': self.subject_template_name,
            'request': self.request,
            'html_email_template_name': self.html_email_template_name,
            'extra_email_context': {
                'lang_code': lang,  
                'site_name': 'Thank Japan'
            }, 
        }
        form.save(**opts)
        
        return redirect(self.get_success_url())
    
    

class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    def get_success_url(self):
        lang = self.request.GET.get('lang') or self.request.session.get('tj_lang_code', 'en')
        return reverse('password_reset_complete') + f"?lang={lang}"
    
    
    
#company infomation
class CompanyFormView(TemplateView):
     template_name = 'thank_japan_app/info/company.html'
     
class CompanyFormZHCNView(TemplateView):
     template_name = 'thank_japan_app/info/company_zh_cn-v2.html'
     
class CompanyFormZHHANTView(TemplateView):
     template_name = 'thank_japan_app/info/company_zh_hant-v2.html'
     
class CompanyFormVIView(TemplateView):
     template_name = 'thank_japan_app/info/company_vi-v2.html'
     
class CompanyFormTHView(TemplateView):
     template_name = 'thank_japan_app/info/company_th-v2.html'
     
class CompanyFormPTView(TemplateView):
     template_name = 'thank_japan_app/info/company_pt-v2.html'
     
class CompanyFormPTBRView(TemplateView):
     template_name = 'thank_japan_app/info/company_pt_br-v2.html'
     
class CompanyFormKOView(TemplateView):
     template_name = 'thank_japan_app/info/company_ko-v2.html'
     
class CompanyFormJAView(TemplateView):
     template_name = 'thank_japan_app/info/company_ja-v2.html'
     
class CompanyFormITView(TemplateView):
     template_name = 'thank_japan_app/info/company_it-v2.html'
     
class CompanyFormFRView(TemplateView):
     template_name = 'thank_japan_app/info/company_fr-v2.html'
     
class CompanyFormESMXView(TemplateView):
     template_name = 'thank_japan_app/info/company_es_mx-v2.html'
     
class CompanyFormESESView(TemplateView):
     template_name = 'thank_japan_app/info/company_es_es-v2.html'
     
class CompanyFormENINView(TemplateView):
     template_name = 'thank_japan_app/info/company_en_in-v2.html'
     
class CompanyFormDEView(TemplateView):
     template_name = 'thank_japan_app/info/company_de-v2.html'
     
     
     
#legalnotice

class LegalNoticeView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeZHCNView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_zh_cn-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
     
class LegalNoticeZHHANTView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_zh_hant-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeVIView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_vi-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeTHView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_th-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticePTView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_pt-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticePTBRView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_pt_br-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeKOView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_ko-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeJAView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_ja-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeITView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_it-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeFRView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_fr-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeESMXView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_es_mx-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeESESView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_es_es-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeENINView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_en_in-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class LegalNoticeDEView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_de-v2.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
    
    
#privacypolicy    
class PrivacyPolicy(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyZHCN(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_zh_cn.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyZHHANT(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_zh_hant.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyVI(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_vi.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyTH(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_th.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyPT(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_pt.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyPTBR(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_pt_br.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyKO(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_ko.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyJA(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_ja.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyIT(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_it.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyFR(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_fr.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyESMX(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_es_mx.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyESES(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_es_es.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class PrivacyPolicyENIN(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_en_in.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context

class PrivacyPolicyDE(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_de.html"
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
    
#riyoukiyaku    
class KiyakuView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuZHCNView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_zh_cn.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuZHHANTView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_zh_hant.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuVIView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_vi.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuTHView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_th.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuPTView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_pt.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuPTBRView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_pt_br.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuKOView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_ko.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuJAView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_ja.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuITView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_it.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuFRView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_fr.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuESMXView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_es_mx.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuESESView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_es_es.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context

class KiyakuENINView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_en_in.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
class KiyakuDEView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_de.html"
    model = ThankJapanModel
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_twa'] = is_android_twa(self.request)
        return context
    
    
#login_bonus


def apply_login_bonus(request):
    if request.user.is_authenticated:
        profile = request.user.profile
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)


        if profile.last_bonus_date != today:


            if profile.last_bonus_date == yesterday:

                profile.streak_count += 1
            else:

                profile.streak_count = 1


            if profile.streak_count % 7 == 0:
                bonus_points = 5
            else:
                bonus_points = 1


            profile.total_score += bonus_points
            profile.last_bonus_date = today
            # A fresh active day ends any idle streak being tracked for the
            # inactivity-reminder emails, so the next idle period starts clean.
            profile.last_reminder_step_sent = 0
            profile.save()

            
            player, created = Player.objects.get_or_create(username=request.user.username)
            player.total_score = profile.total_score 
            player.save()
            
            
            request.session['show_bonus_toast'] = True

            request.session['earned_points'] = bonus_points

            newly_unlocked = check_and_unlock_achievements(profile)
            streak_unlocks = [a['code'] for a in newly_unlocked if a['code'] in STREAK_ACHIEVEMENT_CODES]
            if streak_unlocks:
                request.session['newly_unlocked_streak_achievements'] = streak_unlocks

    else:
        
        request.session['show_guest_bonus_alert'] = True        
        
        

def update_login_streak(profile):
    today = date.today()
    yesterday = today - timedelta(days=1)
    
    if profile.last_bonus_date == today:
        return 0
    
    if profile.last_bonus_date == yesterday:
        profile.streak_count += 1
    else:
        
        profile.streak_count = 1

    
    if profile.streak_count % 7 == 0:
        bonus = 5
    else:
        bonus = 1

    
    profile.total_score += bonus
    profile.last_bonus_date = today
    profile.save()

    return bonus        
        
#bgm
      
def get_bgm_url(page_type):
    try:
        record = ThankJapanBackgroundModel.objects.filter(
            page_type=page_type, sound__isnull=False
        ).first()
        
        if record and record.sound:
            url = record.sound.url
            
            if url and url.startswith('http://'):
                url = url.replace('http://', 'https://', 1)
            return url
        return None
    except AttributeError:
        return None
            



class BGMContextMixin:
    bgm_page_type = None  

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.bgm_page_type:
            context['bgm_url'] = get_bgm_url(self.bgm_page_type)
            context['bgm_page_type'] = self.bgm_page_type
        return context
    
    
    
#google firebase

@csrf_exempt
def save_fcm_token(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            token = data.get('token')
            lang = data.get('lang', 'ja')
            user = request.user if request.user.is_authenticated else None
            
            FCMDevice.objects.update_or_create(
                token=token,
                defaults={'user': user, 'lang': lang}
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'method not allowed'}, status=405)





# Firebase Admin SDKの初期化
if not firebase_admin._apps:
    try:
        cred = get_firebase_credentials()
        if cred:
            firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"Firebase initialization error: {e}")



NOTIFICATION_MESSAGES = {
    'daily_growth': {
        'ja': {'title': '今日も一つ、単語を覚えてみよう。', 'body': 'あなたはまた一つ、レベルが上がります。'},
        'en': {'title': 'Let\'s learn one word today.', 'body': 'Your level is about to go up once more.'},
        'vi': {'title': 'Hôm nay hãy học một từ mới nhé.', 'body': 'Cấp độ của bạn sẽ tăng thêm một bậc nữa.'},
        'th': {'title': 'วันนี้มาเรียนรู้คำศัพท์ใหม่กันเถอะ', 'body': 'เลเวลของคุณกำลังจะเพิ่มขึ้นอีกขั้นแล้ว'},
        'ko': {'title': '오늘도 단어 하나를 익혀보세요.', 'body': '당신의 레벨이 한 단계 더 올라갈 것입니다.'},
        'zh-hant': {'title': '今天也來記一個單詞吧。', 'body': '你的等級將會再次提升。'},
        'zh-cn': {'title': '今天也来记一个单词吧。', 'body': '你的等级将会再次提升。'},
        'fr': {'title': 'Apprenons un mot aujourd\'hui.', 'body': 'Votre niveau est sur le point d\'augmenter.'},
        'it': {'title': 'Impariamo una parola oggi.', 'body': 'Il tuo livello sta per salire di nuovo.'},
        'es-es': {'title': 'Aprendamos una palabra hoy.', 'body': 'Tu nivel está a punto de subir de nuevo.'},
        'es-mx': {'title': 'Aprendamos una palabra hoy.', 'body': 'Tu nivel está a punto de subir de nuevo.'},
        'de': {'title': 'Lass uns heute ein Wort lernen.', 'body': 'Dein Level wird bald wieder steigen.'},
        'pt': {'title': 'Vamos aprender uma palavra hoje.', 'body': 'O teu nível está prestes a subir novamente.'},
        'pt-br': {'title': 'Vamos aprender uma palavra hoje.', 'body': 'Seu nível está prestes a subir novamente.'},
        'en-in': {'title': 'Let\'s learn one word today.', 'body': 'Your level is about to go up once more.'},
    }
}


def broadcast_daily_message(request):
    if not request.user.is_staff:
        return JsonResponse({'status': 'denied'})

    devices = FCMDevice.objects.all()
    success_count = 0
    for device in devices:
        lang = device.lang if device.lang in NOTIFICATION_MESSAGES['daily_growth'] else 'en'
        content = NOTIFICATION_MESSAGES['daily_growth'][lang]
        message = messaging.Message(
            notification=messaging.Notification(title=content['title'], body=content['body']),
            token=device.token,
            data={'url': f'/?lang={lang}'} 
        )
        try:
            messaging.send(message)
            success_count += 1
        except Exception:
            device.delete() 

    return JsonResponse({'status': f'Sent {success_count} messages!'})




#country top page

class TopView(BGMContextMixin, ListView): 
    template_name = "thank_japan_app/toppage/toppage.html"
    model = ThankJapanModel
    bgm_page_type = 'top'

    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'en'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        context.update(get_top_page_seo_override(self.request))
        return context


class TopViewJA(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_ja.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'ja'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewVI(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_vi.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'vi'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewFR(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_fr.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'fr'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewIT(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_it.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'it'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewPT(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_pt.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'pt'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewZHCN(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_zh_cn.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'zh-cn'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewZHHANT(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_zh_hant.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'zh-hant'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewKO(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_ko.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'ko'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    

class TopViewESES(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_es_es.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'es-es'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewDE(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_de.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'de'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewTH(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_th.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'th'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewPTBR(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_pt_br.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'pt-br'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewESMX(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_es_mx.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'es-mx'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
class TopViewENIN(BGMContextMixin, ListView):
    template_name = "thank_japan_app/toppage/toppage_en_in.html"
    model = ThankJapanModel
    bgm_page_type = 'top'
    def get(self, request, *args, **kwargs):
        request.session['tj_lang_code'] = 'en-in'
        apply_login_bonus(request)
        return super().get(request, *args, **kwargs)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['bonus_received'] = self.request.session.pop('show_bonus_toast', False)
        context['show_guest_alert'] = self.request.session.pop('show_guest_bonus_alert', False)
        context['earned_points'] = self.request.session.pop('earned_points', 0)
        context['has_played'] = self.request.COOKIES.get('tj_has_played') == '1'
        context['nav_tutorial_learn_step_active'] = _nav_tutorial_eligible(self.request)
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
        return context
    
    
    
#manage_btn
@login_required
@login_required
def account_settings_redirect(request):
    lang = request.session.get('tj_lang_code', 'en')
    
    mapping = {
        'en': 'account_settings',
        'ja': 'account_settingsja',
        'vi': 'account_settingsvi',
        'fr': 'account_settingsfr',
        'it': 'account_settingsit',
        'pt': 'account_settingspt',
        'zh-hant': 'account_settingszhHANT',
        'ko': 'account_settingsko',
        'es-es': 'account_settingsesES',
        'de': 'account_settingsde',
        'th': 'account_settingsth',
        'pt-br': 'account_settingsptBR',
        'es-mx': 'account_settingsesMX',
        'en-in': 'account_settingsenIN',
        'zh-cn': 'account_settingszhCN',
    }
    
    url_name = mapping.get(lang, 'account_settings')
    return redirect(f"{reverse(url_name)}?from=result")



def contact_view(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            title = form.cleaned_data['title']
            email = form.cleaned_data['email']
            message = form.cleaned_data['message']

    
            full_message = f"From: {name} <{email}>\nTitle: {title}\n\n{message}"

            send_mail(
                subject=f"[Support] {title}",
                message=full_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=['yonetoru0@gmail.com'],  
                fail_silently=False,
            )
            return render(request, 'thank_japan_app/contact_thanks.html', {'name': name})
    else:
        form = ContactForm()
    return render(request, 'thank_japan_app/contact.html', {'form': form})


   
def contact_thanks(request):
    template = 'thank_japan_app/contact_thanks.html'
    return render(request, template)




#Game and login register

def player_register(request):
    next_url = request.GET.get('next') or request.POST.get('next') or 'toppage'
    
    if 'register' in str(next_url) or 'login' in str(next_url):
        next_url = 'toppage'
        
    lang_code = request.GET.get('lang') or request.POST.get('lang') or request.session.get('tj_lang_code') or 'en'
    guest_score = request.POST.get('guest_score') or request.GET.get('guest_score') or '0'

    if request.method == "POST":
        form = UsernameForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            email = form.cleaned_data['email']
            raw_password = form.cleaned_data['password']
            country = form.cleaned_data['country']

            if User.objects.filter(username=username).exists() or Player.objects.filter(username=username).exists():
                messages.error(request, "This username is already taken.")
                return render(request, 'thank_japan_app/player_register-v2.html', {
                    'form': form, 'next': next_url, 'lang_code': lang_code, 'guest_score': guest_score
                })

            if User.objects.filter(email=email).exists() or Player.objects.filter(email=email).exists():
                messages.error(request, "This email address is already registered.")
                return render(request, 'thank_japan_app/player_register-v2.html', {
                    'form': form, 'next': next_url, 'lang_code': lang_code, 'guest_score': guest_score
                })

            user = User.objects.create_user(username=username, email=email, password=raw_password)
            player = Player(username=username, email=email, country=country)
            player.set_password(raw_password)
            player.save()

            if hasattr(user, 'profile'):
                user.profile.country = country
                user.profile.save()

            messages.success(request, "Account created! Welcome to ThankJapan!")

            # Log the new account straight in (same flow player_login uses),
            # instead of bouncing the user back to the login form to
            # re-enter the credentials they just typed.
            auth_user = authenticate(request, username=username, password=raw_password)

            temp_guest_score = int(request.session.get('game_score', 0))

            keys_to_clear = ['is_guest', 'game_score', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty', 'player_id']
            for key in keys_to_clear:
                request.session.pop(key, None)

            request.session['tj_lang_code'] = lang_code

            if auth_user is not None:
                auth_login(request, auth_user)
                request.session['is_guest'] = False

                try:
                    profile = auth_user.profile
                    profile.total_score += temp_guest_score
                    profile.save()

                    player.total_score = profile.total_score
                    player.save()

                    request.session['player_id'] = player.id
                except Exception:
                    pass

            # 'registered=1' lets the destination page fire the GA4 'sign_up' event once.
            if next_url == 'toppage':
                lang_urls = {
                    'ja': 'toppageja', 'vi': 'toppagevi', 'fr': 'toppagefr',
                    'it': 'toppageit', 'pt': 'toppagept', 'zh-hant': 'toppagezhHANT',
                    'zh-cn': 'toppagezhCN', 'ko': 'toppageko', 'es-es': 'toppageesES',
                    'de': 'toppagede', 'th': 'toppageth', 'pt-br': 'toppageptBR',
                    'es-mx': 'toppageesMX', 'en-in': 'toppageenIN'
                }
                target_url = reverse(lang_urls.get(lang_code, 'toppage'))
            else:
                try:
                    target_url = reverse(next_url)
                except Exception:
                    target_url = reverse('toppage')

            query_params = urlencode({'registered': '1'})
            return redirect(f"{target_url}?{query_params}")

    else:
        form = UsernameForm()

    return render(request, 'thank_japan_app/player_register-v2.html', {
        'form': form, 
        'next': next_url, 
        'lang_code': lang_code, 
        'guest_score': guest_score
    })
        

def player_login(request):
    lang_code = request.GET.get('lang') or request.POST.get('lang') or request.session.get('tj_lang_code') or 'en'
    next_url = request.GET.get('next') or request.POST.get('next') or 'toppage'
    
    if 'login' in str(next_url) or 'register' in str(next_url):
        next_url = 'toppage'

    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            temp_guest_score = int(request.session.get('game_score', 0))

            keys_to_clear = ['is_guest', 'game_score', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty']
            for key in keys_to_clear:
                request.session.pop(key, None)
            
            auth_login(request, user)
            
            request.session['tj_lang_code'] = lang_code
            request.session['is_guest'] = False 
            
            try:
                profile = user.profile
                profile.total_score += temp_guest_score
                profile.save()

                player_obj, created = Player.objects.get_or_create(username=user.username)
                player_obj.total_score = profile.total_score
                player_obj.save()
                
                request.session['player_id'] = player_obj.id
            except:
                pass

            if next_url == 'toppage':
                lang_urls = {
                    'ja': 'toppageja', 'vi': 'toppagevi', 'fr': 'toppagefr',
                    'it': 'toppageit', 'pt': 'toppagept', 'zh-hant': 'toppagezhHANT',
                    'zh-cn': 'toppagezhCN', 'ko': 'toppageko', 'es-es': 'toppageesES',
                    'de': 'toppagede', 'th': 'toppageth', 'pt-br': 'toppageptBR',
                    'es-mx': 'toppageesMX', 'en-in': 'toppageenIN'
                }
                return redirect(lang_urls.get(lang_code, 'toppage'))

            try:
                return redirect(next_url)
            except Exception:
                # Unknown/unregistered next (bad ?next= value) must not crash
                # after the user is already logged in.
                return redirect('toppage')
        else:
            messages.error(request, "Invalid username or password.", extra_tags="login_invalid")
            
    return render(request, 'thank_japan_app/player_login-v2.html', {
        'next': next_url,
        'lang_code': lang_code
    })        
    
def player_logout(request):
    lang_code = request.GET.get('lang') or request.session.get('tj_lang_code')
    
    if not lang_code:
        referer = request.META.get('HTTP_REFERER', '')
        if '/ja/' in referer: lang_code = 'ja'
        elif '/vi/' in referer: lang_code = 'vi'
        elif '/fr/' in referer: lang_code = 'fr'
        elif '/it/' in referer: lang_code = 'it'
        elif '/pt/' in referer: lang_code = 'pt'
        elif '/zh-hant/' in referer: lang_code = 'zh-hant'
        elif '/zh-cn/' in referer: lang_code = 'zh-cn'
        elif '/ko/' in referer: lang_code = 'ko'
        elif '/es-es/' in referer: lang_code = 'es-es'
        elif '/de/' in referer: lang_code = 'de'
        elif '/th/' in referer: lang_code = 'th'
        elif '/pt-br/' in referer: lang_code = 'pt-br'
        elif '/es-mx/' in referer: lang_code = 'es-mx'
        elif '/en-in/' in referer: lang_code = 'en-in'
        else: lang_code = 'en'

    auth_logout(request)
    
    request.session['tj_lang_code'] = lang_code
    request.session['is_guest'] = True 
    messages.info(request, "Logged out successfully.")

    lang_urls = {
        'ja': 'toppageja', 'vi': 'toppagevi', 'fr': 'toppagefr',
        'it': 'toppageit', 'pt': 'toppagept', 'zh-hant': 'toppagezhHANT',
        'zh-cn': 'toppagezhCN', 'ko': 'toppageko', 'es-es': 'toppageesES',
        'de': 'toppagede', 'th': 'toppageth', 'pt-br': 'toppageptBR',
        'es-mx': 'toppageesMX', 'en-in': 'toppageenIN'
    }
    
    return redirect(lang_urls.get(lang_code, 'toppage'))


def delete_player_confirm(request):
    if not request.user.is_authenticated:
        return redirect('player_login')
    return render(request, 'thank_japan_app/delete_player.html')

@require_POST
def delete_player(request):
    if not request.user.is_authenticated:
        return redirect('player_login')

    password = request.POST.get('password')
    user = request.user

    if user.check_password(password):
        Player.objects.filter(username=user.username).delete()
        user.delete()
        request.session.flush()
        messages.success(request, "Your account has been deleted.")
        return redirect('toppage')
    else:
        messages.error(request, "Incorrect password. Account not deleted.")
        return redirect('delete_player_confirm')
    
    

DIFFICULTY_SETTINGS = {
    'single': {'num_questions': 1, 'model_type': 'free'},
    # First-visit taste flow from the top page: 3 EASY-tier questions, no
    # difficulty picker (the tile game shares a single 60s timer, see
    # game_play's timer setup below).
    # dummy_count left at the default (1 => 2 choices) for first_taste/easy so
    # beginners aren't discouraged; NORMAL and above use 3 (=> 4 choices) so
    # the answer can't be found by elimination alone.
    'first_taste': {'category_filter': ['sports', 'food', 'animal', 'dailyactions'], 'length_regex': r'^.{1,20}$', 'num_questions': 3, 'model_type': 'free'},
    'easy': {'category_filter': ['sports', 'food', 'animal', 'dailyactions'], 'length_regex': r'^.{1,20}$', 'num_questions': 50, 'model_type': 'free'},
    'normal': {'category_filter': ['cook', 'food', 'culture', 'body', 'live', 'work', 'dailyactions'], 'length_regex': r'^.{1,9}$', 'num_questions': 50, 'model_type': 'free', 'dummy_count': 3},
    'hard': {'category_filter': None, 'length_regex': r'^.{1,9}$', 'num_questions': 50, 'model_type': 'free', 'dummy_count': 3},
    'super_hard': {'category_filter': None, 'length_regex': None, 'num_questions': 50, 'model_type': 'free', 'dummy_count': 3},

    'kanji1': {
        'category_filter': ['nature', 'food', 'cook', 'animal', 'building', 'dailyactions'],
        'length_regex': r'^.{1,3}$',
        'num_questions': 50,
        'model_type': 'free',
        'is_kanji_mode': True,
        'dummy_count': 3,
    },

        'kanji2': {
        'category_filter': ['culture', 'work', 'fashion', 'flower', 'householditems', 'sports', 'body'],
        'length_regex': r'^.{1,3}$',
        'num_questions': 50,
        'model_type': 'free',
        'is_kanji_mode': True,
        'dummy_count': 3,
    },


    'sample_premium': {'category_filter': ['DailyConversation', 'slang', 'TourismEtiquette' ,'Entertainment', 'RomanceRelationships'], 'jlpt_level': ['N5', 'N4', 'N3'], 'num_questions': 550, 'model_type': 'premium', 'dummy_count': 3},
    'n5_premium': {'jlpt_level': 'N5', 'num_questions': 50, 'model_type': 'premium', 'dummy_count': 3},
    'n4_premium': {'jlpt_level': 'N4', 'num_questions': 50, 'model_type': 'premium', 'dummy_count': 3},
    'n3_premium': {'jlpt_level': 'N3', 'num_questions': 50, 'model_type': 'premium', 'dummy_count': 3},
}
# DIFFICULTY_SETTINGS above is a holdover from the retired multiple-choice
# difficulty picker. Only the 'easy'/'normal'/'hard' category+length filters
# are still read (by _build_tile_game_questions below), to keep the tile
# game's EASY/NORMAL/HARD word pools consistent with what those labels used
# to mean. The picker UI itself (game_start-v2.html) no longer links to any
# of these tiers.

TILE_GAME_POOL_SIZE = 50  # generous upper bound; the 60s timer always ends the game long before this runs out
FIRST_TASTE_QUESTION_COUNT = 3  # first-visit guest trial: 3 EASY-tier words, untimed
TIME_EXTEND_COST = 10  # points spent per "add more time" purchase, no cap on repeat use
TIME_EXTEND_SECONDS = 10

# --- Navigation-exploration tutorial (returning-guest only, 2026-09-17) ---
# A second, separate tutorial from first_taste above: guides a guest who has
# already played once (tj_has_played) through Learn -> Categories -> Cook ->
# a specific word's detail page -> practicing it in single mode -> back to
# the top page, exactly once (tj_nav_tutorial_seen). Targeted by slug (not
# grid position) so it keeps working if cook.html's word ordering ever
# shifts - see NAV_TUTORIAL_TARGET_SLUG below and its per-view fallback
# (nothing is shown if the word is ever removed).
NAV_TUTORIAL_TARGET_SLUG = 'cook-tsukemen'  # つけ麺 (Tsukemen) - id 927, category=cook


def _nav_tutorial_eligible(request):
    """True for a guest who has played the game at least once but hasn't
    seen the navigation-exploration tutorial yet - the same guest-only,
    one-time-only shape as first_taste's own gate, just inverted (this one
    needs has_played=1 instead of guarding against it).

    2026-09-20: also excludes a guest whose first_taste tutorial just
    finished in this same browser session (see tj_first_taste_just_completed,
    set by game_result) - tj_has_played flips to '1' the moment first_taste
    is *started*, not completed, so without this a guest landing back on the
    top page right after finishing the ramen tutorial would immediately
    qualify for this one too and get walked straight into a second,
    back-to-back tutorial. Deferred to their next visit instead."""
    return (
        not request.user.is_authenticated
        and request.COOKIES.get('tj_has_played') == '1'
        and request.COOKIES.get('tj_nav_tutorial_seen') != '1'
        and request.COOKIES.get('tj_first_taste_just_completed') != '1'
    )


def _sample_ids(model, category_filter=None, length_regex=None, exclude_ids=(), count=1):
    """Random id sample from `model`, honoring the same category/length
    filters the old difficulty tiers used."""
    qs = model.objects.all()
    if category_filter:
        qs = qs.filter(category__in=category_filter)
    if length_regex:
        qs = qs.filter(name__iregex=length_regex)
    if exclude_ids:
        qs = qs.exclude(id__in=exclude_ids)
    ids = list(qs.values_list('id', flat=True))
    random.shuffle(ids)
    return ids[:count]


def _long_word_candidate_ids(exclude_ids=()):
    """All free-tier 'long word' ids (chunk-split route: >5 mora or contains
    alphabet), unshuffled and uncapped - callers shuffle/slice themselves."""
    qs = ThankJapanModel.objects.exclude(id__in=exclude_ids).values_list('id', 'jpname')
    return [pk for pk, jpname in qs if classify_route(jpname)['route'] == 'chunk']


def _sample_long_word_ids(exclude_ids=(), count=1):
    """Free-tier 'long word' pool (chunk-split route: >5 mora or contains
    alphabet) used from Q5 onward for players without premium access."""
    long_ids = _long_word_candidate_ids(exclude_ids)
    random.shuffle(long_ids)
    return long_ids[:count]


EASY_MAX_MORA = 4  # Q1-2 should be a quick, genuinely gentle first puzzle


def _sample_easy_ids(category_filter, exclude_ids=(), count=1):
    """EASY pool: the old 'easy' category filter alone doesn't guarantee a
    short word (e.g. a rare animal name like chimpanzee/ちんぱんじー is
    still in the 'animal' category), and for a tile puzzle, mora count -
    not category - is what actually makes a word quick to solve. So filter
    down to short (<=4 mora) words on top of the category filter."""
    qs = ThankJapanModel.objects.exclude(id__in=exclude_ids)
    if category_filter:
        qs = qs.filter(category__in=category_filter)
    candidates = list(qs.values_list('id', 'jpname'))
    short_ids = [pk for pk, jpname in candidates if (classify_route(jpname)['mora_count'] or 99) <= EASY_MAX_MORA]
    random.shuffle(short_ids)
    return short_ids[:count]


def _build_tile_game_questions(is_premium):
    """Ordered question list for the tile-rearrangement time attack:
    Q1-2 EASY, Q3-5 NORMAL/NORMAL/HARD, Q6+ PREMIUM (subscribers) or long
    free words + the free-preview premium categories (everyone else).
    Returns [{'id', 'model': 'free'|'premium'}, ...] - a game can mix both
    models, unlike the old single-model-per-game multiple-choice flow."""
    easy, normal, hard = DIFFICULTY_SETTINGS['easy'], DIFFICULTY_SETTINGS['normal'], DIFFICULTY_SETTINGS['hard']

    free_ids = _sample_easy_ids(easy['category_filter'], count=2)
    free_ids += _sample_ids(ThankJapanModel, normal['category_filter'], normal['length_regex'], exclude_ids=free_ids, count=2)
    free_ids += _sample_ids(ThankJapanModel, hard['category_filter'], hard['length_regex'], exclude_ids=free_ids, count=1)

    questions = [{'id': pk, 'model': 'free'} for pk in free_ids]

    remaining = TILE_GAME_POOL_SIZE - len(questions)
    if is_premium:
        premium_ids = list(ThankJapanPremium.objects.values_list('id', flat=True))
        random.shuffle(premium_ids)
        questions += [{'id': pk, 'model': 'premium'} for pk in premium_ids[:remaining]]
    else:
        # Non-subscribers' Q6+ pool: free "long word" content plus the
        # premium-table categories that are free for everyone (2026-09-11 -
        # these were previously reachable only via each word's own detail
        # page/single-mode review, never from the main game for guests).
        # Tagged 'premium' since they live in ThankJapanPremium (same as
        # single-mode review already does for them) - scored the same 2pt
        # as any other premium-model question, by explicit product decision.
        long_free_ids = _long_word_candidate_ids(exclude_ids=free_ids)
        free_preview_ids = list(
            ThankJapanPremium.objects.filter(category__in=FREE_PREVIEW_PREMIUM_CATEGORIES).values_list('id', flat=True)
        )
        combined = [('free', pk) for pk in long_free_ids] + [('premium', pk) for pk in free_preview_ids]
        random.shuffle(combined)
        questions += [{'id': pk, 'model': model_tag} for model_tag, pk in combined[:remaining]]

    return questions


def combo_bonus_for_streak(combo):
    """Bonus points on top of the base per-question points: a flat +1 once
    the consecutive-correct streak reaches 3, no further escalation no
    matter how long the streak runs (simplified from the old tiered
    2:+1/5:+2/7:+3 escalation)."""
    return 1 if combo >= 3 else 0


def _model_for(question_model_tag):
    return ThankJapanPremium if question_model_tag == 'premium' else ThankJapanModel


def _tiles_for_question(question_obj, force_chunk=False, want_tutorial_order=False):
    """Shuffled display tiles + the correct answer text for one question
    (works for both ThankJapanModel and ThankJapanPremium - both have
    jpname/example_sentence_ja). force_chunk: see build_tiles - used for
    premium-word review (mode=single) so every premium category is quizzed
    on its example sentence regardless of how short the reading is.

    want_tutorial_order: also return 'tutorial_order', the shuffled-tile
    positions in the order they must be tapped to spell the answer (index 0
    of tutorial_order is the shuffled position holding the first correct
    tile, and so on). Built from an index permutation rather than matching
    tile *text* so it stays correct even when two tiles share the same
    text (e.g. a repeated mora). Only ever requested for the first-taste
    tutorial's first question (see game_question_data) - giving away the
    solved order anywhere else would defeat the point of the game."""
    built = build_tiles(question_obj.jpname, question_obj.example_sentence_ja, force_chunk=force_chunk)
    original = built['tiles']
    order = list(range(len(original)))
    random.shuffle(order)
    # A shuffle landing back on the solved order isn't a bug, but for 3+
    # tiles it's rare and a bit anticlimactic - reshuffle once if it happens.
    if len(order) > 2 and order == list(range(len(original))):
        random.shuffle(order)
    shuffled = [original[i] for i in order]
    result = {'route': built['route'], 'tiles': shuffled, 'answer_text': built['answer_text']}
    if want_tutorial_order:
        # order[shuffled_pos] = original_index. We want, for each
        # original_index in solve order (0, 1, 2, ...), which shuffled_pos
        # holds it - i.e. order's inverse.
        result['tutorial_order'] = [order.index(i) for i in range(len(original))]
    return result

def get_current_player_info(request):
    if request.user.is_authenticated:
        player, _ = Player.objects.get_or_create(username=request.user.username)
        return player, False
    
    temp_score = request.session.get('game_score', 0)
    player = Player(username='Guest', country='Guestland', total_score=temp_score)
    
    return player, True


#game_start_play

def game_start(request):
    player, is_guest = get_current_player_info(request)

    premium_url_name, lang_code = get_lang_info(request)

    return render(request, 'thank_japan_app/game_start-v2.html', {
        'player': player,
        'is_guest': is_guest,
        'lang_code': lang_code,
        'premium_url_name': premium_url_name,
        'is_twa': is_android_twa(request),
        'bgm_url': get_bgm_url('quiz_menu'),
        'bgm_page_type': 'quiz_menu',
        # Gates the tutorial finger-pointing hint on the PLAY button - only a
        # true first-time visitor who hasn't finished a game yet needs the
        # nudge (same tj_has_played cookie the top page's own CTA uses).
        'has_played': request.COOKIES.get('tj_has_played') == '1',
    })



def game_play(request):
    """Renders the game shell once per game (countdown + tile-board markup).
    Question data itself is fetched via game_question_data right after the
    3-2-1 countdown finishes - see game_play-v2.html."""
    player, is_guest = get_current_player_info(request)
    premium_url_name, lang_code = get_lang_info(request)
    questions = request.session.get('game_question_ids', [])
    index = request.session.get('game_current_index', 0)

    if not questions or index >= len(questions):
        return redirect('game_start')

    difficulty = request.session.get('game_difficulty', 'timeattack')
    fire_game_start_event = request.session.pop('ga_fire_game_start', False)
    point_balance = request.user.profile.total_score if request.user.is_authenticated else 0

    return render(request, 'thank_japan_app/game_play-v2.html', {
        'player': player,
        'is_guest': is_guest,
        'lang_code': lang_code,
        'difficulty': difficulty,
        'skip_countdown': difficulty in ('single', 'first_taste'),
        'has_timer': difficulty not in ('single', 'first_taste'),
        'single_return_url': request.session.get('single_return_url'),
        'is_nav_tutorial_single': request.session.get('nav_tutorial_single_active', False),
        'score': request.session.get('game_score', 0),
        'bgm_url': get_bgm_url('game'),
        'bgm_page_type': 'game',
        'top_bgm_url': get_bgm_url('top'),
        'fire_game_start_event': fire_game_start_event,
        'is_authenticated': request.user.is_authenticated,
        'point_balance': point_balance,
        'time_extend_cost': TIME_EXTEND_COST,
        'time_extend_seconds': TIME_EXTEND_SECONDS,
    })


def game_question_data(request):
    """AJAX: current question's shuffled tiles. Also the one place that
    starts the 60s clock - game_end_time is set the first time this is
    called for a game, i.e. right after the 3-2-1 countdown finishes on the
    client, so the countdown doesn't eat into the 60s."""
    questions = request.session.get('game_question_ids', [])
    index = request.session.get('game_current_index', 0)
    difficulty = request.session.get('game_difficulty', 'timeattack')

    if not questions or index >= len(questions):
        return JsonResponse({'game_over': True, 'reason': 'no_more_questions'})

    seconds_left = None
    if difficulty not in ('single', 'first_taste'):
        current_time = time.time()
        game_end_time = request.session.get('game_end_time')
        if not game_end_time:
            game_end_time = current_time + 60
            request.session['game_end_time'] = game_end_time
        seconds_left = int(game_end_time - current_time)
        if seconds_left <= 0:
            return JsonResponse({'game_over': True, 'reason': 'time_up'})

    q = questions[index]
    model = _model_for(q['model'])
    question = get_object_or_404(model, id=q['id'])
    # Premium word review (mode=single) always quizzes the example sentence,
    # regardless of mora count - the main timed game's routing (any
    # difficulty other than 'single') is untouched.
    force_chunk = difficulty == 'single' and q['model'] == 'premium'
    # Step-by-step tile tutorial only ever runs on the first two questions a
    # brand-new guest sees (see game_play-v2.html) - the solved order is
    # otherwise never sent to the client. Q1 guides to the CORRECT order;
    # Q2 (いぬ, forced above in game_restart) deliberately guides to the
    # WRONG one (below) instead, so the guest sees the "incorrect answer"
    # tile treatment once before the real, unguided game starts.
    # Nav-tutorial step 6 (see NAV_TUTORIAL_TARGET_SLUG / game_restart):
    # single mode is always exactly one question, so this is equivalent to
    # is_tutorial_q1's own first_taste guided-correct-order treatment - same
    # tile finger-guide and wrong-tap blocking, just triggered by a
    # different session flag instead of difficulty.
    is_nav_tutorial_q = difficulty == 'single' and index == 0 and request.session.get('nav_tutorial_single_active', False)
    is_tutorial_q1 = (difficulty == 'first_taste' and index == 0) or is_nav_tutorial_q
    is_tutorial_q2 = difficulty == 'first_taste' and index == 1
    tile_data = _tiles_for_question(question, force_chunk=force_chunk, want_tutorial_order=is_tutorial_q1 or is_tutorial_q2)
    tutorial_wrong_order = None
    if is_tutorial_q2 and tile_data.get('tutorial_order'):
        # The reverse of the correct solve order - guaranteed to actually
        # be wrong for いぬ specifically (い != ぬ, so reversing a 2-tile
        # word can't accidentally reconstruct the same string the way it
        # could for a word with a repeated mora).
        tutorial_wrong_order = list(reversed(tile_data['tutorial_order']))

    # Cache the canonical answer text computed *right now* against this
    # exact index, so game_submit_answer validates against the very same
    # string the tiles shown here were built from - instead of recomputing
    # build_tiles() a second time. This isn't just an optimization: it
    # closes off an entire class of "shown tiles don't match what scoring
    # expects" bugs if the two computations were ever to disagree (e.g. a
    # tile_split.py change deployed between the two calls, or any future
    # non-determinism in the tokenizer) - there's only one computation now.
    request.session['current_answer_index'] = index
    request.session['current_answer_text'] = tile_data['answer_text']

    return JsonResponse({
        'game_over': False,
        'index': index + 1,
        'question_id': question.id,
        'question_model': q['model'],
        'route': tile_data['route'],
        'tiles': tile_data['tiles'],
        'tutorial_order': tile_data.get('tutorial_order') if is_tutorial_q1 else None,
        'tutorial_wrong_order': tutorial_wrong_order,
        'seconds_left': seconds_left,
        'score': request.session.get('game_score', 0),
        'combo': request.session.get('game_combo', 0),
        'image_url': question.image.url if question.image else '',
        'caption': question.englishname,
    })


@require_POST
def game_submit_answer(request):
    """AJAX: submit the player's tile order for the current question,
    score it, append to game_history, and advance game_current_index. The
    correct order is always recomputed server-side from the DB - the
    client's tile order is never trusted for scoring."""
    questions = request.session.get('game_question_ids', [])
    index = request.session.get('game_current_index', 0)

    if not questions or index >= len(questions):
        return JsonResponse({'game_over': True, 'reason': 'no_more_questions'})

    if request.session.get('last_answered_index') == index:
        history = request.session.get('game_history', [])
        last_entry = next((h for h in history if h['index'] == index), None)
        if last_entry:
            return JsonResponse({
                'game_over': False,
                'is_correct': last_entry['is_correct'],
                'points': last_entry['points'],
                'combo': last_entry['combo'],
                'correct_answer': last_entry['correct_answer'],
                'score': request.session.get('game_score', 0),
            })

    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'bad_request'}, status=400)

    submitted_tiles = body.get('tiles', [])
    q = questions[index]
    model = _model_for(q['model'])
    question = get_object_or_404(model, id=q['id'])

    # Validate against the answer text cached when this question's tiles
    # were served (see game_question_data) rather than recomputing
    # build_tiles() here - that guarantees the check is against the exact
    # same string the displayed tiles were cut from. Recompute only as a
    # defensive fallback if that cache is missing (e.g. a replayed/crafted
    # request that skipped game_question_data for this index).
    if request.session.get('current_answer_index') == index:
        answer_text = request.session['current_answer_text']
    else:
        difficulty = request.session.get('game_difficulty', 'timeattack')
        force_chunk = difficulty == 'single' and q['model'] == 'premium'
        answer_text = _tiles_for_question(question, force_chunk=force_chunk)['answer_text']

    submitted_text = ''.join(str(t) for t in submitted_tiles)
    correct_flag = submitted_text == answer_text
    is_premium_question = q['model'] == 'premium'

    # Ground-truth usage counter (see real_usage_report) - only for the
    # actual "game" difficulties, not 'single' (word-review from a detail
    # page, a different intent than "playing the game"). Incremented only
    # here, once a real tile submission has been received and judged.
    # Aggregate-only, no per-user row (see DailyActivitySummary).
    game_difficulty = request.session.get('game_difficulty', 'timeattack')
    if game_difficulty in ('timeattack', 'first_taste'):
        DailyActivitySummary.record(
            activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME,
            is_guest=not request.user.is_authenticated,
            is_correct=correct_flag,
        )

    # "Learned words" history (account settings) - single-word review from a
    # detail page only, and only for logged-in users (see WordReviewLog).
    if game_difficulty == 'single' and correct_flag and request.user.is_authenticated:
        WordReviewLog.record_review(
            user=request.user,
            word=question if q['model'] == 'free' else None,
            premium_word=question if q['model'] == 'premium' else None,
        )

    points = combo = combo_bonus = 0
    if correct_flag:
        base_points = 2 if is_premium_question else 1
        combo = request.session.get('game_combo', 0) + 1
        request.session['game_combo'] = combo
        request.session['game_max_combo'] = max(request.session.get('game_max_combo', 0), combo)
        combo_bonus = combo_bonus_for_streak(combo)
        points = base_points + combo_bonus
        request.session['game_score'] = request.session.get('game_score', 0) + points
    else:
        request.session['game_combo'] = 0

    history = request.session.get('game_history', [])
    history.append({
        'question_id': question.id,
        'question_model': q['model'],
        'index': index,
        'is_correct': correct_flag,
        'user_input': answer_text if correct_flag else submitted_text,
        'correct_answer': answer_text,
        'combo': combo,
        'combo_bonus': combo_bonus,
        'points': points,
    })
    request.session['game_history'] = history
    request.session['last_answered_index'] = index
    request.session['game_current_index'] = index + 1

    response = JsonResponse({
        'game_over': False,
        'is_correct': correct_flag,
        'points': points,
        'combo': combo,
        'correct_answer': answer_text,
        'score': request.session.get('game_score', 0),
    })

    # Nav-tutorial step 7 (see NAV_TUTORIAL_TARGET_SLUG): mark it seen only
    # once the guided round is actually completed correctly - set here
    # rather than at game_restart (unlike tj_has_played) so a guest who
    # starts the multi-page flow and drops off partway through still gets
    # offered it again on a later visit, instead of losing the one-time
    # slot to an attempt they never finished.
    if game_difficulty == 'single' and correct_flag and request.session.pop('nav_tutorial_single_active', False):
        response.set_cookie(
            'tj_nav_tutorial_seen', '1',
            max_age=34560000,
            httponly=True, samesite='Lax', secure=request.is_secure(),
        )

    return response


@require_POST
def game_extend_time(request):
    """AJAX: spend TIME_EXTEND_COST points to add TIME_EXTEND_SECONDS to
    the running game clock, when the "time's up" modal's extend option is
    chosen (see game_play-v2.html). Guests can't reach this - the modal
    only offers a registration CTA for them - but it's still validated
    server-side rather than trusted from the client either way. Can be
    called any number of times in one game; the player's own point balance
    is what naturally limits repeat use, not a counter.

    The point deduction is a single conditional UPDATE (only succeeds if
    the balance is still >= cost at the moment it runs) so two concurrent
    requests - two tabs, or a fast double-tap - can't both succeed and
    push the balance negative; PositiveIntegerField would reject that at
    the DB level anyway, but the conditional query avoids relying on that
    as the only guard.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'reason': 'not_authenticated'}, status=403)

    questions = request.session.get('game_question_ids', [])
    index = request.session.get('game_current_index', 0)
    difficulty = request.session.get('game_difficulty', 'timeattack')
    if not questions or index >= len(questions) or difficulty in ('single', 'first_taste'):
        return JsonResponse({'success': False, 'reason': 'no_active_timed_game'}, status=400)

    updated = Profile.objects.filter(
        user=request.user, total_score__gte=TIME_EXTEND_COST
    ).update(total_score=F('total_score') - TIME_EXTEND_COST)

    profile = Profile.objects.get(user=request.user)
    if not updated:
        return JsonResponse({'success': False, 'reason': 'insufficient_points', 'balance': profile.total_score})

    player = Player.objects.filter(username=request.user.username).first()
    if player:
        player.total_score = profile.total_score
        player.save()

    current_time = time.time()
    game_end_time = request.session.get('game_end_time') or current_time
    game_end_time += TIME_EXTEND_SECONDS
    request.session['game_end_time'] = game_end_time

    return JsonResponse({
        'success': True,
        'balance': profile.total_score,
        'seconds_left': max(0, int(game_end_time - current_time)),
    })


def game_restart(request):
    """Starts a new game. Three shapes:
    - mode=single: practice one specific word (from a word-detail page deep
      link) - untimed, no countdown.
    - difficulty=first_taste: the first-visit 3-question guest trial - all
      EASY-tier short words, untimed, no countdown (same reasoning as
      single: it's a low-pressure taster, not the real time attack).
    - anything else: the tile-rearrangement time attack, auto-progressing
      EASY -> NORMAL/HARD -> PREMIUM/long-words (see
      _build_tile_game_questions). There's no difficulty picker anymore;
      the old ?difficulty= tiers (kanji1/kanji2/super_hard/N5-N4-N3 etc.)
      are retired along with the multiple-choice UI they were built for.
    """
    mode = request.GET.get('mode')
    difficulty_param = request.GET.get('difficulty')
    player, is_guest = get_current_player_info(request)

    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    if mode == 'single':
        model_type = request.GET.get('model_type')
        val = request.GET.get('slug')

        if model_type == 'premium':
            question = get_object_or_404(ThankJapanPremium, slug=val)

            if question.category not in ["DailyConversation", "slang", "TourismEtiquette", "Entertainment", "RomanceRelationships"]:
                if not is_premium:
                    free_sample_ids = ThankJapanPremium.objects.filter(
                        category=question.category
                    ).order_by('-timestamp').values_list('id', flat=True)[:5]

                    if question.id not in free_sample_ids:
                        url_name, _ = get_lang_info(request)
                        return redirect(url_name)

            questions = [{'id': question.id, 'model': 'premium'}]
            single_return_url = reverse('detail_premium', kwargs={'category': question.category, 'slug': question.slug})
        else:
            try:
                question = ThankJapanModel.objects.get(slug=val)
            except ThankJapanModel.DoesNotExist:
                question = get_object_or_404(ThankJapanModel, name=val)
            questions = [{'id': question.id, 'model': 'free'}]
            single_return_url = reverse('category_detail', kwargs={'category': question.category, 'slug': question.slug})

        game_difficulty = 'single'
        # Nav-tutorial step 6 (see NAV_TUTORIAL_TARGET_SLUG): the "practice
        # this word" button on つけ麺's detail page is the same generic
        # single-mode link every word's detail page uses - there's no
        # separate tutorial-only URL. Recognizing it here, by slug + the
        # same guest-eligibility gate every other step in this tutorial
        # checks, is what turns this one specific practice round into a
        # guided one (tile finger-guide, wrong-tap blocking, auto-return to
        # top on success) without touching that shared button at all.
        is_nav_tutorial_single = (
            model_type != 'premium' and val == NAV_TUTORIAL_TARGET_SLUG and _nav_tutorial_eligible(request)
        )
    elif difficulty_param == 'first_taste' and not request.user.is_authenticated and request.COOKIES.get('tj_has_played') != '1':
        # The special first-taste tutorial (forced らーめん Q1, wrong-tap
        # blocking, the result screen's auto-guided flow) is meant for a
        # genuinely brand-new guest exactly once - the top page only ever
        # links here when has_played is False (itself gated to logged-out
        # visitors), and that link disappears for good the moment they
        # click it (game_restart sets tj_has_played below). But nothing
        # previously stopped this URL from being reached a second time
        # directly (a stale bookmark/back-button/cached page, or simply
        # retyping it) - guarded here too so the tutorial mechanics can
        # never fire again once played, and never fire at all for a
        # logged-in user (2026-09-17).
        easy = DIFFICULTY_SETTINGS['easy']
        # Q1 is always らーめん (id=628, 'cook') - a guaranteed-correct,
        # fully-guided first step (see game_play-v2.html: tutorial finger +
        # wrong-tap blocking). Q2 is always いぬ (id=193, 'animal',
        # 2-mora い/ぬ) - a guaranteed-WRONG step, guided to the reversed
        # (ぬ→い) order on purpose, so the guest sees the "incorrect" tile
        # treatment at least once before the real game (2026-09-19). Both
        # fall back to the normal random pick if the exact word's ever
        # renamed/removed, so the flow never breaks.
        ramen = ThankJapanModel.objects.filter(jpname='らーめん').first()
        inu = ThankJapanModel.objects.filter(jpname='いぬ').first()
        forced = [w.id for w in (ramen, inu) if w]
        remaining = _sample_easy_ids(easy['category_filter'], exclude_ids=forced, count=FIRST_TASTE_QUESTION_COUNT - len(forced))
        ids = forced + remaining
        questions = [{'id': pk, 'model': 'free'} for pk in ids]
        game_difficulty = 'first_taste'
    else:
        questions = _build_tile_game_questions(is_premium)
        game_difficulty = 'timeattack'

    keys_to_clear = [
        'game_question_ids', 'game_current_index', 'game_score', 'game_difficulty',
        'game_history', 'score_saved', 'achievements_checked',
        'game_end_time', 'last_answered_index',
        'game_combo', 'game_max_combo', 'single_return_url',
        'nav_tutorial_single_active',
    ]
    for key in keys_to_clear:
        request.session.pop(key, None)

    request.session['game_question_ids'] = questions
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0
    request.session['game_difficulty'] = game_difficulty
    request.session['game_history'] = []
    if mode == 'single':
        # Lets game_play's "finish review" button return the player to the
        # exact word-detail page they came from, instead of the normal
        # timed-game result screen (single is a one-question practice
        # round, not a real playthrough).
        request.session['single_return_url'] = single_return_url
        if is_nav_tutorial_single:
            request.session['nav_tutorial_single_active'] = True
    # Consumed once by game_play to fire the GA4 'game_start' event, so it fires
    # exactly once per new game rather than on every question fetch.
    request.session['ga_fire_game_start'] = True

    _, lang_code = get_lang_info(request)
    response = redirect(f"{reverse('game_play')}?lang={lang_code}")

    # First-visit taste flow from the top page: marks the visitor as a
    # returning player as soon as they click "try it", so leaving mid-flow
    # still shows the normal play button on their next visit (see
    # TopView.get_context_data). Keyed on the actual game_difficulty that
    # was granted, not the raw URL param - a first_taste request that got
    # silently downgraded to a normal game above (already played, or
    # logged in) shouldn't need this re-set anyway.
    if game_difficulty == 'first_taste':
        response.set_cookie(
            'tj_has_played', '1',
            max_age=34560000,  # ~400 days: the practical cap most browsers honor
            httponly=True, samesite='Lax', secure=request.is_secure(),
        )

    return response


# --- Daily Question (今日の一問) ---

DAILY_QUESTION_SESSION_ANSWERED_KEY = 'daily_question_answered_date'
DAILY_QUESTION_BANNER_DISMISSED_KEY = 'daily_question_banner_dismissed'
DAILY_QUESTION_TIME_LIMIT = 30  # seconds - a single question doesn't need the main game's 60s

DAILY_QUESTION_SHARE_LOCALE_ALIAS = {'es-mx': 'es-es', 'pt-br': 'pt', 'en-in': 'en'}

# 2026-09-12: share text redesigned around a word-centric "assembly process"
# reveal (mirrors the notification titles below for the heading) rather
# than the previous "I got it right/wrong" framing - the same text is used
# whether the player answered correctly or not (see _daily_question_share_text),
# since inviting friends to comment Yes/No about their own attempt reads
# fine either way without singling out a personal result.
DAILY_QUESTION_SHARE_HEADING = {
    'ja': '今日の一問',
    'zh-hant': '今日一題',
    'zh-cn': '今日一题',
    'ko': '오늘의 한 문제',
    'de': 'Deine Frage des Tages',
    'fr': 'La question du jour',
    'es-es': 'Tu pregunta del día',
    'it': 'La tua domanda del giorno',
    'pt': 'A sua pergunta do dia',
    'vi': 'Câu hỏi hôm nay',
    'th': 'คำถามประจำวันของคุณ',
    'en': "Today's Daily Question",
}

DAILY_QUESTION_SHARE_INVITE = {
    'ja': '正解できた人は「Yes」、できなかった人は「No」って教えてください！',
    'zh-hant': '答對的人請留言「Yes」，答錯的人請留言「No」！',
    'zh-cn': '答对的人请留言「Yes」，答错的人请留言「No」！',
    'ko': '정답을 맞힌 사람은 "Yes", 틀린 사람은 "No"라고 댓글 남겨주세요!',
    'de': 'Wer\'s richtig hatte, schreibt "Yes" – wer nicht, schreibt "No"!',
    'fr': 'Si tu as trouvé, commente "Yes" – sinon, commente "No" !',
    'es-es': 'Si acertaste, comenta "Yes"; si no, comenta "No"!',
    'it': 'Se hai indovinato, commenta "Yes" – altrimenti "No"!',
    'pt': 'Se acertou, comente "Yes" – se não, comente "No"!',
    'vi': 'Nếu trả lời đúng, hãy bình luận "Yes" – nếu sai thì "No"!',
    'th': 'ถ้าตอบถูกคอมเมนต์ "Yes" ถ้าตอบผิดคอมเมนต์ "No" นะ!',
    'en': 'Got it right? Comment "Yes"! Got it wrong? Comment "No"!',
}

DAILY_QUESTION_SHARE_CTA = {
    'ja': 'あなたも挑戦してみて👇',
    'zh-hant': '你也來挑戰看看👇',
    'zh-cn': '你也来挑战看看👇',
    'ko': '당신도 도전해보세요👇',
    'de': "Versuch's auch mal👇",
    'fr': 'Essaie toi aussi👇',
    'es-es': '¡Pruébalo tú también!👇',
    'it': 'Prova anche tu👇',
    'pt': 'Experimente você também👇',
    'vi': 'Bạn cũng thử xem nhé👇',
    'th': 'ลองท้าทายดูสิ👇',
    'en': 'Try it yourself too👇',
}

# Chunk-route words (long premium example-sentence phrases) would make the
# tweet too long if every chunk were spelled out arrow-by-arrow, so they get
# a step-count summary instead of the mora route's full す→し→寿司 reveal.
DAILY_QUESTION_SHARE_CHUNK_STEPS = {
    'ja': '{n}ステップで完成！',
    'zh-hant': '{n}個步驟完成！',
    'zh-cn': '{n}个步骤完成！',
    'ko': '{n}단계로 완성!',
    'de': 'In {n} Schritten fertig!',
    'fr': 'Terminé en {n} étapes !',
    'es-es': '¡Completado en {n} pasos!',
    'it': 'Completato in {n} passaggi!',
    'pt': 'Completo em {n} passos!',
    'vi': 'Hoàn thành trong {n} bước!',
    'th': 'เสร็จใน {n} ขั้นตอน!',
    'en': 'Completed in {n} steps!',
}

DAILY_QUESTION_SHARE_ANSWER_LABEL = {
    'ja': '正解',
    'zh-hant': '答案',
    'zh-cn': '答案',
    'ko': '정답',
    'de': 'Lösung',
    'fr': 'Réponse',
    'es-es': 'Respuesta',
    'it': 'Risposta',
    'pt': 'Resposta',
    'vi': 'Đáp án',
    'th': 'คำตอบ',
    'en': 'Answer',
}


def _daily_question_word_detail_url(word, lang_code, share_date):
    """Permanent detail-page URL for today's word - the X share button's
    link target (2026-09-12), replacing the old dated /daily/ URL. Rationale:
    /daily/ rotates to a new word every day, so an old tweet's card would
    keep showing (via X's per-URL OGP cache) the word/image from the day it
    was posted while the link itself led to whatever word is live *now* - a
    permanent mismatch for anyone clicking days later. The word's own detail
    page never changes what it shows, so the link target stays correct
    forever with no date-stamping needed for *navigation* purposes.

    2026-09-15 fix: a bare permanent URL reintroduces the exact OGP-caching
    bug the dated /daily/ URL was built to avoid (see the 2026-09-04 fix
    referenced in project memory) - if this exact word/detail URL was ever
    crawled by X at a time its og:image was missing/broken (template bug,
    empty image field, etc, at any point in this URL's history), X keeps
    serving that broken cached card forever, even though the page is 100%
    correct now. Appending today's date as a harmless query param (Django
    ignores unknown params, so the page/content shown is unaffected) makes
    each day's share a syntactically distinct URL, so a stale per-URL cache
    from the past can never stick around - every day's share gets its own
    fresh crawl. Unlike /daily/'s own dated URL, this doesn't reflect the
    *word* changing (the word's own page never changes) - it's purely a
    cache-buster for X's crawler, so the plain undated path keeps working
    correctly for anyone who bookmarks/revisits it later."""
    if isinstance(word, ThankJapanPremium):
        path = reverse('detail_premium', kwargs={'category': word.category, 'slug': word.slug})
    else:
        path = reverse('category_detail', kwargs={'category': word.category, 'slug': word.slug})
    return f"https://www.thankjapan.com{path}?lang={lang_code}&scb={share_date.isoformat()}"


def _daily_question_assembly_process(word):
    """The '組み立て過程' line: mora-route words spell out every tile
    (す→し→寿司), chunk-route words (long premium phrases) collapse to a
    step count instead so the tweet doesn't balloon in length. Always
    reflects the correct order, regardless of what the player submitted -
    this is a reveal of today's answer, not a replay of their attempt."""
    tile_data = build_tiles(word.jpname, word.example_sentence_ja)
    if tile_data['route'] == 'mora':
        final_display = word.kanji_name or word.jpname
        return '→'.join(tile_data['tiles'] + [final_display])
    return len(tile_data['tiles'])  # caller formats this into the step-count template


def _daily_question_share_text(lang_code, word):
    lang = DAILY_QUESTION_SHARE_LOCALE_ALIAS.get(lang_code, lang_code)
    heading = DAILY_QUESTION_SHARE_HEADING.get(lang, DAILY_QUESTION_SHARE_HEADING['en'])
    invite = DAILY_QUESTION_SHARE_INVITE.get(lang, DAILY_QUESTION_SHARE_INVITE['en'])
    cta = DAILY_QUESTION_SHARE_CTA.get(lang, DAILY_QUESTION_SHARE_CTA['en'])
    answer_label = DAILY_QUESTION_SHARE_ANSWER_LABEL.get(lang, DAILY_QUESTION_SHARE_ANSWER_LABEL['en'])

    process = _daily_question_assembly_process(word)
    if isinstance(process, int):
        step_template = DAILY_QUESTION_SHARE_CHUNK_STEPS.get(lang, DAILY_QUESTION_SHARE_CHUNK_STEPS['en'])
        process = step_template.format(n=process)

    return f"{heading}\n\n{process}({answer_label})🀄\n\n{invite}\n\n{cta}"


DAILY_QUESTION_HASHTAG_TERM = {
    'ja': '今日の一問',
    'zh-hant': '今日一題',
    'zh-cn': '今日一题',
    'ko': '오늘의한문제',
    'de': 'FrageDesTages',
    'fr': 'QuestionDuJour',
    'es-es': 'PreguntaDelDia',
    'it': 'DomandaDelGiorno',
    'pt': 'PerguntaDoDia',
    'vi': 'CauHoiHomNay',
    'th': 'คำถามประจำวัน',
    'en': 'DailyQuestion',
}


def _daily_question_hashtags(lang_code, word):
    lang = DAILY_QUESTION_SHARE_LOCALE_ALIAS.get(lang_code, lang_code)
    term = DAILY_QUESTION_HASHTAG_TERM.get(lang, DAILY_QUESTION_HASHTAG_TERM['en'])
    word_tag = ''.join(word.englishname.split())  # hashtags can't contain spaces
    tags = ['ThankJapan', term]
    if word_tag:
        tags.append(word_tag)
    return ','.join(tags)


def _daily_question_tiles(request, word, today):
    """Build shuffled tiles for today's word, session-caching the canonical
    answer text for the day so a page reload before answering can reshuffle
    freely without ever disagreeing with what POST validates against (same
    pattern as the main game's game_question_data/game_submit_answer)."""
    answer_key = f'daily_answer_{today.isoformat()}'
    tile_data = _tiles_for_question(word)
    request.session[answer_key] = tile_data['answer_text']
    return tile_data


def daily_question_view(request):
    premium_url_name, lang_code = get_lang_info(request)
    top_page_url = f"/?lang={lang_code}"
    today = timezone.localdate()  # TIME_ZONE='Asia/Tokyo' -> JST "today"
    # Logged-in users are gated on a DB-backed, user-identity-keyed field
    # rather than the session: the "1 per day" limit must hold regardless of
    # which language/entry-point the user is on, and a session cookie isn't
    # guaranteed to be the same across those (e.g. iOS gives each distinct
    # "Add to Home Screen" PWA install its own isolated cookie jar even for
    # the same origin, which let a user re-answer after switching language -
    # 2026-09-17). Guests have no identity to key off besides the session,
    # so they keep the original (best-effort) session check.
    if request.user.is_authenticated:
        already_answered = request.user.profile.last_daily_question_answered_date == today
    else:
        already_answered = request.session.get(DAILY_QUESTION_SESSION_ANSWERED_KEY) == today.isoformat()

    # X/Twitter (and other social crawlers) cache OGP data per-URL, not
    # per-response - a fixed /daily/ URL kept showing whatever word's image
    # was cached from the first time it got crawled, days later, regardless
    # of which word is actually live now. Stamping today's date onto the
    # URL makes each day a distinct URL, so there's nothing stale to serve:
    # a URL nobody has shared/crawled yet always gets a fresh fetch. This is
    # the URL actually embedded in the X share link below and echoed back as
    # og:url, so what a crawler fetches and what og:url claims always agree.
    daily_url = f"https://www.thankjapan.com/daily/?lang={lang_code}&date={today.isoformat()}"

    daily_question, _ = DailyQuestion.objects.get_or_create_for_date(today)

    if daily_question is None:
        return render(request, 'thank_japan_app/daily_question.html', {
            'daily_state': 'unavailable',
            'lang_code': lang_code,
            'top_page_url': top_page_url,
            'daily_url': daily_url,
        })

    word = daily_question.selected_word

    if request.method == 'POST':
        if already_answered:
            return render(request, 'thank_japan_app/daily_question.html', {
                'daily_state': 'already_answered',
                'lang_code': lang_code,
                'top_page_url': top_page_url,
                'object': word,
                'daily_url': daily_url,
            })

        submitted_tiles_raw = request.POST.get('answer_tiles', '[]')
        try:
            submitted_tiles = json.loads(submitted_tiles_raw)
        except (TypeError, ValueError):
            submitted_tiles = []
        submitted_text = ''.join(t for t in submitted_tiles if isinstance(t, str))

        answer_key = f'daily_answer_{today.isoformat()}'
        correct_answer_text = request.session.get(answer_key)
        if correct_answer_text is None:
            # Cache missed (e.g. direct POST without a prior GET) - fall
            # back to a fresh recompute, same defensive pattern as
            # game_submit_answer.
            correct_answer_text = build_tiles(word.jpname, word.example_sentence_ja)['answer_text']
        is_correct = submitted_text == correct_answer_text
        request.session[DAILY_QUESTION_SESSION_ANSWERED_KEY] = today.isoformat()

        # Ground-truth usage counter (see real_usage_report) - incremented
        # only here, after a real tile submission has been received and
        # judged, never on a bare page view. Aggregate-only, no per-user
        # row (see DailyActivitySummary).
        DailyActivitySummary.record(
            activity_type=DailyActivitySummary.ACTIVITY_DAILY_QUESTION,
            is_guest=not request.user.is_authenticated,
            is_correct=is_correct,
        )

        # Guests earn nothing (nowhere to persist it); logged-in users get a
        # flat 1pt for a correct answer, mirroring the scoring pattern in
        # game_result (views.py ~2290) so it shows up in the same ranking/
        # weekly-score displays. The "already answered today" gate above
        # already prevents this from running twice in one day.
        #
        # last_daily_question_answered_date is stamped unconditionally (not
        # just on a correct answer) - same as the session key above, "1 per
        # day" is about having attempted it, not about getting it right.
        if request.user.is_authenticated:
            profile = request.user.profile
            profile.last_daily_question_answered_date = today
            if is_correct:
                profile.total_score += 1
                profile.last_score = 1
                player = Player.objects.filter(username=request.user.username).first()
                if player:
                    player.total_score = profile.total_score
                    player.save()
                week_start = WeeklyScore.get_current_week_start()
                weekly_record, _ = WeeklyScore.objects.get_or_create(user=request.user, week_start=week_start)
                weekly_record.score += 1
                weekly_record.save()
            profile.save()

        share_text = _daily_question_share_text(lang_code, word)
        share_hashtags = _daily_question_hashtags(lang_code, word)
        share_url = _daily_question_word_detail_url(word, lang_code, today)

        return render(request, 'thank_japan_app/daily_question.html', {
            'daily_state': 'result',
            'is_correct': is_correct,
            'object': word,
            'share_text': share_text,
            'share_url': share_url,
            'share_hashtags': share_hashtags,
            'lang_code': lang_code,
            'top_page_url': top_page_url,
            'is_twa': is_android_twa(request),
            'daily_url': daily_url,
        })

    if already_answered:
        return render(request, 'thank_japan_app/daily_question.html', {
            'daily_state': 'already_answered',
            'lang_code': lang_code,
            'top_page_url': top_page_url,
            'object': word,
            'daily_url': daily_url,
        })

    tile_data = _daily_question_tiles(request, word, today)

    return render(request, 'thank_japan_app/daily_question.html', {
        'daily_state': 'question',
        'object': word,
        'tiles_json': json.dumps(tile_data['tiles']),
        'time_limit': DAILY_QUESTION_TIME_LIMIT,
        'lang_code': lang_code,
        'daily_url': daily_url,
        'top_page_url': top_page_url,
        'is_twa': is_android_twa(request),
    })


COMBO_SHARE_ACHIEVEMENTS = {
    'high': {
        'ja': '🔥{max_combo}連続正解達成！ThankJapanの60秒チャレンジで{score}pt獲得！',
        'zh-hant': '🔥達成{max_combo}連續正確！在ThankJapan的60秒挑戰中獲得{score}分！',
        'zh-cn': '🔥达成{max_combo}连续正确！在ThankJapan的60秒挑战中获得{score}分！',
        'ko': '🔥{max_combo}연속 정답 달성! ThankJapan의 60초 챌린지에서 {score}점 획득!',
        'de': '🔥{max_combo} Treffer in Folge! {score} Punkte in der ThankJapan 60-Sekunden-Challenge!',
        'fr': "🔥{max_combo} bonnes réponses d'affilée ! {score} points dans le Défi 60 secondes de ThankJapan !",
        'es-es': '🔥¡{max_combo} aciertos seguidos! ¡{score} puntos en el Reto de 60 segundos de ThankJapan!',
        'it': '🔥{max_combo} risposte corrette di fila! {score} punti nella Sfida di 60 secondi di ThankJapan!',
        'pt': '🔥{max_combo} acertos seguidos! {score} pontos no Desafio de 60 segundos do ThankJapan!',
        'vi': '🔥Đạt {max_combo} câu đúng liên tiếp! Ghi {score} điểm trong Thử thách 60 giây của ThankJapan!',
        'th': '🔥ทำได้ {max_combo} คอมโบติดต่อกัน! ได้ {score} คะแนนใน 60-Second Challenge ของ ThankJapan!',
        'en': '🔥{max_combo}-combo streak! Scored {score}pts in the 60-Second Challenge on ThankJapan!',
    },
    'mid': {
        'ja': '⚡最高コンボ{max_combo}達成！ThankJapanの60秒チャレンジで{score}pt獲得！',
        'zh-hant': '⚡最高連擊{max_combo}！在ThankJapan的60秒挑戰中獲得{score}分！',
        'zh-cn': '⚡最高连击{max_combo}！在ThankJapan的60秒挑战中获得{score}分！',
        'ko': '⚡최대 콤보 {max_combo}! ThankJapan의 60초 챌린지에서 {score}점 획득!',
        'de': '⚡Maximal-Combo {max_combo}! {score} Punkte in der ThankJapan 60-Sekunden-Challenge!',
        'fr': '⚡Combo max {max_combo} ! {score} points dans le Défi 60 secondes de ThankJapan !',
        'es-es': '⚡¡Combo máximo {max_combo}! ¡{score} puntos en el Reto de 60 segundos de ThankJapan!',
        'it': '⚡Combo massima {max_combo}! {score} punti nella Sfida di 60 secondi di ThankJapan!',
        'pt': '⚡Combo máximo {max_combo}! {score} pontos no Desafio de 60 segundos do ThankJapan!',
        'vi': '⚡Combo tối đa {max_combo}! Ghi {score} điểm trong Thử thách 60 giây của ThankJapan!',
        'th': '⚡คอมโบสูงสุด {max_combo}! ได้ {score} คะแนนใน 60-Second Challenge ของ ThankJapan!',
        'en': '⚡Max combo {max_combo}! Scored {score}pts in the 60-Second Challenge on ThankJapan!',
    },
    'none': {
        'ja': 'ThankJapanの60秒チャレンジで{score}pt獲得！日本語、どれだけ知ってる？',
        'zh-hant': '在ThankJapan的60秒挑戰中獲得{score}分！你懂多少日文？',
        'zh-cn': '在ThankJapan的60秒挑战中获得{score}分！你懂多少日文？',
        'ko': 'ThankJapan의 60초 챌린지에서 {score}점 획득! 일본어 얼마나 아세요?',
        'de': '{score} Punkte in der ThankJapan 60-Sekunden-Challenge! Wie viel Japanisch kannst du?',
        'fr': 'Défi 60 secondes de ThankJapan : {score} points ! Et toi, combien de japonais connais-tu ?',
        'es-es': '¡{score} puntos en el Reto de 60 segundos de ThankJapan! ¿Cuánto japonés sabes tú?',
        'it': '{score} punti nella Sfida di 60 secondi di ThankJapan! Quanto giapponese conosci?',
        'pt': '{score} pontos no Desafio de 60 segundos do ThankJapan! Quanto japonês você sabe?',
        'vi': 'Ghi {score} điểm trong Thử thách 60 giây của ThankJapan! Bạn biết bao nhiêu tiếng Nhật?',
        'th': 'ได้ {score} คะแนนใน 60-Second Challenge ของ ThankJapan! คุณรู้ภาษาญี่ปุ่นแค่ไหน?',
        'en': 'Scored {score}pts in the 60-Second Challenge on ThankJapan! How much Japanese do you know?',
    },
}

COMBO_SHARE_CTA = {
    'ja': '挑戦してみて！🎮',
    'zh-hant': '來挑戰看看吧！🎮',
    'zh-cn': '来挑战看看吧！🎮',
    'ko': '도전해 보세요! 🎮',
    'de': 'Trau dich! 🎮',
    'fr': 'À toi de jouer ! 🎮',
    'es-es': '¡Atrévete! 🎮',
    'it': 'Provaci anche tu! 🎮',
    'pt': 'Bora tentar! 🎮',
    'vi': 'Thử sức xem nào! 🎮',
    'th': 'มาลองดูสิ! 🎮',
    'en': 'Can you beat me? 🎮',
}


def build_combo_share_message(lang_code, max_combo, score):
    lang = {'es-mx': 'es-es', 'pt-br': 'pt', 'en-in': 'en'}.get(lang_code, lang_code)
    # 'mid' now starts where the combo bonus actually kicks in (3), matching
    # combo_bonus_for_streak - 'high' (7+) stays as an aspirational streak
    # length unrelated to the (now flat) scoring bonus.
    tier = 'high' if max_combo >= 7 else ('mid' if max_combo >= 3 else 'none')
    achievement = COMBO_SHARE_ACHIEVEMENTS[tier].get(lang, COMBO_SHARE_ACHIEVEMENTS[tier]['en'])
    cta = COMBO_SHARE_CTA.get(lang, COMBO_SHARE_CTA['en'])
    return f"{achievement.format(max_combo=max_combo, score=score)} {cta}"


def game_result(request):
    _, lang_code = get_lang_info(request)

    raw_history = request.session.get('game_history', [])

    deduped_dict = {h.get('index'): h for h in raw_history}
    history = list(deduped_dict.values())

    total_played = len(history)
    correct_count = sum(1 for h in history if h.get('is_correct'))

    # A game can now mix free and premium questions (Q1-4 are always free,
    # Q5+ is premium content for subscribers) - this is just "did this game
    # include any premium content", not a whole-game mode flag like before.
    is_premium_mode = any(h.get('question_model') == 'premium' for h in history)
    combo_bonus_total = sum(h.get('combo_bonus', 0) for h in history if h.get('is_correct'))
    max_combo = max((h.get('combo', 0) for h in history), default=0)
    score = sum(h.get('points', 0) for h in history)

    player, is_guest = get_current_player_info(request)
    difficulty = request.session.get('game_difficulty')

    if not request.session.get('score_saved', False) and score > 0:
        if not is_guest and request.user.is_authenticated:
            profile = request.user.profile
            profile.total_score += score
            profile.last_score = score
            profile.save()
            player.total_score = profile.total_score
            player.save()
            week_start = WeeklyScore.get_current_week_start()
            weekly_record, _ = WeeklyScore.objects.get_or_create(user=request.user, week_start=week_start)
            weekly_record.score += score
            weekly_record.save()
        request.session['score_saved'] = True

    unlocked_achievements = []
    if not request.session.get('achievements_checked', False) and total_played > 0:
        if not is_guest and request.user.is_authenticated:
            profile = request.user.profile
            profile.games_played += 1
            if max_combo > profile.best_combo:
                profile.best_combo = max_combo
            profile.save()
            unlocked_achievements = check_and_unlock_achievements(profile)
        request.session['achievements_checked'] = True

    player_global_rank = None
    total_registered = 0
    if not is_guest:
        registered_players = Player.objects.exclude(username__icontains="Guest")
        total_registered = registered_players.count()
        higher_scores_count = registered_players.filter(total_score__gt=player.total_score).count()
        player_global_rank = higher_scores_count + 1
    
    # History can mix free and premium question ids, so look each up in its
    # own table and key by (model, id) - a free id and a premium id can
    # collide numerically since they're different tables.
    free_ids = [h['question_id'] for h in history if h.get('question_model', 'free') != 'premium']
    premium_ids = [h['question_id'] for h in history if h.get('question_model') == 'premium']
    played_questions = {('free', pk): obj for pk, obj in ThankJapanModel.objects.in_bulk(free_ids).items()}
    played_questions.update({('premium', pk): obj for pk, obj in ThankJapanPremium.objects.in_bulk(premium_ids).items()})
    review_data = [
        {'object': played_questions.get((h.get('question_model', 'free'), h['question_id'])),
         'is_correct': h['is_correct'], 'user_input': h['user_input'], 'correct_answer': h.get('correct_answer')}
        for h in history if played_questions.get((h.get('question_model', 'free'), h['question_id']))
    ]

    ranking = Player.objects.exclude(username__icontains="Guest").order_by('-total_score')[:20]
    current_week = WeeklyScore.get_current_week_start()
    raw_weekly_ranking = WeeklyScore.objects.filter(week_start=current_week).order_by('-score')[:10]
    
    weekly_ranking = []
    last_score, rank_val = None, 0
    for i, r in enumerate(raw_weekly_ranking, 1):
        if r.score != last_score: rank_val = i
        r.display_rank = rank_val
        weekly_ranking.append(r)
        last_score = r.score

    # In-app review prompt: TWA + logged in + at least one correct answer +
    # never shown before. Shown right here on the result screen (not the top
    # page - see the retired review_prompt_status/base_top.html version this
    # replaced). review_prompt_shown is flipped the moment we decide to show
    # it, in this same request, rather than waiting for a follow-up
    # dismiss/complete call - that's what makes "shown at most once ever"
    # unconditional instead of depending on the client actually reporting back.
    show_review_prompt = (
        request.user.is_authenticated and is_android_twa(request) and
        correct_count >= 1 and not request.user.profile.review_prompt_shown
    )
    if show_review_prompt:
        Profile.objects.filter(pk=request.user.profile.pk).update(review_prompt_shown=True)

    response = render(request, 'thank_japan_app/game_result-v2.html', {
        'lang_code': lang_code,
        'player': player,
        'score': score,
        'combo_bonus_total': combo_bonus_total,
        'max_combo': max_combo,
        'combo_gauge_capped': min(max_combo, 3),
        'all_correct': total_played > 0 and correct_count == total_played,
        'share_message': build_combo_share_message(lang_code, max_combo, score),
        'share_url': 'https://www.thankjapan.com',
        'correct_count': correct_count,
        'total_played': total_played,
        'is_guest': is_guest,
        'review_data': review_data,
        'difficulty': difficulty,
        'ranking': ranking,
        'weekly_ranking': weekly_ranking,
        'current_rank': player_global_rank,
        'total_registered': total_registered,
        'bgm_url': get_bgm_url('top'),
        'bgm_page_type': 'top',
        'is_premium_mode': is_premium_mode,
        'unlocked_achievements': [a['code'] for a in unlocked_achievements],
        'show_review_prompt': show_review_prompt,
    })

    # Reaching this result screen for first_taste means all 3 questions were
    # actually answered (not just started) - see _nav_tutorial_eligible,
    # which this cookie defers until the guest's next visit so the nav
    # tutorial (see NAV_TUTORIAL_TARGET_SLUG) never starts back-to-back with
    # the one that just finished. No max_age: a true browser-session cookie,
    # gone once the browser itself is closed (2026-09-20).
    if difficulty == 'first_taste':
        response.set_cookie(
            'tj_first_taste_just_completed', '1',
            httponly=True, samesite='Lax', secure=request.is_secure(),
        )

    return response
    
    
                                
#category select view

def category_list(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'en')

    return render(request, 'thank_japan_app/category/category_list.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    

def category_list_zhcn(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'zh-cn')

    return render(request, 'thank_japan_app/category/category_list_zh_cn.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
     

def category_list_zhhant(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'zh-hant')

    return render(request, 'thank_japan_app/category/category_list_zh_hant.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    

def category_list_vi(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'vi')

    return render(request, 'thank_japan_app/category/category_list_vi.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    

def category_list_th(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'th')

    return render(request, 'thank_japan_app/category/category_list_th.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    
def category_list_pt(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'pt')

    return render(request, 'thank_japan_app/category/category_list_pt.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    

def category_list_pt_br(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'pt-br')

    return render(request, 'thank_japan_app/category/category_list_pt_br.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    

def category_list_ko(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'ko')

    return render(request, 'thank_japan_app/category/category_list_ko.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
     

def category_list_ja(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'ja')

    return render(request, 'thank_japan_app/category/category_list_ja.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    
 
def category_list_it(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'it')

    return render(request, 'thank_japan_app/category/category_list_it.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    
 
def category_list_fr(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'fr')

    return render(request, 'thank_japan_app/category/category_list_fr.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    

def category_list_es_mx(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'es-mx')

    return render(request, 'thank_japan_app/category/category_list_es_mx.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    

def category_list_es_es(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'es-es')

    return render(request, 'thank_japan_app/category/category_list_es_es.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    
 
def category_list_en_in(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'en-in')

    return render(request, 'thank_japan_app/category/category_list_en_in.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    
 
def category_list_de(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'de')

    return render(request, 'thank_japan_app/category/category_list_de.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
        'nav_tutorial_cook_card_step_active': _nav_tutorial_eligible(request),
    })
    
    
 
 
#category view
                            
class FoodView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/food.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="food").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Food Guide | Popular Dishes, Street Food & Snacks | ThankJapan"
        context['seo_description'] = "Discover iconic Japanese foods like sushi, ramen, and tempura. Learn about their ingredients and cultural roots."
        context['seo_og_title'] = "Japanese Food - Explore Traditional Dishes | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class NatureView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/nature.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="nature").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Explore Japanese Nature | Mountains, Forests & Scenic Views | ThankJapan"
        context['seo_description'] = "Discover the beauty of Japanese nature including mountains, forests, gardens, and scenic landscapes."
        context['seo_og_title'] = "Japanese Nature - Scenic Spots & Natural Wonders | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class FashionView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/fashion.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="fashion").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Fashion | Traditional & Modern Styles | ThankJapan"
        context['seo_description'] = "Explore Japanese fashion, from traditional kimono to modern streetwear and pop culture trends."
        context['seo_og_title'] = "Japanese Fashion - Kimono, Streetwear & Trends | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class CultureView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/culture.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="culture").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Culture | Traditions, Festivals & Customs | ThankJapan"
        context['seo_description'] = "Learn about Japanese culture, including festivals, traditional arts, customs, and heritage."
        context['seo_og_title'] = "Japanese Culture - Festivals, Arts & Traditions | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        # 2026-09-19: culture isn't part of the nav-tutorial's own guided
        # path (see NAV_TUTORIAL_TARGET_SLUG, which lives in 'cook'), but a
        # tutorial-eligible visitor browsing here too would otherwise see
        # this page's own unrelated "first card" tap-guide competing for
        # attention with the ongoing tutorial - simple suppression only,
        # no retargeting (unlike cook.html, nothing here should get guided).
        context['show_nav_tutorial_hint'] = _nav_tutorial_eligible(self.request)
        return context
    
class CookView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/cook.html"
    paginate_by = 200
    bgm_page_type = 'study_select'

    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="cook").order_by('-timestamp')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code

        context['seo_title'] = "Japanese Cooking | Recipes & Culinary Techniques | ThankJapan"
        context['seo_description'] = "Discover Japanese cooking techniques and recipes from traditional dishes to modern cuisine."
        context['seo_og_title'] = "Japanese Cooking - Recipes & Techniques | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        # Nav-tutorial step 4 (see NAV_TUTORIAL_TARGET_SLUG) - targeted by
        # slug within object_list in the template, not position, so it
        # keeps pointing at the right card regardless of grid order.
        # Its own, page-scoped flag - deliberately NOT
        # nav_tutorial_learn_step_active (TopView-only) or
        # nav_tutorial_cook_card_step_active (category_list-only), so this
        # page's flag can never leak into a page it doesn't belong to.
        context['show_nav_tutorial_hint'] = _nav_tutorial_eligible(self.request)
        context['nav_tutorial_target_slug'] = NAV_TUTORIAL_TARGET_SLUG
        return context
    
class AppliancesView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/appliances.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="appliances").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Appliances | Modern & Traditional Devices | ThankJapan"
        context['seo_description'] = "Explore Japanese home appliances, both modern and traditional, and learn how they simplify daily life."
        context['seo_og_title'] = "Japanese Appliances - Innovative Devices & Tools | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class AnimalView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/animal.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="animal").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Animals | Wildlife & Pets | ThankJapan"
        context['seo_description'] = "Learn about animals in Japan, from native wildlife to popular pets and cultural symbolism."
        context['seo_og_title'] = "Japanese Animals - Wildlife & Pets | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class BuildingView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/building.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="building").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Buildings | Architecture & Landmarks | ThankJapan"
        context['seo_description'] = "Explore Japanese architecture, from historic temples and shrines to modern urban buildings."
        context['seo_og_title'] = "Japanese Buildings - Traditional & Modern Architecture | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class FlowerView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/flower.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="flower").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Flowers | Traditional Gardens & Seasonal Blooms | ThankJapan"
        context['seo_description'] = "Discover Japanese flowers and gardens, seasonal blooms, and their cultural significance."
        context['seo_og_title'] = "Japanese Flowers - Gardens & Seasonal Blooms | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class HouseholdItemsView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/householditems.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="householditems").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Household Items | Traditional & Modern Goods | ThankJapan"
        context['seo_description'] = "Explore Japanese household items, including traditional tools and modern gadgets used in everyday life."
        context['seo_og_title'] = "Japanese Household Items - Traditional & Modern Goods | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class SportsView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/sports.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="sports").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Sports | Traditional & Modern Games | ThankJapan"
        context['seo_description'] = "Learn about sports in Japan, from traditional martial arts to modern popular games."
        context['seo_og_title'] = "Japanese Sports - Martial Arts & Modern Games | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
class WorkView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/work.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="work").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Work Culture | Jobs, Professions & Traditions | ThankJapan"
        context['seo_description'] = "Explore Japanese work culture, professions, and workplace traditions throughout history and today."
        context['seo_og_title'] = "Japanese Work Culture - Jobs & Traditions | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
class LiveView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/live.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="live").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Living in Japan | Lifestyle, Housing & Daily Life | ThankJapan"
        context['seo_description'] = "Learn about daily life in Japan, housing, and lifestyle, from traditional to modern practices."
        context['seo_og_title'] = "Living in Japan - Lifestyle & Daily Life | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    

class BodyView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/body.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="body").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Body Parts List | Learn Vocabulary & Kanji | ThankJapan"
        context['seo_description'] = "Master essential Japanese vocabulary for body parts. Learn kanji and pronunciation for head, hands, feet, and more to help in daily life and health."
        context['seo_og_title'] = "Learn Japanese Body Parts - Essential Vocabulary | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

    
class DailyactionsView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/dailyactions.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="dailyactions").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        
        context['seo_title'] = "Japanese Daily Actions & Verbs | Learn Basic Vocabulary | ThankJapan"
        context['seo_description'] = "Master essential Japanese verbs for daily actions. Learn kanji and pronunciation for eating, drinking, sleeping, and more to help in everyday life."
        context['seo_og_title'] = "Learn Japanese Daily Actions - Essential Basic Verbs | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context


    
#japan food

class JapanFoodView(BGMContextMixin, TemplateView):
    template_name = "thank_japan_app/japan/japanfoodpage.html"
    bgm_page_type = 'region'


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = self.kwargs.get('lang_code') or self.request.GET.get('lang')
        
        if lang:
            self.request.session['tj_lang_code'] = lang
        
        current_lang = lang or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = current_lang
        context['lang'] = current_lang
        
        return context
    

class PrefectureListView(BGMContextMixin, TemplateView):
    template_name = "thank_japan_app/japan/prefecture_list_page.html"
    bgm_page_type = 'region'


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = self.kwargs.get('lang_code') or self.request.GET.get('lang')
        
        if lang:
            self.request.session['tj_lang_code'] = lang 
        
        current_lang = lang or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = current_lang
        context['lang'] = current_lang
        
        return context
    
            

class IshikawaView(BGMContextMixin, TemplateView):
    template_name = "thank_japan_app/japan/ishikawapage.html"
    bgm_page_type = 'region'


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = self.kwargs.get('lang_code') or self.request.GET.get('lang')
        
        if lang:
            self.request.session['tj_lang_code'] = lang

        current_lang = lang or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = current_lang
        context['lang'] = current_lang
        return context    


class ToyamaView(BGMContextMixin, TemplateView):
    template_name = "thank_japan_app/japan/toyamapage.html"
    bgm_page_type = 'region'


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = self.kwargs.get('lang_code') or self.request.GET.get('lang')
        
        if lang:
            self.request.session['tj_lang_code'] = lang

        current_lang = lang or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = current_lang
        context['lang'] = current_lang
        return context
    
    

class FukuiView(BGMContextMixin, TemplateView):
    template_name = "thank_japan_app/japan/fukuipage.html"
    bgm_page_type = 'region'


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lang = self.kwargs.get('lang_code') or self.request.GET.get('lang')
        
        if lang:
            self.request.session['tj_lang_code'] = lang
        
        current_lang = lang or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = current_lang
        context['lang'] = current_lang
        
        return context
    
    

#Thank_Japan premium 


def _activate_paypal_subscription(user, subscription_id):
    """Verifies a subscription_id directly with PayPal (never trusting a
    client- or redirect-supplied ID blindly) and activates premium for
    `user` if it's ACTIVE and on the expected plan. Returns (ok, message)."""
    subscription = get_paypal_subscription_details(subscription_id)
    if not subscription:
        logger.warning(f"PayPal subscription {subscription_id} could not be verified with PayPal")
        return False, 'subscription not found'

    if subscription.get('status') != 'ACTIVE':
        logger.warning(f"PayPal subscription {subscription_id} is not ACTIVE (status={subscription.get('status')})")
        return False, 'subscription is not active'

    if settings.PAYPAL_PLAN_ID and subscription.get('plan_id') != settings.PAYPAL_PLAN_ID:
        logger.warning(f"PayPal subscription {subscription_id} has unexpected plan_id={subscription.get('plan_id')}")
        return False, 'unexpected plan'

    profile, created = Profile.objects.get_or_create(user=user)
    profile.is_premium = True
    profile.paypal_subscription_id = subscription_id
    sync_paypal_premium_state(profile, subscription)
    profile.save()
    return True, 'success'


@login_required
@require_POST
def update_premium_status(request):
    try:
        data = json.loads(request.body)
        subscription_id = data.get('subscriptionID')

        if not subscription_id:
            return JsonResponse({'status': 'error'}, status=400)

        ok, message = _activate_paypal_subscription(request.user, subscription_id)
        if not ok:
            return JsonResponse({'status': 'error', 'message': message}, status=400)

        return JsonResponse({'status': 'success'})

    except Exception as e:
        logger.error(f"Update Error: {str(e)}")
        return JsonResponse({'status': 'error'}, status=500)

@login_required
@require_POST
def start_premium_trial(request):
    """Grants a 7-day app-managed premium trial - no PayPal subscription is
    created here at all (that's what avoided the client-side plan-override
    403 that blocked the earlier trial rollout, see PayPal checkout memory).
    is_premium is set True (not just is_trial) so every existing premium-gate
    check in the app (has_premium_access, ImgPremiumDetailView, etc.) grants
    access automatically with no changes needed elsewhere. premium_expires_at
    is shared with the regular-subscription field by design - trial and paid
    periods for one user never overlap, and expire_premium_subscriptions
    already resets is_trial=False in its non-Google-Play fallback branch when
    that date passes.

    trial_used is re-checked here even though the button is hidden client-side
    once it's True - never trust that alone. has_premium_access is also
    checked so a user who's already premium (e.g. subscribed directly without
    ever trialing) can't hit this endpoint directly and have their real
    premium_expires_at overwritten with a mere +7 days.
    """
    profile = request.user.profile
    if profile.trial_used:
        return JsonResponse({'status': 'error', 'message': 'Trial already used.'}, status=400)
    if profile.has_premium_access:
        return JsonResponse({'status': 'error', 'message': 'Already have premium access.'}, status=400)

    profile.is_premium = True
    profile.is_trial = True
    profile.trial_used = True
    profile.premium_expires_at = timezone.now() + timedelta(days=7)
    profile.save()

    return JsonResponse({'status': 'success', 'premium_expires_at': profile.premium_expires_at.isoformat()})


@login_required
@require_POST
def create_paypal_subscription(request):
    """Creates the PayPal subscription server-side (with the region-adjusted
    price override) and returns an approval URL for the browser to redirect
    to.

    The JS SDK's client-side actions.subscription.create() rejects a 'plan'
    override with 403 NOT_AUTHORIZED ("Billing Plan Override is not allowed
    due to insufficient permissions") - confirmed by sending the exact same
    override straight to the REST API with a server-side OAuth token, which
    succeeds (201) every time. Overriding a plan's pricing at
    subscription-creation time is only permitted for server-authenticated
    requests, so the override has to happen here rather than in the button's
    createSubscription callback.
    """
    price = get_premium_price(request)

    token = get_paypal_access_token()
    if not token:
        logger.error("PayPal subscription create failed: could not obtain access token")
        return JsonResponse({'status': 'error'}, status=502)

    body = {
        "plan_id": settings.PAYPAL_PLAN_ID,
        "plan": {
            "billing_cycles": [{
                "sequence": 1,
                "pricing_scheme": {
                    "fixed_price": {"value": price['price_usd'], "currency_code": "USD"}
                }
            }]
        },
        "application_context": {
            "return_url": request.build_absolute_uri(reverse('paypal_subscription_return')),
            "cancel_url": request.build_absolute_uri(reverse('paypal_subscription_cancel')),
        },
    }

    create_url = "https://api-m.paypal.com/v1/billing/subscriptions"
    if settings.PAYPAL_MODE == "sandbox":
        create_url = "https://api-m.sandbox.paypal.com/v1/billing/subscriptions"

    try:
        resp = requests.post(
            create_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                # Idempotency key so a double-click can't create duplicate subscriptions.
                "PayPal-Request-Id": f"sub-{request.user.id}-{int(timezone.now().timestamp())}",
            },
            json=body,
        )
    except requests.RequestException as e:
        logger.error(f"PayPal subscription create request failed: {e}")
        return JsonResponse({'status': 'error'}, status=502)

    if resp.status_code not in (200, 201):
        logger.error(f"PayPal subscription create failed: {resp.status_code} {resp.text}")
        return JsonResponse({'status': 'error'}, status=502)

    data = resp.json()
    approve_url = next((l.get('href') for l in data.get('links', []) if l.get('rel') == 'approve'), None)
    subscription_id = data.get('id')
    if not approve_url or not subscription_id:
        logger.error(f"PayPal subscription create response missing approve link: {data}")
        return JsonResponse({'status': 'error'}, status=502)

    # Read back on return from PayPal instead of trusting the redirect's own
    # query string - same never-trust-client-input principle as elsewhere.
    request.session['pending_paypal_subscription_id'] = subscription_id
    return JsonResponse({'status': 'success', 'approve_url': approve_url})

@login_required
def paypal_subscription_return(request):
    """Buyer is redirected back here by PayPal after approving (or
    abandoning) the subscription created in create_paypal_subscription."""
    subscription_id = request.session.pop('pending_paypal_subscription_id', None)
    url_name, lang_code = get_lang_info(request)

    if not subscription_id:
        return redirect(f"{reverse(url_name)}?lang={lang_code}&paypal_error=1")

    ok, message = _activate_paypal_subscription(request.user, subscription_id)
    if not ok:
        logger.warning(f"paypal_subscription_return: activation failed for {subscription_id}: {message}")
        return redirect(f"{reverse(url_name)}?lang={lang_code}&paypal_error=1")

    # thank_you url names mirror premium_info's exactly (same language
    # suffix), so the language-specific page can be derived without a
    # second lookup table.
    thank_you_url_name = url_name.replace('premium_info', 'thank_you')
    return redirect(thank_you_url_name)

@login_required
def paypal_subscription_cancel(request):
    """Buyer backed out of the PayPal approval page."""
    request.session.pop('pending_paypal_subscription_id', None)
    url_name, lang_code = get_lang_info(request)
    return redirect(f"{reverse(url_name)}?lang={lang_code}")

@csrf_exempt
def paypal_webhook(request):

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        logger.warning(f"PayPal webhook received invalid JSON body: {e}")
        return HttpResponse(status=400)

    if not settings.PAYPAL_WEBHOOK_ID:
        logger.error("PAYPAL_WEBHOOK_ID is not configured; rejecting webhook")
        return HttpResponse(status=500)

    if not verify_paypal_webhook_signature(request, data):
        logger.warning("PayPal webhook signature verification failed")
        return HttpResponse(status=401)

    try:
        event_type = data.get('event_type')
        resource = data.get('resource') or {}
        subscription_id = resource.get('id')

        deactivate_events = [
            "BILLING.SUBSCRIPTION.CANCELLED",
            "BILLING.SUBSCRIPTION.SUSPENDED",
            "BILLING.SUBSCRIPTION.EXPIRED",
            "BILLING.SUBSCRIPTION.PAYMENT.FAILED"
        ]

        if event_type in deactivate_events and subscription_id:
            Profile.objects.filter(paypal_subscription_id=subscription_id).update(
                is_premium=False, is_trial=False, premium_expires_at=None
            )
            logger.info(f"Webhook Handled: {subscription_id} to Free")

        elif event_type == "BILLING.SUBSCRIPTION.ACTIVATED" and subscription_id:
            # Fires on first activation (trial or paid) and on renewals/reactivations.
            # Re-fetch from PayPal rather than trusting the webhook payload's own
            # billing_info, consistent with never trusting client/webhook-supplied data.
            profile = Profile.objects.filter(paypal_subscription_id=subscription_id).first()
            if profile:
                subscription = get_paypal_subscription_details(subscription_id)
                if subscription:
                    profile.is_premium = True
                    sync_paypal_premium_state(profile, subscription)
                    profile.save()
                    logger.info(f"Webhook Handled: {subscription_id} ACTIVATED (is_trial={profile.is_trial})")

                    # Opportunistic fast path for the continuation-benefit
                    # points: the daily grant_premium_renewal_points batch
                    # is the reliable backstop for both PayPal and Google
                    # Play, this just lets PayPal subscribers see their
                    # +50pt sooner when their own renewal webhook happens
                    # to fire. Never grants during trial.
                    if profile.is_premium and not profile.is_trial:
                        grant_renewal_rewards_if_due(profile)

        return HttpResponse(status=200)

    except Exception as e:
        logger.error(f"Webhook Error: {str(e)}")
        return HttpResponse(status=200)
        
#premium_info

def get_lang_info(request):
    
    lang_code = request.GET.get('lang')
    
    
    if not lang_code:
        path = request.path.lower()
        lang_keys = [
            'zh-hant', 'zh-cn', 'de', 'en-in', 'es-es', 'es-mx', 
            'fr', 'it', 'ja', 'ko', 'pt-br', 'pt', 'th', 'vi'
        ]
        for key in lang_keys:
            if f'/{key}/' in path:
                lang_code = key
                break

    
    if not lang_code:
        lang_code = request.session.get('user_lang')

    
    if not lang_code:
        referer = request.META.get('HTTP_REFERER', '').lower()
        for key in lang_keys:
            if f'/{key}/' in referer:
                lang_code = key
                break

    if not lang_code:
        lang_code = 'en'

    request.session['user_lang'] = lang_code

    lang_map = {
        'de': 'premium_infode',
        'en-in': 'premium_infoenIN',
        'es-es': 'premium_infoesES',
        'es-mx': 'premium_infoesMX',
        'fr': 'premium_infofr',
        'it': 'premium_infoit',
        'ja': 'premium_infoja',
        'ko': 'premium_infoko',
        'pt-br': 'premium_infoptBR',
        'pt': 'premium_infopt',
        'th': 'premium_infoth',
        'vi': 'premium_infovi',
        'zh-hant': 'premium_infozhHANT',
        'zh-cn': 'premium_infozhCN',
    }
    
    url_name = lang_map.get(lang_code, 'premium_info')
    return url_name, lang_code


def premium_info(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info-v2.html', context)

def premium_infoZHCN(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_zh_cn-v2.html', context)


def premium_infoZHHANT(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_zh_hant-v2.html', context)

def premium_infoVI(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_vi-v2.html', context)

def premium_infoTH(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_th-v2.html', context)

def premium_infoPT(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_pt-v2.html', context)

def premium_infoPTBR(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_pt_br-v2.html', context)

def premium_infoKO(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_ko-v2.html', context)

def premium_infoJA(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_ja-v2.html', context)

def premium_infoIT(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_it-v2.html', context)

def premium_infoFR(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_fr-v2.html', context)

def premium_infoESMX(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_es_mx-v2.html', context)

def premium_infoESES(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_es_es-v2.html', context)

def premium_infoENIN(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_en_in-v2.html', context)

def premium_infoDE(request):
    context = {
        # Google Play Billing button falls back here (?billing=web) when the
        # Digital Goods API isn't available even inside a real TWA session.
        'is_twa': is_android_twa(request) and request.GET.get('billing') != 'web',
        # Guests have never used a trial either, so they see the trial CTA too
        # (matches the PayPal button, which is likewise shown to guests -
        # actually starting a trial or subscription still requires login,
        # enforced server-side same as create_paypal_subscription today).
        'trial_used': request.user.profile.trial_used if request.user.is_authenticated else False,
    }
    context.update(get_premium_price(request))
    return render(request, 'thank_japan_app/premium/premium_info_de-v2.html', context)


#thankyou
@login_required
def thank_you(request):
    return render(request, 'thank_japan_app/thankyou/thank_you.html')

@login_required
def thank_youZHCN(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_zh_cn-v2.html')


@login_required
def thank_youZHHANT(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_zh_hant-v2.html')

@login_required
def thank_youVI(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_vi-v2.html')

@login_required
def thank_youTH(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_th-v2.html')

@login_required
def thank_youPT(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_pt-v2.html')

@login_required
def thank_youPTBR(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_pt_br-v2.html')

@login_required
def thank_youKO(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_ko-v2.html')

@login_required
def thank_youJA(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_ja-v2.html')

@login_required
def thank_youIT(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_it-v2.html')

@login_required
def thank_youFR(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_fr-v2.html')

@login_required
def thank_youESMX(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_es_mx-v2.html')

@login_required
def thank_youESES(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_es_es-v2.html')

@login_required
def thank_youENIN(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_en_in-v2.html')

@login_required
def thank_youDE(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_de-v2.html')


def get_weekly_progress(user, weeks=8):
    """Last `weeks` weeks of WeeklyScore for the account settings chart, oldest
    first, zero-filled for weeks with no recorded score so the bars stay
    contiguous. Returns [] when there's no score in the whole window at all,
    so the template can show an empty state instead of an all-zero chart."""
    current_week_start = WeeklyScore.get_current_week_start()
    start_range = current_week_start - timedelta(weeks=weeks - 1)
    scores_by_week = dict(
        WeeklyScore.objects.filter(user=user, week_start__gte=start_range)
        .values_list('week_start', 'score')
    )

    if not any(scores_by_week.values()):
        return []

    max_score = max(scores_by_week.values())
    weekly_progress = []
    for i in range(weeks):
        week_start = start_range + timedelta(weeks=i)
        score = scores_by_week.get(week_start, 0)
        weekly_progress.append({
            'label': week_start.strftime('%m/%d'),
            'score': score,
            'height_percent': round((score / max_score) * 100) if max_score else 0,
        })
    return weekly_progress


class ThankJapanConnectionsView(ConnectionsView):
    """Same allauth ConnectionsView/DisconnectForm/connect-flow, just with
    extra context so the restyled template (templates/socialaccount/connections.html)
    can show per-provider connect/disconnect state without its own view."""
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        connected_accounts = context['form'].accounts
        context['connected_accounts'] = connected_accounts
        context['connected_provider_ids'] = list(connected_accounts.values_list('provider', flat=True))
        context['can_disconnect'] = self.request.user.has_usable_password() or connected_accounts.count() > 1
        return context


#account_settings
@login_required
def account_settings(request):
    lang = request.GET.get('lang') or request.session.get('tj_lang_code')
    
    if lang and lang != 'en':
        mapping = {
            'ja': 'account_settingsja',
            'vi': 'account_settingsvi',
            'fr': 'account_settingsfr',
            'it': 'account_settingsit',
            'pt': 'account_settingspt',
            'zh-hant': 'account_settingszhHANT',
            'zh-cn': 'account_settingszhCN',
            'ko': 'account_settingsko',
            'es-es': 'account_settingsesES',
            'de': 'account_settingsde',
            'th': 'account_settingsth',
            'pt-br': 'account_settingsptBR',
            'es-mx': 'account_settingsesMX',
            'en-in': 'account_settingsenIN',
        }
        if lang in mapping:
            url = reverse(mapping[lang])
            query = request.GET.urlencode()
            return redirect(f"{url}?{query}" if query else url)

    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings-v2.html', context)


FREE_LEARNED_WORDS_CAP = 30


@login_required
def learned_words(request):
    """Lists every word the user has correctly reviewed from a word-detail
    page (see WordReviewLog / game_submit_answer's mode='single' branch),
    grouped by category, most-recently-active category first. Linked from
    account_settings ("Learned Words").

    Free accounts see only the FREE_LEARNED_WORDS_CAP most-recently-reviewed
    words (across all categories combined); anything beyond that is never
    included in the response at all - same hard-cutoff-plus-count approach
    as the premium-category 12-item cap (see e.g. BusinessJapaneseView)
    rather than a CSS blur, so no extra word data ever reaches the DOM for
    a free account. has_premium_access is checked live on every request
    with nothing cached/snapshotted, so this - like that same 12-item cap -
    naturally re-engages the moment premium lapses, with no grandfathering."""
    premium_url_name, lang_code = get_lang_info(request)
    is_premium = getattr(request.user.profile, 'has_premium_access', False)

    logs = WordReviewLog.objects.filter(user=request.user).select_related('word', 'premium_word').order_by('-last_reviewed_at')

    flat_items = []
    for log in logs:
        word = log.selected_word
        if word is None:
            continue
        flat_items.append({
            'log': log,
            'object': word,
            'is_premium': log.premium_word_id is not None,
        })

    total_learned = len(flat_items)
    visible_items = flat_items if is_premium else flat_items[:FREE_LEARNED_WORDS_CAP]
    hidden_count = max(0, total_learned - len(visible_items))

    grouped = {}
    for item in visible_items:
        grouped.setdefault(item['object'].category, []).append(item)

    category_groups = sorted(
        grouped.items(),
        key=lambda kv: max(item['log'].last_reviewed_at for item in kv[1]),
        reverse=True,
    )

    context = {
        'lang_code': lang_code,
        'category_groups': category_groups,
        'total_learned': total_learned,
        'is_locked': hidden_count > 0,
        'hidden_count': hidden_count,
        'premium_url_name': premium_url_name,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/learned_words.html', context)


@login_required
def account_settingsZHCN(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_zh_cn-v2.html', context)


@login_required
def account_settingsZHHANT(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_zh_hant-v2.html', context)


@login_required
def account_settingsVI(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_vi-v2.html', context)


@login_required
def account_settingsTH(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_th-v2.html', context)


@login_required
def account_settingsPT(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_pt-v2.html', context)


@login_required
def account_settingsPTBR(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_pt_br-v2.html', context)


@login_required
def account_settingsKO(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_ko-v2.html', context)


@login_required
def account_settingsJA(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
     
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_ja-v2.html', context)


@login_required
def account_settingsIT(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_it-v2.html', context)


@login_required
def account_settingsFR(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_fr-v2.html', context)


@login_required
def account_settingsESMX(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_es_mx-v2.html', context)


@login_required
def account_settingsESES(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_es_es-v2.html', context)


@login_required
def account_settingsENIN(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_en_in-v2.html', context)


@login_required
def account_settingsDE(request):
    profile = request.user.profile
    s = profile.total_score
    
    registered_players = Player.objects.exclude(username__icontains="Guest")
    total_registered = registered_players.count()
    current_rank = registered_players.filter(total_score__gt=s).count() + 1
    
    
    thresholds = [0, 50, 100, 200, 300, 450, 650, 900, 1200, 2000]
    
    current_min = 0
    next_max = 2000
    for i in range(len(thresholds) - 1):
        if s < thresholds[i+1]:
            current_min = thresholds[i]
            next_max = thresholds[i+1]
            break
    else:
        current_min = 2000
        next_max = 2000

    
    pts_to_next = next_max - s if s < 2000 else 0
    
    if next_max > current_min:
        progress_percent = ((s - current_min) / (next_max - current_min)) * 100
    else:
        progress_percent = 100

    recent_renewal_rewards = PremiumRenewalReward.objects.filter(user=request.user).order_by('-granted_at')[:5]

    context = {
        'total_score': s,
        'pts_to_next': pts_to_next,
        'progress_percent': progress_percent,
        'current_rank': current_rank,
        'total_registered': total_registered,
        'streak_count': profile.streak_count,
        'weekly_progress': get_weekly_progress(request.user),
        'achievement_progress': get_achievement_progress(profile),
        'is_twa': is_android_twa(request),
        'daily_question_notify': profile.daily_question_notify,
        'recent_renewal_rewards': recent_renewal_rewards,
    }
    return render(request, 'thank_japan_app/account/account_settings_de-v2.html', context)


#subscription

def get_paypal_access_token():
    auth_url = "https://api-m.paypal.com/v1/oauth2/token"
    if settings.PAYPAL_MODE == "sandbox":
        auth_url = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
    resp = requests.post(auth_url, auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET), data={"grant_type": "client_credentials"})
    return resp.json().get('access_token')

def cancel_paypal_subscription(subscription_id, reason):
    token = get_paypal_access_token()
    if token:
        cancel_url = f"https://api-m.paypal.com/v1/billing/subscriptions/{subscription_id}/cancel"
        if settings.PAYPAL_MODE == "sandbox":
            cancel_url = f"https://api-m.sandbox.paypal.com/v1/billing/subscriptions/{subscription_id}/cancel"
        requests.post(cancel_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"reason": reason})

def get_paypal_subscription_details(subscription_id):
    """Looks up a subscription directly on PayPal's servers so a client-supplied
    subscriptionID can't be trusted blindly before granting premium access."""
    token = get_paypal_access_token()
    if not token:
        return None

    details_url = f"https://api-m.paypal.com/v1/billing/subscriptions/{subscription_id}"
    if settings.PAYPAL_MODE == "sandbox":
        details_url = f"https://api-m.sandbox.paypal.com/v1/billing/subscriptions/{subscription_id}"

    try:
        resp = requests.get(details_url, headers={"Authorization": f"Bearer {token}"})
    except requests.RequestException as e:
        logger.error(f"PayPal subscription lookup failed for {subscription_id}: {e}")
        return None

    if resp.status_code != 200:
        return None
    return resp.json()

def verify_paypal_webhook_signature(request, webhook_event):
    """Verifies a PayPal webhook's authenticity via PayPal's
    verify-webhook-signature API, using the PAYPAL-* transmission headers."""
    headers = request.headers
    payload = {
        "auth_algo": headers.get("Paypal-Auth-Algo"),
        "cert_url": headers.get("Paypal-Cert-Url"),
        "transmission_id": headers.get("Paypal-Transmission-Id"),
        "transmission_sig": headers.get("Paypal-Transmission-Sig"),
        "transmission_time": headers.get("Paypal-Transmission-Time"),
        "webhook_id": settings.PAYPAL_WEBHOOK_ID,
        "webhook_event": webhook_event,
    }

    if not all([payload["auth_algo"], payload["cert_url"], payload["transmission_id"],
                payload["transmission_sig"], payload["transmission_time"]]):
        return False

    token = get_paypal_access_token()
    if not token:
        return False

    verify_url = "https://api-m.paypal.com/v1/notifications/verify-webhook-signature"
    if settings.PAYPAL_MODE == "sandbox":
        verify_url = "https://api-m.sandbox.paypal.com/v1/notifications/verify-webhook-signature"

    try:
        resp = requests.post(
            verify_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
    except requests.RequestException as e:
        logger.error(f"PayPal webhook signature verification request failed: {e}")
        return False

    if resp.status_code != 200:
        return False
    return resp.json().get("verification_status") == "SUCCESS"

def _paypal_current_cycle_is_trial(subscription):
    """Reads billing_info.cycle_executions to find the currently-active
    billing cycle and reports whether it's the TRIAL one. total_cycles == 0
    means an indefinitely-repeating (usually REGULAR) cycle."""
    cycle_executions = (subscription.get('billing_info') or {}).get('cycle_executions') or []
    for cycle in sorted(cycle_executions, key=lambda c: c.get('sequence', 0)):
        total_cycles = cycle.get('total_cycles', 0)
        cycles_completed = cycle.get('cycles_completed', 0)
        if total_cycles == 0 or cycles_completed < total_cycles:
            return cycle.get('tenure_type') == 'TRIAL'
    return False

def sync_paypal_premium_state(profile, subscription):
    """Updates a Profile's premium/trial bookkeeping from a verified PayPal
    subscription resource (as returned by get_paypal_subscription_details).
    Does not save() - callers persist alongside their own field changes."""
    next_billing_time = (subscription.get('billing_info') or {}).get('next_billing_time')
    if next_billing_time:
        parsed = parse_datetime(next_billing_time)
        if parsed:
            profile.premium_expires_at = parsed

    is_trial = _paypal_current_cycle_is_trial(subscription)
    profile.is_trial = is_trial
    if is_trial:
        profile.trial_used = True


PREMIUM_RENEWAL_REWARD_POINTS = 50
PREMIUM_RENEWAL_REWARD_MAX_CATCHUP = 3  # cap on periods granted in one call, in case a run was missed for a while


def grant_renewal_rewards_if_due(profile):
    """Continuation-benefit points: +{PREMIUM_RENEWAL_REWARD_POINTS}pt for
    each elapsed month of a real (non-trial) paid subscription, anchored to
    profile.premium_paid_started_at. Call for any profile currently
    is_premium=True and is_trial=False - from grant_premium_renewal_points
    (the daily batch, the reliable primary path covering both PayPal and
    Google Play) and opportunistically from the PayPal ACTIVATED webhook
    for faster crediting. Caller must have already saved is_premium/
    is_trial themselves; this only touches premium_paid_started_at and
    total_score.

    Safe to call redundantly (webhook and batch racing, or the batch
    running twice) - the (user, period_start) uniqueness on
    PremiumRenewalReward is what actually prevents a double grant, not
    caller coordination. Returns the number of periods newly granted.
    """
    if not profile.premium_paid_started_at:
        # No historical contract-start date exists for profiles already
        # past their trial before this feature shipped - "now" becomes
        # this profile's anchor rather than granting retroactively.
        profile.premium_paid_started_at = timezone.now()
        profile.save(update_fields=['premium_paid_started_at'])
        return 0

    now = timezone.now()
    granted_count = 0
    for n in range(1, PREMIUM_RENEWAL_REWARD_MAX_CATCHUP + 1):
        period_start = profile.premium_paid_started_at + relativedelta(months=n)
        if period_start > now:
            break
        _, created = PremiumRenewalReward.objects.get_or_create(
            user=profile.user, period_start=period_start,
            defaults={'points': PREMIUM_RENEWAL_REWARD_POINTS},
        )
        if created:
            profile.total_score += PREMIUM_RENEWAL_REWARD_POINTS
            granted_count += 1

    if granted_count:
        profile.save(update_fields=['total_score'])
        player = Player.objects.filter(username=profile.user.username).first()
        if player:
            player.total_score = profile.total_score
            player.save()

    return granted_count


@login_required
@require_POST
def downgrade_premium(request):
    profile = request.user.profile
    if profile.is_trial:
        # The "Cancel Subscription" button is only ever rendered for real,
        # paying subscribers (see account_settings*-v2.html), never during a
        # trial - a trial just lapses on its own after 7 days. This is a
        # defense-in-depth check against this endpoint being hit directly
        # (e.g. via the URL/devtools) while is_trial is still True, since
        # there's nothing to actually cancel and letting it through would
        # just discard the trial early with no benefit to anyone.
        return HttpResponse(status=400)
    if profile.has_premium_access and profile.paypal_subscription_id:
        try:
            cancel_paypal_subscription(profile.paypal_subscription_id, "User downgraded")
        except Exception:
            pass
    # is_premium / premium_expires_at are deliberately left untouched here:
    # the user already paid for the current billing period, so access should
    # continue until premium_expires_at (like Google Play's own cancellation,
    # which this mirrors), not end the instant they click Cancel.
    # cancel_paypal_subscription above already stops future auto-renewal;
    # expire_premium_subscriptions reverts is_premium once premium_expires_at
    # actually passes, same as any other expiry.

    next_url_name = request.POST.get('downgrade_url_name', 'downgrade_success')

    lang_code = request.session.get('tj_lang_code', 'en')

    try:
        target_url = reverse(next_url_name)
    except Exception:
        # Unknown/unregistered downgrade_url_name must not crash after the
        # downgrade already succeeded, so fall back to the one URL that's
        # always there.
        target_url = reverse('downgrade_success')
    return redirect(f"{target_url}?lang={lang_code}")
    
    
    
@login_required
@require_POST
def delete_account(request):
    
    lang_code = request.GET.get('lang') or request.session.get('tj_lang_code', 'en')
    
    username = request.user.username
    user = request.user
    profile = user.profile
    
    if profile.has_premium_access and profile.paypal_subscription_id:
        try:
            cancel_paypal_subscription(profile.paypal_subscription_id, "User deleted account")
        except Exception:
            pass
            
    Player.objects.filter(username=username).delete()
    user.delete()
    
    logout(request) 
    
    next_url_name = request.POST.get('success_url_name', 'delete_success')

    try:
        target_url = reverse(next_url_name)
    except Exception:
        # Unknown/unregistered success_url_name (e.g. a template referencing
        # a name that doesn't exist) must not crash after the account is
        # already deleted, so fall back to the one URL that's always there.
        target_url = reverse('delete_success')
    return redirect(f"{target_url}?lang={lang_code}")
    
    

#downgrade_success
def downgrade_success_v2(request):
    
    return render(request, 'thank_japan_app/downgrade/downgrade_success_v2.html')



#delete_success

def delete_success_v2(request):
   
    return render(request, 'thank_japan_app/delete/delete_success_v2.html')



#free-category

class DailyConversationView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/dairy_conversation.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="DailyConversation").order_by('timestamp')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        return context 
    
    
class TourismEtiquetteView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/tourism_etiquette.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="TourismEtiquette").order_by('timestamp')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        return context
    

class EntertainmentView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/entertainment.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="Entertainment").order_by('timestamp')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        return context    
  
 
      

class SlangView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/slang.html"
    paginate_by = 200
    bgm_page_type = 'study_select'

    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="slang").order_by('timestamp')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        return context


class RomanceRelationshipsView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/romance_relationships.html"
    paginate_by = 200
    bgm_page_type = 'study_select'

    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="RomanceRelationships").order_by('timestamp')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        return context
    
    

#premium-category
   
    
class BusinessJapaneseView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/business_japanese.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="BusinessJapanese").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if not is_premium:
            return qs[:12]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="BusinessJapanese")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 12)
        else:
            context['is_locked'] = False
        return context

class LivingInJapanView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/living_in_japan.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="LivingInJapan").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if not is_premium:
            return qs[:12]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="LivingInJapan")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 12)
        else:
            context['is_locked'] = False
        return context

class MedicalEmergencyView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/medical_emergency.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="MedicalEmergency").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if not is_premium:
            return qs[:12]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="MedicalEmergency")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 12)
        else:
            context['is_locked'] = False
        return context

class RealestateRulesView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/realestate_rules.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="RealEstateRules").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if not is_premium:
            return qs[:12]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="RealEstateRules")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 12)
        else:
            context['is_locked'] = False
        return context

class PrefectureView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/prefecture.html"
    paginate_by = 200
    bgm_page_type = 'study_select'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="Prefectures").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if not is_premium:
            return qs[:12]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="Prefectures")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 12)
        else:
            context['is_locked'] = False
        return context    

               
# SEO title/description for word detail pages, generated per language.
# GSC data showed high impressions but very low CTR on these pages because the
# title was not localized and no meta description was rendered at all.
WORD_NAME_FIELD_MAP = {
    'ja': 'englishname_ja',
    'en-in': 'englishname_en_in',
    'zh-cn': 'englishname_zh_cn',
    'zh-hant': 'englishname_zh_hant',
    'ko': 'englishname_ko',
    'fr': 'englishname_fr',
    'de': 'englishname_de',
    'it': 'englishname_it',
    'es-es': 'englishname_es_es',
    'es-mx': 'englishname_es_mx',
    'pt': 'englishname_pt',
    'pt-br': 'englishname_pt_br',
    'th': 'englishname_th',
    'vi': 'englishname_vi',
}

WORD_SEO_TITLE_TEMPLATES = {
    'en': "{word} Meaning & Pronunciation | Photo Quiz - ThankJapan",
    'en-in': "{word} Meaning in Japanese | Photo Quiz - ThankJapan",
    'ja': "{word}の意味・読み方・発音 | 写真クイズで覚える日本語 | ThankJapan",
    'zh-cn': "{word}是什么意思？发音怎么读 | 图片测验学日语 - ThankJapan",
    'zh-hant': "{word}是什麼意思？發音怎麼唸 | 圖片測驗學日文 - ThankJapan",
    'ko': "{word} 뜻・발음 정리 | 사진 퀴즈 - ThankJapan",
    'fr': "{word} : sens et prononciation | Quiz photo - ThankJapan",
    'de': "{word}: Bedeutung & Aussprache | Foto-Quiz - ThankJapan",
    'it': "{word}: significato e pronuncia | Quiz foto - ThankJapan",
    'es-es': "{word}: significado y pronunciación | Quiz - ThankJapan",
    'es-mx': "{word}: significado y pronunciación | Quiz - ThankJapan",
    'pt': "{word}: significado e pronúncia | Quiz - ThankJapan",
    'pt-br': "{word}: significado e pronúncia | Quiz - ThankJapan",
    'th': "{word} แปลว่าอะไร? อ่าน-ออกเสียง | ควิซรูปภาพ - ThankJapan",
    'vi': "{word} nghĩa là gì? Đọc & phát âm | Quiz ảnh - ThankJapan",
}

WORD_SEO_DESCRIPTION_TEMPLATES = {
    'en': "What does {word} mean in Japanese? Get the meaning, reading and native pronunciation, then make it stick with a fun photo quiz on ThankJapan.",
    'en-in': "Wondering what {word} means in Japanese? Learn the meaning, reading and pronunciation, then practice with a fun photo quiz on ThankJapan.",
    'ja': "「{word}」の意味・読み方・発音をわかりやすく解説。ネイティブ音声を聞きながら、写真クイズで楽しく日本語の語彙を身につけよう。今すぐThankJapanでチェック!",
    'zh-cn': "「{word}」到底是什么意思？这里有详细的含义、发音和读法讲解，还能通过趣味图片测验边玩边记单词。快来ThankJapan轻松学日语！",
    'zh-hant': "「{word}」到底是什麼意思？這裡有清楚的含義、發音與讀法說明，還能透過趣味圖片測驗邊玩邊記單字。立即到ThankJapan輕鬆學日文！",
    'ko': "{word}의 뜻과 읽는 법, 발음을 한눈에 확인하고 재미있는 사진 퀴즈로 일본어 단어를 익혀보세요. ThankJapan에서 지금 시작하세요!",
    'fr': "Que signifie « {word} » en japonais ? Découvrez sa signification, sa lecture et sa prononciation, puis retenez-le facilement avec un quiz photo sur ThankJapan.",
    'de': "Was bedeutet „{word}“ auf Japanisch? Entdecke Bedeutung, Lesung und Aussprache und präge es dir mit einem unterhaltsamen Foto-Quiz auf ThankJapan spielerisch ein.",
    'it': "Cosa significa “{word}” in giapponese? Scopri significato, lettura e pronuncia, poi mettiti alla prova con un divertente quiz fotografico su ThankJapan.",
    'es-es': "¿Qué significa “{word}” en japonés? Descubre su significado, lectura y pronunciación, y apréndelo jugando con un quiz de fotos en ThankJapan.",
    'es-mx': "¿Qué significa “{word}” en japonés? Conoce su significado, lectura y pronunciación, y apréndelo jugando con un divertido quiz de fotos en ThankJapan.",
    'pt': "O que significa “{word}” em japonês? Descobre o significado, a leitura e a pronúncia, e fixa tudo com um quiz de fotos divertido no ThankJapan.",
    'pt-br': "O que significa “{word}” em japonês? Descubra o significado, a leitura e a pronúncia, e grave tudo brincando com um quiz de fotos no ThankJapan.",
    'th': "{word} แปลว่าอะไรในภาษาญี่ปุ่น? ดูความหมาย การอ่าน และการออกเสียงแบบเจ้าของภาษา แล้วจำศัพท์ได้ง่ายๆ ด้วยควิซรูปภาพสนุกๆ ที่ ThankJapan",
    'vi': "{word} nghĩa là gì trong tiếng Nhật? Xem ngay nghĩa, cách đọc và phát âm chuẩn, rồi ghi nhớ dễ dàng với quiz hình ảnh thú vị tại ThankJapan.",
}


def build_word_seo_context(item, lang_code):
    field_name = WORD_NAME_FIELD_MAP.get(lang_code)
    word_name = getattr(item, field_name, None) if field_name else None
    if not word_name and lang_code == 'ja':
        word_name = item.jpname
    if not word_name:
        word_name = item.englishname

    title_template = WORD_SEO_TITLE_TEMPLATES.get(lang_code, WORD_SEO_TITLE_TEMPLATES['en'])
    description_template = WORD_SEO_DESCRIPTION_TEMPLATES.get(lang_code, WORD_SEO_DESCRIPTION_TEMPLATES['en'])

    return {
        'seo_word_name': word_name,
        'seo_title': title_template.format(word=word_name),
        'seo_description': description_template.format(word=word_name),
    }


# free detail view
class CategoryDetailView(DetailView):
    model = ThankJapanModel
    template_name = "thank_japan_app/thankjapanmodel_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_template_names(self):
        # is_modal (X-Requested-With: XMLHttpRequest) renders the bare
        # detail_main content with no site-chrome wrapper - Django's
        # {% extends %} can't conditionally resolve to "nothing" inside one
        # template (that used to be attempted via `is_modal|yesno:",base/..."`,
        # which raised TemplateSyntaxError on every modal request - see
        # thankjapanmodel_detail_modal.html for the fix), so the two cases
        # are two separate template files that both {% include %} the same
        # shared body partial (thankjapanmodel_detail_body.html).
        if self.is_modal:
            return ["thank_japan_app/thankjapanmodel_detail_modal.html"]
        return [self.template_name]

    def get(self, request, lang=None, *args, **kwargs):
        category = self.kwargs.get('category')
        slug = self.kwargs.get('slug')

        if slug == 'null':
            raise Http404

        self.is_modal = request.headers.get('x-requested-with') == 'XMLHttpRequest'

        try:
            self.object = ThankJapanModel.objects.get(category__iexact=category, slug=slug)
            return super().get(request, *args, **kwargs)
        except ThankJapanModel.DoesNotExist:
            moved_item = ThankJapanModel.objects.filter(
                category__iexact=category, 
                slug__icontains=slug
            ).first()

            if not moved_item:
                search_key = slug.replace('-', '')[:4]
                moved_item = ThankJapanModel.objects.filter(
                    category__iexact=category,
                    slug__icontains=search_key
                ).first()

            if moved_item:
                lang_param = request.GET.get('lang')
                new_url = reverse('category_detail', kwargs={
                    'category': moved_item.category.lower(),
                    'slug': moved_item.slug
                })
                if lang_param:
                    new_url += f"?lang={lang_param}"
                return redirect(new_url, permanent=True)
            
            raise Http404

    def get_queryset(self):
        category = self.kwargs['category']
        return ThankJapanModel.objects.filter(category__iexact=category)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_item = self.object
        
        context['is_modal'] = self.is_modal

        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['is_twa'] = is_android_twa(self.request)

        context.update(build_word_seo_context(current_item, lang_code))

        context['related_items'] = ThankJapanModel.objects.filter(
            category=current_item.category
        ).exclude(
            id=current_item.id
        ).order_by('?')[:6]

        url_name = CATEGORY_URL_MAP.get(current_item.category, 'category_list')
        context['category_list_url'] = reverse(url_name)
        
        return context
    

#premium-detail

class ImgPremiumDetailView(DetailView):
    template_name = "thank_japan_app/thankjapanmodel_detail_premium.html"
    model = ThankJapanPremium
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_template_names(self):
        # See CategoryDetailView.get_template_names - same fix, same reason.
        if self.is_modal:
            return ["thank_japan_app/thankjapanmodel_detail_premium_modal.html"]
        return [self.template_name]

    def get_queryset(self):
        category = self.kwargs.get('category')
        return ThankJapanPremium.objects.filter(category__iexact=category).order_by('timestamp')

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if obj.category not in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment", "RomanceRelationships"] and not is_premium:
            free_sample_ids = ThankJapanPremium.objects.filter(
                category__iexact=obj.category
            ).order_by('timestamp').values_list('id', flat=True)[:12]
            if obj.id not in free_sample_ids:
                raise Http404
        return obj

    def dispatch(self, request, lang=None, *args, **kwargs):
        category = self.kwargs.get('category')
        slug = self.kwargs.get('slug')

        if slug == 'null':
            raise Http404

        self.is_modal = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            moved_item = ThankJapanPremium.objects.filter(
                category__iexact=category,
                slug__icontains=slug
            ).first()
            if not moved_item:
                search_key = slug.replace('-', '')[:4]
                moved_item = ThankJapanPremium.objects.filter(
                    category__iexact=category,
                    slug__icontains=search_key
                ).first()
            if moved_item:
                if moved_item.category in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment", "RomanceRelationships"] or is_premium:
                    lang_param = request.GET.get('lang')
                    new_url = reverse('detail_premium', kwargs={
                        'category': moved_item.category,
                        'slug': moved_item.slug
                    })
                    if lang_param:
                        new_url += f"?lang={lang_param}"
                    return redirect(new_url, permanent=True)
                else:
                    _, lang_code = get_lang_info(request)
                    return redirect(f"{reverse('premium_info')}?lang={lang_code}")
            raise Http404

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_modal'] = self.is_modal
        current_item = self.object
        url_name, lang_code = get_lang_info(self.request)
        context['premium_url_name'] = url_name
        context['lang_code'] = lang_code
        context['is_twa'] = is_android_twa(self.request)
        context.update(build_word_seo_context(current_item, lang_code))
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_target_name = CATEGORY_URL_MAP.get(current_item.category, 'toppage')
        try:
            base_category_url = reverse(url_target_name)
        except:
            base_category_url = "/"
        context['category_list_url'] = f"{base_category_url}?lang={lang_code}"
        if current_item.category in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment", "RomanceRelationships"] or is_premium:
            context['free_sample_ids'] = ThankJapanPremium.objects.filter(
                category=current_item.category
            ).values_list('id', flat=True)
        else:
            context['free_sample_ids'] = ThankJapanPremium.objects.filter(
                category=current_item.category
            ).order_by('timestamp').values_list('id', flat=True)[:12]
        context['related_items'] = ThankJapanPremium.objects.filter(
            category=current_item.category
        ).exclude(id=current_item.id).order_by('?')[:6]
        return context
    
                
                
def sitemap_view(request):
    free_items = ThankJapanModel.objects.all()
    premium_items_all = ThankJapanPremium.objects.all()
    
    free_cats = free_items.values_list('category', flat=True).distinct()
    premium_cats = premium_items_all.values_list('category', flat=True).distinct()
    
    unique_categories = set([cat.lower() for cat in free_cats] + [cat.lower() for cat in premium_cats])
    
    public_premium_items = []
    
    free_samples = ThankJapanPremium.objects.filter(category__in=["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment", "RomanceRelationships"]).order_by('timestamp')
    public_premium_items.extend(list(free_samples))

    other_categories = ThankJapanPremium.objects.exclude(category__in=["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment", "RomanceRelationships"]).values_list('category', flat=True).distinct()
    
    for cat in other_categories:
        samples = ThankJapanPremium.objects.filter(category=cat).order_by('timestamp')[:12]
        public_premium_items.extend(list(samples))
        
    prefectures = ['ishikawa', 'toyama', 'fukui'] 

    languages = [
        {'code': 'en', 'hreflang': 'en'},
        {'code': 'ja', 'hreflang': 'ja'},
        {'code': 'ko', 'hreflang': 'ko'},
        {'code': 'zh-cn', 'hreflang': 'zh-hans'},
        {'code': 'zh-hant', 'hreflang': 'zh-hant'},
        {'code': 'th', 'hreflang': 'th'},
        {'code': 'vi', 'hreflang': 'vi'},
        {'code': 'de', 'hreflang': 'de'},
        {'code': 'fr', 'hreflang': 'fr'},
        {'code': 'it', 'hreflang': 'it'},
        {'code': 'es-es', 'hreflang': 'es-es'},
        {'code': 'es-mx', 'hreflang': 'es-mx'},
        {'code': 'pt', 'hreflang': 'pt-pt'},
        {'code': 'pt-br', 'hreflang': 'pt-br'},
        {'code': 'en-in', 'hreflang': 'en-in'},
    ]

    context = {
        'free_items': free_items,
        'premium_items': public_premium_items,
        'categories': sorted(list(unique_categories)),
        'languages': languages,
        'prefectures': prefectures,
    }
    
    return render(request, 'sitemap.xml', context, content_type='application/xml')