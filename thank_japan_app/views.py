from django.shortcuts import render, redirect, get_object_or_404, HttpResponse, HttpResponseRedirect
from django.views import View
from django.views.generic import ListView, DetailView, FormView, TemplateView
from django.views.generic.edit import FormView
from .models import ThankJapanModel, Player, Profile, ThankJapanPremium
from django.contrib import messages
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.conf import settings
from .forms import AnswerForm, ContactForm, UsernameForm
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
from .models import WeeklyScore, ThankJapanBackgroundModel, FCMDevice, DailyQuestion
from .pricing import get_premium_price
from google.oauth2 import service_account
from googleapiclient.discovery import build
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from firebase_admin import messaging
from .firebase_utils import get_firebase_credentials
from allauth.socialaccount.views import ConnectionsView
from .achievements import check_and_unlock_achievements, get_achievement_progress, STREAK_ACHIEVEMENT_CODES
from datetime import date, timedelta
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
        context['unlocked_streak_achievements'] = self.request.session.pop('newly_unlocked_streak_achievements', [])
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
    # difficulty picker (all difficulties share the same 30s timer, see
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


    'sample_premium': {'category_filter': ['DailyConversation', 'slang', 'TourismEtiquette' ,'Entertainment'], 'jlpt_level': ['N5', 'N4', 'N3'], 'num_questions': 550, 'model_type': 'premium', 'dummy_count': 3},
    'n5_premium': {'jlpt_level': 'N5', 'num_questions': 50, 'model_type': 'premium', 'dummy_count': 3},
    'n4_premium': {'jlpt_level': 'N4', 'num_questions': 50, 'model_type': 'premium', 'dummy_count': 3},
    'n3_premium': {'jlpt_level': 'N3', 'num_questions': 50, 'model_type': 'premium', 'dummy_count': 3},
}

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
    })



def game_play(request):
    player, is_guest = get_current_player_info(request)
    premium_url_name, lang_code = get_lang_info(request)
    ids = request.session.get('game_question_ids', [])
    index = request.session.get('game_current_index', 0)
    is_premium_mode = request.session.get('is_premium_mode', False)

    if not ids or index >= len(ids):
        return redirect('game_start')

    current_time = time.time()
    
    frozen = request.session.get('frozen_seconds_left')
    if frozen is not None:
        game_end_time = current_time + int(frozen)
        request.session['game_end_time'] = game_end_time
        del request.session['frozen_seconds_left']
    else:
        game_end_time = request.session.get('game_end_time')
        if not game_end_time:
            time_limit = 31
            game_end_time = current_time + time_limit
            request.session['game_end_time'] = game_end_time

    seconds_left = int(game_end_time - current_time)
    difficulty = request.session.get('game_difficulty', 'normal')

    if difficulty != 'single' and seconds_left <= 0:
        return redirect('game_result')

    settings = DIFFICULTY_SETTINGS.get(difficulty, {})
    is_kanji_mode = settings.get('is_kanji_mode', False)

    model = ThankJapanPremium if is_premium_mode else ThankJapanModel
    question = get_object_or_404(model, id=ids[index])

    choice_index = request.session.get('choice_index_check')
    if choice_index == index and request.session.get('current_choices'):
        choice_ids = request.session.get('current_choices')
        choices = [get_object_or_404(model, id=cid) for cid in choice_ids]
    else:
        KARUTA_DUMMY_COUNT = settings.get('dummy_count', 1)
        dummy_pool = model.objects.filter(category=question.category).exclude(id=question.id).exclude(jpname=question.jpname)
        if is_kanji_mode:
            dummy_pool = dummy_pool.filter(kanji_name__regex=r'[一-龠]')
        if dummy_pool.count() < KARUTA_DUMMY_COUNT:
            dummy_pool = model.objects.exclude(id=question.id).exclude(jpname=question.jpname)
            if is_kanji_mode:
                dummy_pool = dummy_pool.filter(kanji_name__regex=r'[一-龠]')
        
        num_to_sample = min(dummy_pool.count(), KARUTA_DUMMY_COUNT)
        dummies = random.sample(list(dummy_pool), num_to_sample)
        choice_objects = [question] + dummies
        random.shuffle(choice_objects)
        choices = choice_objects
        request.session['current_choices'] = [c.id for c in choice_objects]
        request.session['choice_index_check'] = index

    db_answer = extract_base_name(question.name).lower()
    fire_game_start_event = request.session.pop('ga_fire_game_start', False)
    return render(request, 'thank_japan_app/game_play-v2.html', {
        'object': question,
        'choices': choices,
        'seconds_left': seconds_left,
        'show_result': False,
        'form': AnswerForm(),
        'current_index': index + 1,
        'total_questions': len(ids),
        'score': request.session.get('game_score', 0),
        'player': player,
        'is_guest': is_guest,
        'hint_length': len(db_answer),
        'difficulty': difficulty,
        'is_premium_mode': is_premium_mode,
        'is_kanji_mode': is_kanji_mode,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('game'),
        'bgm_page_type': 'game',
        'fire_game_start_event': fire_game_start_event,
    })
         

def game_answer(request, pk):
    if request.method != 'POST':
        return redirect('game_play')

    index = request.session.get('game_current_index', 0)

    player, is_guest = get_current_player_info(request)
    is_premium_mode = request.session.get('is_premium_mode', False)
    premium_url_name, lang_code = get_lang_info(request)

    model = ThankJapanPremium if is_premium_mode else ThankJapanModel
    question = get_object_or_404(model, id=pk)

    difficulty = request.session.get('game_difficulty', 'normal')
    settings = DIFFICULTY_SETTINGS.get(difficulty, {})
    is_kanji_mode = settings.get('is_kanji_mode', False)

    ids = request.session.get('game_question_ids', [])
    is_last_question = (index + 1) >= len(ids)
    choice_ids = request.session.get('current_choices', [])
    choices = [get_object_or_404(model, id=cid) for cid in choice_ids]
    current_time = time.time()
    game_end_time = request.session.get('game_end_time', current_time)
    seconds_left = int(game_end_time - current_time)

    
    if request.session.get('last_answered_index') == index:
        history = request.session.get('game_history', [])
        last_entry = history[-1] if history else {}
        return render(request, 'thank_japan_app/game_play-v2.html', {
            'object': question,
            'choices': choices,
            'user_input': last_entry.get('user_input', ''),
            'is_correct': last_entry.get('is_correct', False),
            'combo': last_entry.get('combo', 0),
            'show_result': True,
            'is_last_question': is_last_question,
            'current_index': index + 1,
            'total_questions': len(ids),
            'score': request.session.get('game_score', 0),
            'player': player,
            'is_guest': is_guest,
            'seconds_left': seconds_left,
            'difficulty': difficulty,
            'is_premium_mode': is_premium_mode,
            'is_kanji_mode': is_kanji_mode,
            'lang_code': lang_code,
            'bgm_url': get_bgm_url('game'),
            'bgm_page_type': 'game',
        })
    request.session['last_answered_index'] = index
    

    user_input = request.POST.get('answer')

    client_seconds_left = request.POST.get('seconds_left')
    if client_seconds_left:
        request.session['frozen_seconds_left'] = int(client_seconds_left)

    correct_flag = (str(user_input) == str(question.id))

    points = 0
    combo = 0
    combo_bonus = 0
    if correct_flag:
        # Base point for a correct answer: 1 for free-category questions, 2 for
        # premium-category questions. On top of that, a combo bonus grows with
        # the current streak of consecutive correct answers (reset on a miss):
        # 3-4 in a row: +1 / 5-6: +2 / 7+: +3.
        base_points = 2 if is_premium_mode else 1
        combo = request.session.get('game_combo', 0) + 1
        request.session['game_combo'] = combo
        request.session['game_max_combo'] = max(request.session.get('game_max_combo', 0), combo)
        if combo >= 7:
            combo_bonus = 3
        elif combo >= 5:
            combo_bonus = 2
        elif combo >= 3:
            combo_bonus = 1
        points = base_points + combo_bonus
        request.session['game_score'] = request.session.get('game_score', 0) + points
    else:
        request.session['game_combo'] = 0

    history = request.session.get('game_history', [])
    history.append({
        'question_id': question.id,
        'index': index,
        'is_correct': correct_flag,
        'user_input': question.name if correct_flag else "Wrong",
        'correct_answer': question.name,
        'combo': combo,
        'combo_bonus': combo_bonus,
        'points': points,
    })
    request.session['game_history'] = history

    return render(request, 'thank_japan_app/game_play-v2.html', {
        'object': question,
        'choices': choices,
        'user_input': user_input,
        'is_correct': correct_flag,
        'combo': combo,
        'show_result': True,
        'is_last_question': is_last_question,
        'current_index': index + 1,
        'total_questions': len(ids),
        'score': request.session.get('game_score', 0),
        'player': player,
        'is_guest': is_guest,
        'seconds_left': seconds_left,
        'difficulty': difficulty,
        'is_premium_mode': is_premium_mode,
        'is_kanji_mode': is_kanji_mode,  
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('game'),
        'bgm_page_type': 'game',
    })    
                
def game_next_question(request):
    _, lang_code = get_lang_info(request)
    request.session['game_current_index'] = request.session.get('game_current_index', 0) + 1
    if request.session['game_current_index'] >= len(request.session.get('game_question_ids', [])):
        return redirect(f"{reverse('game_result')}?lang={lang_code}")
    return redirect(f"{reverse('game_play')}?lang={lang_code}")



def game_restart(request):
    difficulty = request.GET.get('difficulty', 'normal')
    mode = request.GET.get('mode')
    player, is_guest = get_current_player_info(request)
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    if mode == 'single':
        model_type = request.GET.get('model_type')
        val = request.GET.get('slug') 
        
        if model_type == 'premium':
            question = get_object_or_404(ThankJapanPremium, slug=val)
             
            if question.category not in ["DailyConversation", "slang", "TourismEtiquette", "Entertainment"]:
                if not is_premium:
                    free_sample_ids = ThankJapanPremium.objects.filter(
                        category=question.category
                    ).order_by('-timestamp').values_list('id', flat=True)[:5]
                    
                    if question.id not in free_sample_ids:
                        url_name, _ = get_lang_info(request)
                        return redirect(url_name)
        
            is_premium_mode = True
        else:
            try:
                question = ThankJapanModel.objects.get(slug=val)
            except ThankJapanModel.DoesNotExist:
                question = get_object_or_404(ThankJapanModel, name=val)
            is_premium_mode = False
            
        selected_question_ids = [question.id]
        difficulty = 'single'
    else:
        

        current_settings = DIFFICULTY_SETTINGS.get(difficulty, DIFFICULTY_SETTINGS['normal'])
        is_premium_mode = current_settings.get('model_type') == 'premium'
        model = ThankJapanPremium if is_premium_mode else ThankJapanModel
        qs = model.objects.all()

        if current_settings.get('category_filter'): 
            qs = qs.filter(category__in=current_settings['category_filter'])

        if current_settings.get('jlpt_level'):
            jlpt_val = current_settings['jlpt_level']
            if isinstance(jlpt_val, list):
                qs = qs.filter(jlpt_level__in=jlpt_val)
            else:
                qs = qs.filter(jlpt_level=jlpt_val)

        if current_settings.get('is_kanji_mode'):
            qs = qs.filter(kanji_name__isnull=False).exclude(kanji_name="")
            qs = qs.filter(kanji_name__regex=r'[一-龠]')
            if current_settings.get('length_regex'):
                qs = qs.filter(kanji_name__iregex=current_settings['length_regex'])
        else:
            if current_settings.get('length_regex'):
                qs = qs.filter(name__iregex=current_settings['length_regex'])
        
        ids = list(qs.values_list('id', flat=True))
        random.shuffle(ids)
        selected_question_ids = ids[:current_settings['num_questions']]

    keys_to_clear = [
        'game_question_ids', 'game_current_index', 'game_score', 'game_difficulty',
        'game_history', 'score_saved', 'is_premium_mode',
        'game_end_time', 'current_choices', 'choice_index_check','last_answered_index',
        'game_combo', 'game_max_combo',
    ]
    for key in keys_to_clear: request.session.pop(key, None)

    request.session['game_question_ids'] = selected_question_ids
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0
    request.session['game_difficulty'] = difficulty
    request.session['is_premium_mode'] = is_premium_mode
    request.session['game_history'] = []
    # Consumed once by game_play to fire the GA4 'game_start' event, so it fires
    # exactly once per new game rather than on every question fetch.
    request.session['ga_fire_game_start'] = True

    _, lang_code = get_lang_info(request)
    response = redirect(f"{reverse('game_play')}?lang={lang_code}")

    # First-visit taste flow (3 questions, no ranking/registration nudges).
    # Mark the visitor as a returning player as soon as they click the
    # "try 3 questions" button, so leaving mid-flow still shows the normal
    # play button on their next visit (see TopView.get_context_data).
    if difficulty == 'first_taste':
        response.set_cookie(
            'tj_has_played', '1',
            max_age=34560000,  # ~400 days: the practical cap most browsers honor
            httponly=True, samesite='Lax', secure=request.is_secure(),
        )

    return response


# --- Daily Question (今日の一問) ---

DAILY_QUESTION_DUMMY_COUNT = 3  # -> 4 choices total (1 correct + 3 dummies)
DAILY_QUESTION_SESSION_ANSWERED_KEY = 'daily_question_answered_date'
DAILY_QUESTION_BANNER_DISMISSED_KEY = 'daily_question_banner_dismissed'

DAILY_QUESTION_SHARE_TEXT = {
    'ja': {'correct': '「今日の一問」で「{word}」正解しました🎉 ThankJapanで日本語クイズに挑戦しよう！',
           'incorrect': '「今日の一問」で「{word}」不正解でした…😢 ThankJapanで日本語クイズに挑戦しよう！'},
    'zh-hant': {'correct': '「今日一題」答對了「{word}」🎉 到ThankJapan挑戰日文小測驗吧！',
                'incorrect': '「今日一題」答錯了「{word}」…😢 到ThankJapan挑戰日文小測驗吧！'},
    'zh-cn': {'correct': '「今日一题」答对了「{word}」🎉 到ThankJapan挑战日语小测验吧！',
              'incorrect': '「今日一题」答错了「{word}」…😢 到ThankJapan挑战日语小测验吧！'},
    'ko': {'correct': '오늘의 한 문제에서 「{word}」 정답! 🎉 ThankJapan에서 일본어 퀴즈에 도전해보세요!',
           'incorrect': '오늘의 한 문제에서 「{word}」 오답…😢 ThankJapan에서 일본어 퀴즈에 도전해보세요!'},
    'de': {'correct': 'Bei der "Frage des Tages" "{word}" richtig beantwortet 🎉 Probier das Japanisch-Quiz auf ThankJapan!',
           'incorrect': 'Bei der "Frage des Tages" "{word}" leider falsch…😢 Probier das Japanisch-Quiz auf ThankJapan!'},
    'fr': {'correct': 'Question du jour : "{word}" trouvée 🎉 Essaie le quiz de japonais sur ThankJapan !',
           'incorrect': 'Question du jour : "{word}" ratée…😢 Essaie le quiz de japonais sur ThankJapan !'},
    'es-es': {'correct': '¡Pregunta del día "{word}" acertada! 🎉 ¡Prueba el quiz de japonés en ThankJapan!',
              'incorrect': 'Pregunta del día "{word}" fallada…😢 ¡Prueba el quiz de japonés en ThankJapan!'},
    'it': {'correct': 'Domanda del giorno "{word}" indovinata 🎉 Prova il quiz di giapponese su ThankJapan!',
           'incorrect': 'Domanda del giorno "{word}" sbagliata…😢 Prova il quiz di giapponese su ThankJapan!'},
    'pt': {'correct': 'Pergunta do dia "{word}" acertada 🎉 Experimente o quiz de japonês no ThankJapan!',
           'incorrect': 'Pergunta do dia "{word}" errada…😢 Experimente o quiz de japonês no ThankJapan!'},
    'vi': {'correct': 'Câu hỏi hôm nay "{word}" trả lời đúng 🎉 Hãy thử quiz tiếng Nhật trên ThankJapan!',
           'incorrect': 'Câu hỏi hôm nay "{word}" trả lời sai…😢 Hãy thử quiz tiếng Nhật trên ThankJapan!'},
    'th': {'correct': 'คำถามประจำวัน "{word}" ตอบถูก🎉 ลองเล่นควิซภาษาญี่ปุ่นที่ ThankJapan สิ!',
           'incorrect': 'คำถามประจำวัน "{word}" ตอบผิด…😢 ลองเล่นควิซภาษาญี่ปุ่นที่ ThankJapan สิ!'},
    'en': {'correct': 'Got today\'s ThankJapan Daily Question "{word}" right! 🎉 Try the Japanese quiz yourself!',
           'incorrect': 'Missed today\'s ThankJapan Daily Question "{word}"…😢 Try the Japanese quiz yourself!'},
}


def _daily_question_share_text(lang_code, is_correct, word):
    lang = {'es-mx': 'es-es', 'pt-br': 'pt', 'en-in': 'en'}.get(lang_code, lang_code)
    texts = DAILY_QUESTION_SHARE_TEXT.get(lang, DAILY_QUESTION_SHARE_TEXT['en'])
    template = texts['correct'] if is_correct else texts['incorrect']
    return template.format(word=f"{word.jpname} ({word.name})")


DAILY_QUESTION_HASHTAGS = {
    'ja': 'ThankJapan,今日の一問,日本語学習',
    'zh-hant': 'ThankJapan,學日文',
    'zh-cn': 'ThankJapan,学日语',
    'ko': 'ThankJapan,일본어공부',
    'de': 'ThankJapan,JapanischLernen',
    'fr': 'ThankJapan,ApprendreLeJaponais',
    'es-es': 'ThankJapan,AprenderJapones',
    'it': 'ThankJapan,ImparaGiapponese',
    'pt': 'ThankJapan,AprenderJapones',
    'vi': 'ThankJapan,HocTiengNhat',
    'th': 'ThankJapan,เรียนภาษาญี่ปุ่น',
    'en': 'ThankJapan,LearnJapanese',
}


def _daily_question_hashtags(lang_code, word):
    lang = {'es-mx': 'es-es', 'pt-br': 'pt', 'en-in': 'en'}.get(lang_code, lang_code)
    base = DAILY_QUESTION_HASHTAGS.get(lang, DAILY_QUESTION_HASHTAGS['en'])
    word_tag = ''.join(word.englishname.split())  # hashtags can't contain spaces
    return f"{base},{word_tag}" if word_tag else base


def _daily_question_choices(request, daily_question, today):
    """Build (and session-cache for the day) the 4 choice objects for today's question."""
    choices_key = f'daily_choices_{today.isoformat()}'
    choice_ids = request.session.get(choices_key)
    if choice_ids:
        choices = list(ThankJapanModel.objects.filter(id__in=choice_ids))
        choices.sort(key=lambda w: choice_ids.index(w.id))
        return choices

    word = daily_question.word
    dummy_pool = ThankJapanModel.objects.filter(category=word.category).exclude(id=word.id).exclude(jpname=word.jpname)
    if dummy_pool.count() < DAILY_QUESTION_DUMMY_COUNT:
        dummy_pool = ThankJapanModel.objects.exclude(id=word.id).exclude(jpname=word.jpname)
    num_to_sample = min(dummy_pool.count(), DAILY_QUESTION_DUMMY_COUNT)
    dummies = random.sample(list(dummy_pool), num_to_sample)
    choices = [word] + dummies
    random.shuffle(choices)
    request.session[choices_key] = [c.id for c in choices]
    return choices


def daily_question_view(request):
    premium_url_name, lang_code = get_lang_info(request)
    top_page_url = f"/?lang={lang_code}"
    today = timezone.localdate()  # TIME_ZONE='Asia/Tokyo' -> JST "today"
    already_answered = request.session.get(DAILY_QUESTION_SESSION_ANSWERED_KEY) == today.isoformat()

    daily_question, _ = DailyQuestion.objects.get_or_create_for_date(today)

    if daily_question is None:
        return render(request, 'thank_japan_app/daily_question.html', {
            'daily_state': 'unavailable',
            'lang_code': lang_code,
            'top_page_url': top_page_url,
        })

    word = daily_question.word

    if request.method == 'POST':
        if already_answered:
            return render(request, 'thank_japan_app/daily_question.html', {
                'daily_state': 'already_answered',
                'lang_code': lang_code,
                'top_page_url': top_page_url,
                'object': word,
            })

        chosen_id = request.POST.get('choice_id')
        is_correct = str(chosen_id) == str(word.id)
        request.session[DAILY_QUESTION_SESSION_ANSWERED_KEY] = today.isoformat()

        # Guests earn nothing (nowhere to persist it); logged-in users get a
        # flat 1pt for a correct answer, mirroring the scoring pattern in
        # game_result (views.py ~2290) so it shows up in the same ranking/
        # weekly-score displays. The "already answered today" gate above
        # already prevents this from running twice in one day.
        if is_correct and request.user.is_authenticated:
            profile = request.user.profile
            profile.total_score += 1
            profile.last_score = 1
            profile.save()
            player = Player.objects.filter(username=request.user.username).first()
            if player:
                player.total_score = profile.total_score
                player.save()
            week_start = WeeklyScore.get_current_week_start()
            weekly_record, _ = WeeklyScore.objects.get_or_create(user=request.user, week_start=week_start)
            weekly_record.score += 1
            weekly_record.save()

        share_text = _daily_question_share_text(lang_code, is_correct, word)
        share_url = f"https://www.thankjapan.com/daily/?lang={lang_code}"
        share_hashtags = _daily_question_hashtags(lang_code, word)

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
        })

    if already_answered:
        return render(request, 'thank_japan_app/daily_question.html', {
            'daily_state': 'already_answered',
            'lang_code': lang_code,
            'top_page_url': top_page_url,
            'object': word,
        })

    choices = _daily_question_choices(request, daily_question, today)

    return render(request, 'thank_japan_app/daily_question.html', {
        'daily_state': 'question',
        'object': word,
        'choices': choices,
        'lang_code': lang_code,
        'top_page_url': top_page_url,
        'is_twa': is_android_twa(request),
    })


COMBO_SHARE_ACHIEVEMENTS = {
    'high': {
        'ja': '🔥{max_combo}連続正解達成！ThankJapanの30秒チャレンジで{score}pt獲得！',
        'zh-hant': '🔥達成{max_combo}連續正確！在ThankJapan的30秒挑戰中獲得{score}分！',
        'zh-cn': '🔥达成{max_combo}连续正确！在ThankJapan的30秒挑战中获得{score}分！',
        'ko': '🔥{max_combo}연속 정답 달성! ThankJapan의 30초 챌린지에서 {score}점 획득!',
        'de': '🔥{max_combo} Treffer in Folge! {score} Punkte in der ThankJapan 30-Sekunden-Challenge!',
        'fr': "🔥{max_combo} bonnes réponses d'affilée ! {score} points dans le Défi 30 secondes de ThankJapan !",
        'es-es': '🔥¡{max_combo} aciertos seguidos! ¡{score} puntos en el Reto de 30 segundos de ThankJapan!',
        'it': '🔥{max_combo} risposte corrette di fila! {score} punti nella Sfida di 30 secondi di ThankJapan!',
        'pt': '🔥{max_combo} acertos seguidos! {score} pontos no Desafio de 30 segundos do ThankJapan!',
        'vi': '🔥Đạt {max_combo} câu đúng liên tiếp! Ghi {score} điểm trong Thử thách 30 giây của ThankJapan!',
        'th': '🔥ทำได้ {max_combo} คอมโบติดต่อกัน! ได้ {score} คะแนนใน 30-Second Challenge ของ ThankJapan!',
        'en': '🔥{max_combo}-combo streak! Scored {score}pts in the 30-Second Challenge on ThankJapan!',
    },
    'mid': {
        'ja': '⚡最高コンボ{max_combo}達成！ThankJapanの30秒チャレンジで{score}pt獲得！',
        'zh-hant': '⚡最高連擊{max_combo}！在ThankJapan的30秒挑戰中獲得{score}分！',
        'zh-cn': '⚡最高连击{max_combo}！在ThankJapan的30秒挑战中获得{score}分！',
        'ko': '⚡최대 콤보 {max_combo}! ThankJapan의 30초 챌린지에서 {score}점 획득!',
        'de': '⚡Maximal-Combo {max_combo}! {score} Punkte in der ThankJapan 30-Sekunden-Challenge!',
        'fr': '⚡Combo max {max_combo} ! {score} points dans le Défi 30 secondes de ThankJapan !',
        'es-es': '⚡¡Combo máximo {max_combo}! ¡{score} puntos en el Reto de 30 segundos de ThankJapan!',
        'it': '⚡Combo massima {max_combo}! {score} punti nella Sfida di 30 secondi di ThankJapan!',
        'pt': '⚡Combo máximo {max_combo}! {score} pontos no Desafio de 30 segundos do ThankJapan!',
        'vi': '⚡Combo tối đa {max_combo}! Ghi {score} điểm trong Thử thách 30 giây của ThankJapan!',
        'th': '⚡คอมโบสูงสุด {max_combo}! ได้ {score} คะแนนใน 30-Second Challenge ของ ThankJapan!',
        'en': '⚡Max combo {max_combo}! Scored {score}pts in the 30-Second Challenge on ThankJapan!',
    },
    'none': {
        'ja': 'ThankJapanの30秒チャレンジで{score}pt獲得！日本語、どれだけ知ってる？',
        'zh-hant': '在ThankJapan的30秒挑戰中獲得{score}分！你懂多少日文？',
        'zh-cn': '在ThankJapan的30秒挑战中获得{score}分！你懂多少日文？',
        'ko': 'ThankJapan의 30초 챌린지에서 {score}점 획득! 일본어 얼마나 아세요?',
        'de': '{score} Punkte in der ThankJapan 30-Sekunden-Challenge! Wie viel Japanisch kannst du?',
        'fr': 'Défi 30 secondes de ThankJapan : {score} points ! Et toi, combien de japonais connais-tu ?',
        'es-es': '¡{score} puntos en el Reto de 30 segundos de ThankJapan! ¿Cuánto japonés sabes tú?',
        'it': '{score} punti nella Sfida di 30 secondi di ThankJapan! Quanto giapponese conosci?',
        'pt': '{score} pontos no Desafio de 30 segundos do ThankJapan! Quanto japonês você sabe?',
        'vi': 'Ghi {score} điểm trong Thử thách 30 giây của ThankJapan! Bạn biết bao nhiêu tiếng Nhật?',
        'th': 'ได้ {score} คะแนนใน 30-Second Challenge ของ ThankJapan! คุณรู้ภาษาญี่ปุ่นแค่ไหน?',
        'en': 'Scored {score}pts in the 30-Second Challenge on ThankJapan! How much Japanese do you know?',
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

    # First-visit taste flow (3 questions, no ranking/registration nudges).
    # tj_has_played is now set as soon as the "try 3 questions" button is
    # clicked (see game_restart), not here.
    if request.session.get('game_difficulty') == 'first_taste':
        player, is_guest = get_current_player_info(request)
        quick_score = sum(h.get('points', 0) for h in history)
        quick_max_combo = max((h.get('combo', 0) for h in history), default=0)
        return render(request, 'thank_japan_app/game_result_quick-v2.html', {
            'lang_code': lang_code,
            'correct_count': correct_count,
            'total_played': total_played,
            'is_guest': is_guest,
            'is_twa': is_android_twa(request),
            'bgm_url': get_bgm_url('result'),
            'bgm_page_type': 'result',
            'share_message': build_combo_share_message(lang_code, quick_max_combo, quick_score),
            'share_url': 'https://www.thankjapan.com',
        })

    is_premium_mode = request.session.get('is_premium_mode', False)
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
    
    model = ThankJapanPremium if is_premium_mode else ThankJapanModel
    played_ids = [h['question_id'] for h in history]
    played_questions = model.objects.in_bulk(played_ids)
    review_data = [{'object': played_questions.get(h['question_id']), 'is_correct': h['is_correct'], 'user_input': h['user_input'], 'correct_answer': h.get('correct_answer')} for h in history if played_questions.get(h['question_id'])]

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

    return render(request, 'thank_japan_app/game_result-v2.html', {
        'lang_code': lang_code,
        'player': player,
        'score': score,
        'combo_bonus_total': combo_bonus_total,
        'max_combo': max_combo,
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
    
    
                                
#category select view

def category_list(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'en')

    return render(request, 'thank_japan_app/category/category_list.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    

def category_list_zhcn(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'zh-cn')

    return render(request, 'thank_japan_app/category/category_list_zh_cn.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
     

def category_list_zhhant(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'zh-hant')

    return render(request, 'thank_japan_app/category/category_list_zh_hant.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    

def category_list_vi(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'vi')

    return render(request, 'thank_japan_app/category/category_list_vi.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    

def category_list_th(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)

    lang_code = request.GET.get('lang', 'th')

    return render(request, 'thank_japan_app/category/category_list_th.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    
def category_list_pt(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'pt')

    return render(request, 'thank_japan_app/category/category_list_pt.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    

def category_list_pt_br(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'pt-br')

    return render(request, 'thank_japan_app/category/category_list_pt_br.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    

def category_list_ko(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'ko')

    return render(request, 'thank_japan_app/category/category_list_ko.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
     

def category_list_ja(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'ja')

    return render(request, 'thank_japan_app/category/category_list_ja.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    
 
def category_list_it(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'it')

    return render(request, 'thank_japan_app/category/category_list_it.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    
 
def category_list_fr(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'fr')

    return render(request, 'thank_japan_app/category/category_list_fr.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    

def category_list_es_mx(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'es-mx')

    return render(request, 'thank_japan_app/category/category_list_es_mx.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    

def category_list_es_es(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'es-es')

    return render(request, 'thank_japan_app/category/category_list_es_es.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    
 
def category_list_en_in(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'en-in')

    return render(request, 'thank_japan_app/category/category_list_en_in.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
    })
    
    
 
def category_list_de(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'has_premium_access', False)
    
    lang_code = request.GET.get('lang', 'de')

    return render(request, 'thank_japan_app/category/category_list_de.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
        'bgm_page_type': 'study', 
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
    }
    return render(request, 'thank_japan_app/account/account_settings-v2.html', context)


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

    def get_queryset(self):
        category = self.kwargs.get('category')
        return ThankJapanPremium.objects.filter(category__iexact=category).order_by('timestamp')

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        if obj.category not in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment"] and not is_premium:
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
                if moved_item.category in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment"] or is_premium:
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
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'has_premium_access', False)
        url_target_name = CATEGORY_URL_MAP.get(current_item.category, 'toppage')
        try:
            base_category_url = reverse(url_target_name)
        except:
            base_category_url = "/"
        context['category_list_url'] = f"{base_category_url}?lang={lang_code}"
        if current_item.category in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment"] or is_premium:
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
    
    free_samples = ThankJapanPremium.objects.filter(category__in=["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment"]).order_by('timestamp')
    public_premium_items.extend(list(free_samples))
    
    other_categories = ThankJapanPremium.objects.exclude(category__in=["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment"]).values_list('category', flat=True).distinct()
    
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