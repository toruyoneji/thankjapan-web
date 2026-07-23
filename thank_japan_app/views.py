from django.shortcuts import render, redirect, get_object_or_404, HttpResponse, HttpResponseRedirect
from django.views import View
from django.views.generic import ListView, DetailView, FormView, TemplateView
from django.views.generic.edit import FormView
from .models import ThankJapanModel, Player, Profile, ThankJapanPremium
from django.contrib import messages
from django.core.mail import send_mail
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
from django.utils.http import urlencode
from django.contrib.auth.views import PasswordResetView, PasswordResetConfirmView
from .context_processors import language_context
from .models import WeeklyScore, ThankJapanBackgroundModel
from google.oauth2 import service_account
from googleapiclient.discovery import build
from django.urls import reverse
import logging
import random
import re, itertools
import json
import paypalrestsdk
import requests
import time
import json






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
                        user.profile.is_premium = True 
                        user.profile.save()
                        return JsonResponse({'status': 'success', 'expiry': expiry_time_ms})
            
            return JsonResponse({'status': 'error', 'message': '検証に失敗したか、期限切れです'}, status=400)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

#android google play
def is_android_twa(request):
    
    ua = request.META.get('HTTP_USER_AGENT', '').lower()
    
    return 'android' in ua or 'twa' in ua

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
        # URLパラメータから取得、なければセッション、なければ en
        lang = self.request.GET.get('lang') or self.request.session.get('tj_lang_code', 'en')
        context['lang_code'] = lang
        return context

    def get_success_url(self):
        # 次の画面（Done画面）への遷移時にも言語を確実に引き継ぐ
        lang = self.request.GET.get('lang') or self.request.session.get('tj_lang_code', 'en')
        return reverse('password_reset_done') + f"?lang={lang}"

    def form_valid(self, form):
        # 重要：メールの内容（extra_email_context）に渡す言語をここで確定させる
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
        today = timezone.now().date()

        
        if profile.last_bonus_date != today:
            
            profile.total_score += 1
            profile.last_bonus_date = today
            profile.save()

            
            player, created = Player.objects.get_or_create(username=request.user.username)
            player.total_score = profile.total_score 
            player.save()
            
            
            request.session['show_bonus_toast'] = True
    else:
        request.session['show_guest_bonus_alert'] = True
        
def get_bgm_url(page_type):
    
    try:
        record = ThankJapanBackgroundModel.objects.filter(
            page_type=page_type, sound__isnull=False
        ).first()
        return record.sound.url if record else None
    except AttributeError:
        return None
            

#country top page

class BGMContextMixin:
    bgm_page_type = None  

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.bgm_page_type:
            context['bgm_url'] = get_bgm_url(self.bgm_page_type)
        return context

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
            
            messages.success(request, "Account created! Please log in.")
            
            keys_to_clear = ['is_guest', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty', 'player_id']
            for key in keys_to_clear:
                request.session.pop(key, None)
            
            request.session['tj_lang_code'] = lang_code
            
            login_url = reverse('player_login')
            query_params = urlencode({'next': next_url, 'lang': lang_code})
            return redirect(f"{login_url}?{query_params}")

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
            
            return redirect(next_url)
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
    'easy': {'category_filter': ['sports', 'food', 'animal', 'dailyactions'], 'length_regex': r'^.{1,20}$', 'num_questions': 50, 'model_type': 'free'},
    'normal': {'category_filter': ['cook', 'food', 'culture', 'body', 'live', 'work', 'dailyactions'], 'length_regex': r'^.{1,9}$', 'num_questions': 50, 'model_type': 'free'},
    'hard': {'category_filter': None, 'length_regex': r'^.{1,9}$', 'num_questions': 50, 'model_type': 'free'},
    'super_hard': {'category_filter': None, 'length_regex': None, 'num_questions': 50, 'model_type': 'free'},
    
    'kanji1': {
        'category_filter': ['nature', 'food', 'cook', 'animal', 'building', 'dailyactions'],
        'length_regex': r'^.{1,3}$', 
        'num_questions': 50,
        'model_type': 'free',
        'is_kanji_mode': True,
    },
    
        'kanji2': {
        'category_filter': ['culture', 'work', 'fashion', 'flower', 'householditems', 'sports', 'body'],
        'length_regex': r'^.{1,3}$',  
        'num_questions': 50,
        'model_type': 'free',
        'is_kanji_mode': True,
    },

    
    'sample_premium': {'category_filter': ['DailyConversation', 'slang', 'TourismEtiquette' ,'Entertainment'], 'jlpt_level': ['N5', 'N4', 'N3'], 'num_questions': 550, 'model_type': 'premium'},
    'n5_premium': {'jlpt_level': 'N5', 'num_questions': 50, 'model_type': 'premium'},
    'n4_premium': {'jlpt_level': 'N4', 'num_questions': 50, 'model_type': 'premium'},
    'n3_premium': {'jlpt_level': 'N3', 'num_questions': 50, 'model_type': 'premium'},
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
            game_end_time = current_time + 61
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
        KARUTA_DUMMY_COUNT = 5
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
    request.session['question_start_time'] = time.time()
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
            'reaction_time': last_entry.get('reaction_time'),
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
        })
    request.session['last_answered_index'] = index
    

    user_input = request.POST.get('answer', '').strip().lower()
    
    question_start_time = request.session.get('question_start_time')
    if question_start_time:
        reaction_time = round(time.time() - question_start_time, 1)
    else:
        reaction_time = None
    
    client_seconds_left = request.POST.get('seconds_left')
    if client_seconds_left:
        request.session['frozen_seconds_left'] = int(client_seconds_left)

    user_answer_cleaned = extract_base_name(user_input).lower()
    db_answer_cleaned = extract_base_name(question.name).lower()
    correct_flag = (user_answer_cleaned == db_answer_cleaned)
    
    points = 0
    if correct_flag:
        if reaction_time is not None and reaction_time < 2:
            points = 3
        elif reaction_time is not None and reaction_time < 4:
            points = 2
        else:
            points = 1
        request.session['game_score'] = request.session.get('game_score', 0) + points

    history = request.session.get('game_history', [])
    history.append({
        'question_id': question.id,
        'is_correct': correct_flag,
        'user_input': user_input,
        'correct_answer': question.name,
        'reaction_time': reaction_time,
        'points': points,
    })
    request.session['game_history'] = history

    return render(request, 'thank_japan_app/game_play-v2.html', {
        'object': question,
        'choices': choices,
        'user_input': user_input,
        'is_correct': correct_flag,
        'reaction_time': reaction_time,
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
    })    
                
def game_next_question(request):
    request.session['game_current_index'] = request.session.get('game_current_index', 0) + 1
    if request.session['game_current_index'] >= len(request.session.get('game_question_ids', [])):
        return redirect('game_result')
    return redirect('game_play')    




def game_restart(request):
    difficulty = request.GET.get('difficulty', 'normal')
    mode = request.GET.get('mode')
    player, is_guest = get_current_player_info(request)
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)

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
    ]
    for key in keys_to_clear: request.session.pop(key, None)

    request.session['game_question_ids'] = selected_question_ids
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0
    request.session['game_difficulty'] = difficulty
    request.session['is_premium_mode'] = is_premium_mode
    request.session['game_history'] = []
    
    return redirect('game_play')



def game_result(request):
    _, lang_code = get_lang_info(request)
    score = int(request.session.get('game_score', 0))
    history = request.session.get('game_history', [])
    time_bonus_total = sum(
        (h.get('points', 1) - 1) for h in history if h.get('is_correct')
    )
    correct_count = sum(1 for h in history if h.get('is_correct'))
    
    player, is_guest = get_current_player_info(request)
    is_premium_mode = request.session.get('is_premium_mode', False)
    difficulty = request.session.get('game_difficulty')

    if not request.session.get('score_saved', False) and score > 0:
        if not is_guest and request.user.is_authenticated:
            profile = request.user.profile
            profile.total_score += score
            profile.last_score = score
            profile.save()

            player.total_score = profile.total_score
            player.last_score = score
            player.country = profile.country
            player.save()
            
            week_start = WeeklyScore.get_current_week_start()
            weekly_record, created = WeeklyScore.objects.get_or_create(
                user=request.user,
                week_start=week_start
            )
            weekly_record.score += score
            weekly_record.country = profile.country 
            weekly_record.save()
        
        request.session['score_saved'] = True
        
    current_rank = None
    total_registered = 0
    if not is_guest:
        
        registered_players = Player.objects.exclude(username__icontains="Guest")
        total_registered = registered_players.count()
        
        higher_scores_count = registered_players.filter(total_score__gt=player.total_score).count()
        current_rank = higher_scores_count + 1
    
    
    model = ThankJapanPremium if is_premium_mode else ThankJapanModel
    played_ids = [h['question_id'] for h in history]
    played_questions = model.objects.in_bulk(played_ids)

    review_data = []
    for h in history:
        q = played_questions.get(h['question_id'])
        if q: 
            review_data.append({
                'object': q, 
                'is_correct': h['is_correct'], 
                'user_input': h['user_input'], 
                'correct_answer': h.get('correct_answer', q.name)
            })

    ranking = Player.objects.exclude(username__icontains="Guest").order_by('-total_score')[:20]
    
    current_week = WeeklyScore.get_current_week_start()
    raw_weekly_ranking = WeeklyScore.objects.filter(
        week_start=current_week
    ).order_by('-score')[:10]
    
    weekly_ranking = []
    last_score = None
    current_rank = 0

    for i, r in enumerate(raw_weekly_ranking, 1):
        if r.score != last_score:
            current_rank = i  
        
        r.display_rank = current_rank
        weekly_ranking.append(r)
        last_score = r.score

    return render(request, 'thank_japan_app/game_result-v2.html', {
        'lang_code': lang_code,
        'player': player, 
        'score': score, 
        'time_bonus_total': time_bonus_total,
        'correct_count': correct_count, 
        'total_played': len(history),
        'is_guest': is_guest, 
        'review_data': review_data, 
        'difficulty': difficulty,
        'is_premium_mode': is_premium_mode,
        'ranking': ranking,
        'weekly_ranking': weekly_ranking,
        'current_rank': current_rank,         
        'total_registered': total_registered,
        'bgm_url': get_bgm_url('result'),
        
    })    
                            
#category select view

def category_list(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)

    lang_code = request.GET.get('lang', 'en')

    return render(request, 'thank_japan_app/category/category_list.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    

def category_list_zhcn(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)

    lang_code = request.GET.get('lang', 'zh-cn')

    return render(request, 'thank_japan_app/category/category_list_zh_cn.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
     

def category_list_zhhant(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)

    lang_code = request.GET.get('lang', 'zh-hant')

    return render(request, 'thank_japan_app/category/category_list_zh_hant.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    

def category_list_vi(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)

    lang_code = request.GET.get('lang', 'vi')

    return render(request, 'thank_japan_app/category/category_list_vi.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    

def category_list_th(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)

    lang_code = request.GET.get('lang', 'th')

    return render(request, 'thank_japan_app/category/category_list_th.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    
def category_list_pt(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'pt')

    return render(request, 'thank_japan_app/category/category_list_pt.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    

def category_list_pt_br(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'pt-br')

    return render(request, 'thank_japan_app/category/category_list_pt_br.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    

def category_list_ko(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'ko')

    return render(request, 'thank_japan_app/category/category_list_ko.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
     

def category_list_ja(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'ja')

    return render(request, 'thank_japan_app/category/category_list_ja.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    
 
def category_list_it(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'it')

    return render(request, 'thank_japan_app/category/category_list_it.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    
 
def category_list_fr(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'fr')

    return render(request, 'thank_japan_app/category/category_list_fr.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    

def category_list_es_mx(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'es-mx')

    return render(request, 'thank_japan_app/category/category_list_es_mx.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    

def category_list_es_es(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'es-es')

    return render(request, 'thank_japan_app/category/category_list_es_es.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    
 
def category_list_en_in(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'en-in')

    return render(request, 'thank_japan_app/category/category_list_en_in.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    
 
def category_list_de(request):
    
    is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
    
    lang_code = request.GET.get('lang', 'de')

    return render(request, 'thank_japan_app/category/category_list_de.html', {
        'is_premium': is_premium,
        'lang_code': lang_code,
        'bgm_url': get_bgm_url('study'),
    })
    
    
 
 
#category view
                            
class FoodView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/food.html"
    paginate_by = 200
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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


@login_required
@require_POST
def update_premium_status(request):
    try:
        data = json.loads(request.body)
        subscription_id = data.get('subscriptionID')
        
        if not subscription_id:
            return JsonResponse({'status': 'error'}, status=400)

        profile, created = Profile.objects.get_or_create(user=request.user)
        
        profile.is_premium = True
        profile.paypal_subscription_id = subscription_id
        profile.save()
        
        return JsonResponse({'status': 'success'})

    except Exception as e:
        print(f"Update Error: {str(e)}")
        return JsonResponse({'status': 'error'}, status=500)

@csrf_exempt
def paypal_webhook(request):
    
    try:
        data = json.loads(request.body)
        event_type = data.get('event_type')
        resource = data.get('resource')
        subscription_id = resource.get('id')

        
        trigger_events = [
            "BILLING.SUBSCRIPTION.CANCELLED",
            "BILLING.SUBSCRIPTION.SUSPENDED",
            "BILLING.SUBSCRIPTION.EXPIRED",
            "BILLING.SUBSCRIPTION.PAYMENT.FAILED"
        ]

        if event_type in trigger_events:
            Profile.objects.filter(paypal_subscription_id=subscription_id).update(is_premium=False)
            print(f"Webhook Handled: {subscription_id} to Free")

        return HttpResponse(status=200)

    except Exception as e:
        print(f"Webhook Error: {str(e)}")
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
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info-v2.html', context)

def premium_infoZHCN(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_zh_cn-v2.html', context)


def premium_infoZHHANT(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_zh_hant-v2.html', context)

def premium_infoVI(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_vi-v2.html', context)

def premium_infoTH(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_th-v2.html', context)

def premium_infoPT(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_pt-v2.html', context)

def premium_infoPTBR(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_pt_br-v2.html', context)

def premium_infoKO(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_ko-v2.html', context)

def premium_infoJA(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_ja-v2.html', context)

def premium_infoIT(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_it-v2.html', context)

def premium_infoFR(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_fr-v2.html', context)

def premium_infoESMX(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_es_mx-v2.html', context)

def premium_infoESES(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_es_es-v2.html', context)

def premium_infoENIN(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
    return render(request, 'thank_japan_app/premium/premium_info_en_in-v2.html', context)

def premium_infoDE(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
        'is_twa': is_android_twa(request),
    }
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
        'is_twa': is_android_twa(request)  
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request) 
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
        'is_twa': is_android_twa(request)  
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
        'is_twa': is_android_twa(request)  
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
        'is_twa': is_android_twa(request)  
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
        'is_twa': is_android_twa(request)  
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

@login_required
@require_POST
def downgrade_premium(request):
    profile = request.user.profile
    if profile.is_premium and profile.paypal_subscription_id:
        try:
            cancel_paypal_subscription(profile.paypal_subscription_id, "User downgraded")
        except Exception:
            pass
    profile.is_premium = False
    profile.save()

    next_url_name = request.POST.get('downgrade_url_name', 'downgrade_success')
        
    lang_code = request.session.get('tj_lang_code', 'en')

    
    response = redirect(next_url_name)
    
    
    
    try:
        target_url = reverse(next_url_name)
        return redirect(f"{target_url}?lang={lang_code}")
    except:
        return response
    
    
    
@login_required
@require_POST
def delete_account(request):
    
    lang_code = request.GET.get('lang') or request.session.get('tj_lang_code', 'en')
    
    username = request.user.username
    user = request.user
    profile = user.profile
    
    if profile.is_premium and profile.paypal_subscription_id:
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
        return redirect(f"{target_url}?lang={lang_code}")
    except:
        return redirect(next_url_name)
    
    

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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
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
    bgm_page_type = 'study'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="BusinessJapanese").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        if not is_premium:
            return qs[:6]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="BusinessJapanese")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 6)
        else:
            context['is_locked'] = False
        return context

class LivingInJapanView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/living_in_japan.html"
    paginate_by = 200
    bgm_page_type = 'study'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="LivingInJapan").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        if not is_premium:
            return qs[:6]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="LivingInJapan")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 6)
        else:
            context['is_locked'] = False
        return context

class MedicalEmergencyView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/medical_emergency.html"
    paginate_by = 200
    bgm_page_type = 'study'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="MedicalEmergency").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        if not is_premium:
            return qs[:6]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="MedicalEmergency")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 6)
        else:
            context['is_locked'] = False
        return context

class RealestateRulesView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/realestate_rules.html"
    paginate_by = 200
    bgm_page_type = 'study'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="RealEstateRules").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        if not is_premium:
            return qs[:6]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="RealEstateRules")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 6)
        else:
            context['is_locked'] = False
        return context

class PrefectureView(BGMContextMixin, ListView):
    template_name = "thank_japan_app/prefecture.html"
    paginate_by = 200
    bgm_page_type = 'study'
    
    def dispatch(self, request, *args, **kwargs):
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
        if not is_premium and request.GET.get('page', '1') != '1':
            url_name, lang_code = get_lang_info(request)
            return redirect(f"{reverse(url_name)}?lang={lang_code}") 
        return super().dispatch(request, *args, **kwargs)
    
    def get_queryset(self):
        qs = ThankJapanPremium.objects.filter(category="Prefectures").order_by('timestamp')
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        if not is_premium:
            return qs[:6]
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_premium_qs = ThankJapanPremium.objects.filter(category="Prefectures")
        total_count = all_premium_qs.count()
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        url_name, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code
        context['premium_url_name'] = url_name
        context['is_twa'] = is_android_twa(self.request)
        if not is_premium:
            context['is_locked'] = True
            context['hidden_count'] = max(0, total_count - 6)
        else:
            context['is_locked'] = False
        return context    

               
# free detail view
class CategoryDetailView(DetailView):
    model = ThankJapanModel
    template_name = "thank_japan_app/thankjapanmodel_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get(self, request, *args, **kwargs):
        category = self.kwargs.get('category')
        slug = self.kwargs.get('slug')

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
        
        _, lang_code = get_lang_info(self.request)
        context['lang_code'] = lang_code

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
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
        if obj.category not in ["DailyConversation", "slang", "TourismEtiquette" ,"Entertainment"] and not is_premium:
            free_sample_ids = ThankJapanPremium.objects.filter(
                category__iexact=obj.category
            ).order_by('timestamp').values_list('id', flat=True)[:6]
            if obj.id not in free_sample_ids:
                raise Http404
        return obj

    def dispatch(self, request, *args, **kwargs):
        category = self.kwargs.get('category')
        slug = self.kwargs.get('slug')
        is_premium = request.user.is_authenticated and getattr(request.user.profile, 'is_premium', False)
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
        current_item = self.object
        url_name, lang_code = get_lang_info(self.request)
        context['premium_url_name'] = url_name
        context['lang_code'] = lang_code
        context['is_twa'] = is_android_twa(self.request)
        is_premium = self.request.user.is_authenticated and getattr(self.request.user.profile, 'is_premium', False)
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
            ).order_by('timestamp').values_list('id', flat=True)[:6]
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
        samples = ThankJapanPremium.objects.filter(category=cat).order_by('timestamp')[:6]
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