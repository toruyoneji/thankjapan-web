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
from django.http import Http404
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.views.decorators.csrf import csrf_exempt
import logging
import random
import re
import json
import paypalrestsdk




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

    text = text.replace("nn", "n")
    return text

def normalize_for_judge(text):
    if not text: return ""
    text = text.lower().strip()
    text = text.replace('wa', 'ha')
    text = re.sub(r'[^a-z0-9\-]', '', text)
    text = re.sub(r'(a)\-', r'aa', text)
    text = re.sub(r'(i)\-', r'ii', text)
    text = re.sub(r'(u)\-', r'uu', text)
    text = re.sub(r'(e)\-', r'ee', text)
    text = re.sub(r'(o)\-', r'oo', text)
    text = text.replace('ou', 'oo')
    repls = [('tsu','tu'),('fu','hu'),('shi','si'),('chi','ti'),('ji','zi'),('sh','sy'),('ch','ty'),('jy','zy'),('nn','n')]
    for old, new in repls: text = text.replace(old, new)
    return text

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
    'DailyConversation': 'dailyconversation',
    'BusinessJapanese': 'businessjapanese',
    'LivingInJapan': 'living_in_japan_page',
}


#new-privacy-policy

@login_required
@require_POST
def update_policy_agreement(request):
    profile = request.user.profile
    profile.privacy_policy_version = "2026-01"
    profile.save()
    return JsonResponse({'status': 'success'})

    
#company infomation
class CompanyFormView(TemplateView):
     template_name = 'thank_japan_app/info/company.html'
     
class CompanyFormZHHANTView(TemplateView):
     template_name = 'thank_japan_app/info/company_zh_hant.html'
     
class CompanyFormVIView(TemplateView):
     template_name = 'thank_japan_app/info/company_vi.html'
     
class CompanyFormTHView(TemplateView):
     template_name = 'thank_japan_app/info/company_th.html'
     
class CompanyFormPTView(TemplateView):
     template_name = 'thank_japan_app/info/company_pt.html'
     
class CompanyFormPTBRView(TemplateView):
     template_name = 'thank_japan_app/info/company_pt_br.html'
     
class CompanyFormKOView(TemplateView):
     template_name = 'thank_japan_app/info/company_ko.html'
     
class CompanyFormJAView(TemplateView):
     template_name = 'thank_japan_app/info/company_ja.html'
     
class CompanyFormITView(TemplateView):
     template_name = 'thank_japan_app/info/company_it.html'
     
class CompanyFormFRView(TemplateView):
     template_name = 'thank_japan_app/info/company_fr.html'
     
class CompanyFormESMXView(TemplateView):
     template_name = 'thank_japan_app/info/company_es_mx.html'
     
class CompanyFormESESView(TemplateView):
     template_name = 'thank_japan_app/info/company_es_es.html'
     
class CompanyFormENINView(TemplateView):
     template_name = 'thank_japan_app/info/company_en_in.html'
     
class CompanyFormDEView(TemplateView):
     template_name = 'thank_japan_app/info/company_de.html'
     
#legalnotice

class LegalNoticeView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice.html"
     
class LegalNoticeZHHANTView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_zh_hant.html"
    
class LegalNoticeVIView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_vi.html"
    
class LegalNoticeTHView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_th.html"
    
class LegalNoticePTView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_pt.html"
    
class LegalNoticePTBRView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_pt_br.html"
    
class LegalNoticeKOView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_ko.html"
    
class LegalNoticeJAView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_ja.html"
    
class LegalNoticeITView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_it.html"
    
class LegalNoticeFRView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_fr.html"
    
class LegalNoticeESMXView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_es_mx.html"
    
class LegalNoticeESESView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_es_es.html"
    
class LegalNoticeENINView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_en_in.html"
    
class LegalNoticeDEView(TemplateView):
    template_name = "thank_japan_app/legal/legal_notice_de.html"
    
    
    
#privacypolicy    
class PrivacyPolicy(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy.html"
    
class PrivacyPolicyZHHANT(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_zh_hant.html"
    
class PrivacyPolicyVI(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_vi.html"
    
class PrivacyPolicyTH(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_th.html"
    
class PrivacyPolicyPT(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_pt.html"
    
class PrivacyPolicyPTBR(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_pt_br.html"
    
class PrivacyPolicyKO(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_ko.html"
    
class PrivacyPolicyJA(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_ja.html"
    
class PrivacyPolicyIT(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_it.html"
    
class PrivacyPolicyFR(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_fr.html"
    
class PrivacyPolicyESMX(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_es_mx.html"
    
class PrivacyPolicyESES(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_es_es.html"
    
class PrivacyPolicyENIN(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_en_in.html"

class PrivacyPolicyDE(TemplateView):
    template_name = "thank_japan_app/privacy/privacy_policy_de.html"
    
    
#riyoukiyaku    
class KiyakuView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku.html"
    model = ThankJapanModel
    
class KiyakuZHHANTView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_zh_hant.html"
    model = ThankJapanModel
    
class KiyakuVIView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_vi.html"
    model = ThankJapanModel
    
class KiyakuTHView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_th.html"
    model = ThankJapanModel
    
class KiyakuPTView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_pt.html"
    model = ThankJapanModel
    
class KiyakuPTBRView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_pt_br.html"
    model = ThankJapanModel
    
class KiyakuKOView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_ko.html"
    model = ThankJapanModel
    
class KiyakuJAView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_ja.html"
    model = ThankJapanModel
    
class KiyakuITView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_it.html"
    model = ThankJapanModel
    
class KiyakuFRView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_fr.html"
    model = ThankJapanModel
    
class KiyakuESMXView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_es_mx.html"
    model = ThankJapanModel
    
class KiyakuESESView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_es_es.html"
    model = ThankJapanModel

class KiyakuENINView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_en_in.html"
    model = ThankJapanModel
    
class KiyakuDEView(ListView):
    template_name = "thank_japan_app/kiyaku/riyoukiyaku_de.html"
    model = ThankJapanModel
    

#country page
class TopView(ListView):
    template_name = "thank_japan_app/toppage/toppage.html"
    model = ThankJapanModel
    
class TopViewJA(ListView):
    template_name = "thank_japan_app/toppage/toppage_ja.html"
    model = ThankJapanModel
    
class TopViewVI(ListView):
    template_name = "thank_japan_app/toppage/toppage_vi.html"
    model = ThankJapanModel
    
class TopViewFR(ListView):
    template_name = "thank_japan_app/toppage/toppage_fr.html"
    model = ThankJapanModel
    
class TopViewIT(ListView):
    template_name = "thank_japan_app/toppage/toppage_it.html"
    model = ThankJapanModel
    
class TopViewPT(ListView):
    template_name = "thank_japan_app/toppage/toppage_pt.html"
    model = ThankJapanModel
    
class TopViewZHHANT(ListView):
    template_name = "thank_japan_app/toppage/toppage_zh_hant.html"
    model = ThankJapanModel
    
class TopViewKO(ListView):
    template_name = "thank_japan_app/toppage/toppage_ko.html"
    model = ThankJapanModel

class TopViewESES(ListView):
    template_name = "thank_japan_app/toppage/toppage_es_es.html"
    model = ThankJapanModel
    
class TopViewDE(ListView):
    template_name = "thank_japan_app/toppage/toppage_de.html"
    model = ThankJapanModel
    
class TopViewTH(ListView):
    template_name = "thank_japan_app/toppage/toppage_th.html"
    model = ThankJapanModel
    
class TopViewPTBR(ListView):
    template_name = "thank_japan_app/toppage/toppage_pt_br.html"
    model = ThankJapanModel
    
class TopViewESMX(ListView):
    template_name = "thank_japan_app/toppage/toppage_es_mx.html"
    model = ThankJapanModel
    
class TopViewENIN(ListView):
    template_name = "thank_japan_app/toppage/toppage_en_in.html"
    model = ThankJapanModel


# views.py
class CategoryDetailView(DetailView):
    model = ThankJapanModel
    template_name = "thank_japan_app/thankjapanmodel_detail.html"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        category = self.kwargs['category']
        return ThankJapanModel.objects.filter(category=category)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_item = self.object
        context['related_items'] = ThankJapanModel.objects.filter(
            category=current_item.category
        ).exclude(
            id=current_item.id
        ).order_by('?')[:6]

        url_name = CATEGORY_URL_MAP.get(current_item.category, 'category_list')
        context['category_list_url'] = reverse(url_name)
        
        return context     

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
    if request.method == "POST":
        form = UsernameForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            email = form.cleaned_data['email']
            raw_password = form.cleaned_data['password']
            country = form.cleaned_data['country']


            if User.objects.filter(username=username).exists() or Player.objects.filter(username=username).exists():
                messages.error(request, "This username is already taken.")
                return render(request, 'thank_japan_app/player_register.html', {'form': form})

            
            if User.objects.filter(email=email).exists() or Player.objects.filter(email=email).exists():
                messages.error(request, "This email address is already registered.")
                return render(request, 'thank_japan_app/player_register.html', {'form': form})

            
            user = User.objects.create_user(username=username, email=email, password=raw_password)
            
            player = Player(username=username, email=email, country=country)
            player.set_password(raw_password)
            player.save()

            if hasattr(user, 'profile'):
                user.profile.country = country
                user.profile.save()
            
            messages.success(request, "Account created! Please log in to start playing.")
            
            keys_to_clear = ['is_guest', 'game_score', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty', 'player_id']
            for key in keys_to_clear:
                request.session.pop(key, None)
            
            return redirect('player_login') 
    else:
        form = UsernameForm()

    return render(request, 'thank_japan_app/player_register.html', {'form': form})


def player_login(request):
    
    next_url = request.GET.get('next') or request.POST.get('next') or 'toppage'

    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            keys_to_clear = ['is_guest', 'game_score', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty']
            for key in keys_to_clear:
                request.session.pop(key, None)
            
            auth_login(request, user)
            
            try:
                player = Player.objects.get(username=username)
                request.session['player_id'] = player.id
            except Player.DoesNotExist:
                pass

            request.session['is_guest'] = False 

            messages.success(request, f"Welcome back, {user.username}!")
            
            # 固定の 'toppage' ではなく取得した next_url へリダイレクト
            return redirect(next_url)
        else:
            messages.error(request, "Invalid username or password.")
            
    return render(request, 'thank_japan_app/player_login.html', {'next': next_url})


def player_logout(request):
    auth_logout(request)
    
    keys_to_clear = ['player_id', 'game_question_ids', 'game_current_index', 'game_score', 'game_message', 'last_question_info', 'game_difficulty']
    for key in keys_to_clear:
        request.session.pop(key, None)
    
    request.session['is_guest'] = True 
    messages.info(request, "You have been logged out.")
    return redirect('player_login')

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
    'easy': {'category_filter': ['sports', 'food'], 'length_regex': r'^.{1,20}$', 'num_questions': 3, 'model_type': 'free'},
    'normal': {'category_filter': ['cook', 'food', 'culture'], 'length_regex': r'^.{1,9}$', 'num_questions': 5, 'model_type': 'free'},
    'hard': {'category_filter': None, 'length_regex': r'^.{1,9}$', 'num_questions': 7, 'model_type': 'free'},
    'super_hard': {'category_filter': None, 'length_regex': None, 'num_questions': 7, 'model_type': 'free'},
    'sample_premium': {'category_filter': ['DailyConversation'], 'jlpt_level': 'N5', 'num_questions': 5, 'model_type': 'premium'},
    'n5_premium': {'jlpt_level': 'N5', 'num_questions': 5, 'model_type': 'premium'},
    'n4_premium': {'jlpt_level': 'N4', 'num_questions': 5, 'model_type': 'premium'},
    'n3_premium': {'jlpt_level': 'N3', 'num_questions': 5, 'model_type': 'premium'},
}

def get_current_player_info(request):
    player = None
    is_guest = True
    if request.user.is_authenticated:
        is_guest = False
        player, _ = Player.objects.get_or_create(username=request.user.username)
    elif 'player_id' in request.session:
        try:
            player = Player.objects.get(id=request.session['player_id'])
            is_guest = False
        except Player.DoesNotExist:
            is_guest = True
    if is_guest:
        player = Player(username='guest', country='Guestland')
    return player, is_guest

def game_start(request):
    player, is_guest = get_current_player_info(request)
    return render(request, 'thank_japan_app/game_start.html', {'player': player, 'is_guest': is_guest})

def game_play(request):
    player, is_guest = get_current_player_info(request)
    ids = request.session.get('game_question_ids', [])
    index = request.session.get('game_current_index', 0)
    is_premium_mode = request.session.get('is_premium_mode', False)

    if not ids or index >= len(ids): return redirect('game_start')

    model = ThankJapanPremium if is_premium_mode else ThankJapanModel
    question = get_object_or_404(model, id=ids[index])
    
    db_answer = extract_base_name(question.name).lower()
    return render(request, 'thank_japan_app/game_play.html', {
        'object': question, 'form': AnswerForm(), 'current_index': index + 1,
        'total_questions': len(ids), 'score': request.session.get('game_score', 0),
        'player': player, 'is_guest': is_guest, 'hint_length': len(db_answer),
        'difficulty': request.session.get('game_difficulty'),
        'is_premium_mode': is_premium_mode
    })
    
def game_answer(request, pk):
    if request.method != 'POST':
        return redirect('game_play')

    player, is_guest = get_current_player_info(request)
    is_premium_mode = request.session.get('is_premium_mode', False)

    if is_premium_mode:
        question = get_object_or_404(ThankJapanPremium, id=pk)
    else:
        question = get_object_or_404(ThankJapanModel, id=pk)

    form = AnswerForm(request.POST)
    current_difficulty = request.session.get('game_difficulty', 'normal')

    original_name = question.name.lower()
    romaji_for_hint = extract_base_name(original_name)
    hint_length = len(romaji_for_hint)

    if form.is_valid():
        user_input = form.cleaned_data['answer'].strip().lower()
        db_answer = extract_base_name(question.name).lower()

        judge_user = normalize_for_judge(user_input)
        judge_db = normalize_for_judge(db_answer)

        is_correct = False
        
        if current_difficulty == 'super_hard':
            if user_input == question.name.lower():
                is_correct = True
        else:
            if judge_user == judge_db:
                is_correct = True

        history = request.session.get('game_history', [])
        history.append({
            'question_id': question.id,
            'is_correct': is_correct,
            'user_input': user_input,
            'correct_answer': question.name,
        })
        request.session['game_history'] = history

        if is_correct:
            request.session['game_score'] = request.session.get('game_score', 0) + 1

        index = request.session.get('game_current_index', 0)
        ids = request.session.get('game_question_ids', [])
        is_last_question = (index + 1) >= len(ids)

        english_text = question.englishname.strip().lower() if question.englishname else ""
        romaji_text = db_answer
        show_english = True

        if english_text == romaji_text:
            show_english = False
        elif english_text.replace('k', 'c') == romaji_text.replace('k', 'c'):
            show_english = False
        elif (english_text in romaji_text or romaji_text in english_text) and abs(len(english_text) - len(romaji_text)) <= 1:
            show_english = False

        return render(request, 'thank_japan_app/game_play.html', {
            'object': question,
            'form': form,
            'user_input': user_input,
            'is_correct': is_correct,
            'show_result': True,
            'is_last_question': is_last_question,
            'current_index': index + 1,
            'total_questions': len(ids),
            'score': request.session.get('game_score', 0),
            'player': player,
            'is_guest': is_guest,
            'hint_length': hint_length,
            'difficulty': current_difficulty,
            'show_english': show_english,
            'is_premium_mode': is_premium_mode,
        })

    else:
        messages.warning(request, "⚠️ Please enter a valid answer.")
        return redirect('game_play')
    
        
def game_next_question(request):
    request.session['game_current_index'] = request.session.get('game_current_index', 0) + 1
    if request.session['game_current_index'] >= len(request.session.get('game_question_ids', [])):
        return redirect('game_result')
    return redirect('game_play')    


def game_restart(request):
    difficulty = request.GET.get('difficulty', 'normal')
    mode = request.GET.get('mode')
    player, is_guest = get_current_player_info(request)

    if mode == 'single':
        model_type = request.GET.get('model_type')
        val = request.GET.get('slug') 
        
        if model_type == 'premium':
            question = get_object_or_404(ThankJapanPremium, slug=val)
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
        premium_only = ['n4_premium', 'n3_premium']
        if difficulty in premium_only:
            if is_guest or not getattr(request.user.profile, 'is_premium', False):
                return redirect('premium_info')

        settings = DIFFICULTY_SETTINGS.get(difficulty, DIFFICULTY_SETTINGS['normal'])
        is_premium_mode = settings['model_type'] == 'premium'
        model = ThankJapanPremium if is_premium_mode else ThankJapanModel
        qs = model.objects.all()
        if settings.get('category_filter'): qs = qs.filter(category__in=settings['category_filter'])
        if settings.get('jlpt_level'): qs = qs.filter(jlpt_level=settings['jlpt_level'])
        if settings.get('length_regex'): qs = qs.filter(name__iregex=settings['length_regex'])
        
        ids = list(qs.values_list('id', flat=True))
        random.shuffle(ids)
        selected_question_ids = ids[:settings['num_questions']]

    keys_to_clear = ['game_question_ids', 'game_current_index', 'game_score', 'game_difficulty', 'game_history', 'score_saved', 'is_premium_mode']
    for key in keys_to_clear: request.session.pop(key, None)

    request.session['game_question_ids'] = selected_question_ids
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0
    request.session['game_difficulty'] = difficulty
    request.session['is_premium_mode'] = is_premium_mode
    request.session['game_history'] = []
    
    return redirect('game_play')

def game_result(request):
    score = request.session.get('game_score', 0)
    player, is_guest = get_current_player_info(request)
    is_premium_mode = request.session.get('is_premium_mode', False)
    difficulty = request.session.get('game_difficulty')

    if not request.session.get('score_saved', False) and not is_guest:
        player.total_score += score
        player.last_score = score
        player.save()
        request.session['score_saved'] = True

    history = request.session.get('game_history', [])
    model = ThankJapanPremium if is_premium_mode else ThankJapanModel
    played_questions = model.objects.in_bulk([h['question_id'] for h in history])

    review_data = []
    for h in history:
        q = played_questions.get(h['question_id'])
        if q: review_data.append({'object': q, 'is_correct': h['is_correct'], 'user_input': h['user_input'], 'correct_answer': h['correct_answer']})

    return render(request, 'thank_japan_app/game_result.html', {
        'player': player, 'score': score, 'is_guest': is_guest, 
        'review_data': review_data, 'difficulty': difficulty,
        'is_premium_mode': is_premium_mode,
        'ranking': Player.objects.order_by('-total_score')[:20]
    })

                
#category select view

def category_list(request):
    return render(request, 'thank_japan_app/category/category_list.html') 

def category_list_zhhant(request):
    return render(request, 'thank_japan_app/category/category_list_zh_hant.html') 

def category_list_vi(request):
    return render(request, 'thank_japan_app/category/category_list_vi.html') 

def category_list_th(request):
    return render(request, 'thank_japan_app/category/category_list_th.html')

def category_list_pt(request):
    return render(request, 'thank_japan_app/category/category_list_pt.html') 

def category_list_pt_br(request):
    return render(request, 'thank_japan_app/category/category_list_pt_br.html') 

def category_list_ko(request):
    return render(request, 'thank_japan_app/category/category_list_ko.html') 

def category_list_ja(request):
    return render(request, 'thank_japan_app/category/category_list_ja.html')
 
def category_list_it(request):
    return render(request, 'thank_japan_app/category/category_list_it.html')
 
def category_list_fr(request):
    return render(request, 'thank_japan_app/category/category_list_fr.html')

def category_list_es_mx(request):
    return render(request, 'thank_japan_app/category/category_list_es_mx.html')

def category_list_es_es(request):
    return render(request, 'thank_japan_app/category/category_list_es_es.html')
 
def category_list_en_in(request):
    return render(request, 'thank_japan_app/category/category_list_en_in.html')
 
def category_list_de(request):
    return render(request, 'thank_japan_app/category/category_list_de.html')
 
 
#category view
                            
class FoodView(ListView):
    template_name = "thank_japan_app/food.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="food").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Food Guide | Popular Dishes, Street Food & Snacks | ThankJapan"
        context['seo_description'] = "Discover iconic Japanese foods like sushi, ramen, and tempura. Learn about their ingredients and cultural roots."
        context['seo_og_title'] = "Japanese Food - Explore Traditional Dishes | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class NatureView(ListView):
    template_name = "thank_japan_app/nature.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="nature").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Explore Japanese Nature | Mountains, Forests & Scenic Views | ThankJapan"
        context['seo_description'] = "Discover the beauty of Japanese nature including mountains, forests, gardens, and scenic landscapes."
        context['seo_og_title'] = "Japanese Nature - Scenic Spots & Natural Wonders | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class FashionView(ListView):
    template_name = "thank_japan_app/fashion.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="fashion").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Fashion | Traditional & Modern Styles | ThankJapan"
        context['seo_description'] = "Explore Japanese fashion, from traditional kimono to modern streetwear and pop culture trends."
        context['seo_og_title'] = "Japanese Fashion - Kimono, Streetwear & Trends | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class CultureView(ListView):
    template_name = "thank_japan_app/culture.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="culture").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Culture | Traditions, Festivals & Customs | ThankJapan"
        context['seo_description'] = "Learn about Japanese culture, including festivals, traditional arts, customs, and heritage."
        context['seo_og_title'] = "Japanese Culture - Festivals, Arts & Traditions | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
class CookView(ListView):
    template_name = "thank_japan_app/cook.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="cook").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Cooking | Recipes & Culinary Techniques | ThankJapan"
        context['seo_description'] = "Discover Japanese cooking techniques and recipes from traditional dishes to modern cuisine."
        context['seo_og_title'] = "Japanese Cooking - Recipes & Techniques | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
class AppliancesView(ListView):
    template_name = "thank_japan_app/appliances.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="appliances").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Appliances | Modern & Traditional Devices | ThankJapan"
        context['seo_description'] = "Explore Japanese home appliances, both modern and traditional, and learn how they simplify daily life."
        context['seo_og_title'] = "Japanese Appliances - Innovative Devices & Tools | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class AnimalView(ListView):
    template_name = "thank_japan_app/animal.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="animal").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Animals | Wildlife & Pets | ThankJapan"
        context['seo_description'] = "Learn about animals in Japan, from native wildlife to popular pets and cultural symbolism."
        context['seo_og_title'] = "Japanese Animals - Wildlife & Pets | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class BuildingView(ListView):
    template_name = "thank_japan_app/building.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="building").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Buildings | Architecture & Landmarks | ThankJapan"
        context['seo_description'] = "Explore Japanese architecture, from historic temples and shrines to modern urban buildings."
        context['seo_og_title'] = "Japanese Buildings - Traditional & Modern Architecture | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class FlowerView(ListView):
    template_name = "thank_japan_app/flower.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="flower").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Flowers | Traditional Gardens & Seasonal Blooms | ThankJapan"
        context['seo_description'] = "Discover Japanese flowers and gardens, seasonal blooms, and their cultural significance."
        context['seo_og_title'] = "Japanese Flowers - Gardens & Seasonal Blooms | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class HouseholdItemsView(ListView):
    template_name = "thank_japan_app/householditems.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="householditems").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Household Items | Traditional & Modern Goods | ThankJapan"
        context['seo_description'] = "Explore Japanese household items, including traditional tools and modern gadgets used in everyday life."
        context['seo_og_title'] = "Japanese Household Items - Traditional & Modern Goods | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context

class SportsView(ListView):
    template_name = "thank_japan_app/sports.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="sports").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Sports | Traditional & Modern Games | ThankJapan"
        context['seo_description'] = "Learn about sports in Japan, from traditional martial arts to modern popular games."
        context['seo_og_title'] = "Japanese Sports - Martial Arts & Modern Games | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
class WorkView(ListView):
    template_name = "thank_japan_app/work.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="work").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Japanese Work Culture | Jobs, Professions & Traditions | ThankJapan"
        context['seo_description'] = "Explore Japanese work culture, professions, and workplace traditions throughout history and today."
        context['seo_og_title'] = "Japanese Work Culture - Jobs & Traditions | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
class LiveView(ListView):
    template_name = "thank_japan_app/live.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="live").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Living in Japan | Lifestyle, Housing & Daily Life | ThankJapan"
        context['seo_description'] = "Learn about daily life in Japan, housing, and lifestyle, from traditional to modern practices."
        context['seo_og_title'] = "Living in Japan - Lifestyle & Daily Life | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
#japan food
class JapanFoodView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage.html"
    
class JapanFoodZHHANTView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_zh_hant.html"
    
class JapanFoodVIView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_vi.html"
    
class JapanFoodTHView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_th.html"
    
class JapanFoodPTView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_pt.html"
    
class JapanFoodPTBRView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_pt_br.html"
    
class JapanFoodKOView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_ko.html"
    
class JapanFoodJAView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_ja.html"
    
class JapanFoodITView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_it.html"
    
class JapanFoodFRView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_fr.html"
    
class JapanFoodESMXView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_es_mx.html"
    
class JapanFoodESESView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_es_es.html"
    
class JapanFoodENINView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_en_in.html"
    
class JapanFoodDEView(TemplateView):
    template_name="thank_japan_app/japan/japanfoodpage_de.html"
    
    
#japan culture    
class JapanCultureView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage.html"
    
class JapanCultureZHHANTView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_zh_hant.html"
    
class JapanCultureVIView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_vi.html"
    
class JapanCultureTHView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_th.html"
    
class JapanCulturePTView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_pt.html"
    
class JapanCulturePTBRView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_pt_br.html"
    
class JapanCultureKOView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_ko.html"
    
class JapanCultureJAView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_ja.html"
    
class JapanCultureITView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_it.html"
    
class JapanCultureFRView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_fr.html"
    
class JapanCultureESMXView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_es_mx.html"
    
class JapanCultureESESView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_es_es.html"
    
class JapanCultureENINView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_en_in.html"
    
class JapanCultureDEView(TemplateView):
    template_name="thank_japan_app/japan/japanculturepage_de.html"
    

#Thank_Japan premium 

@login_required
@require_POST
def update_premium_status(request):
    try:
        data = json.loads(request.body)
        subscription_id = data.get('subscriptionID')

        if subscription_id:
            subscription = paypalrestsdk.Subscription.find(subscription_id)
            
            if subscription.status == "ACTIVE":
                profile = request.user.profile
                profile.is_premium = True
                profile.paypal_subscription_id = subscription_id
                profile.save()
                return JsonResponse({'status': 'success'})
            else:
                return JsonResponse({'status': 'error', 'message': 'Subscription is not active'}, status=400)
        else:
            return JsonResponse({'status': 'error', 'message': 'No ID provided'}, status=400)

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
#webhook

@csrf_exempt
def paypal_webhook(request):
    
    transmission_id = request.headers.get('PAYPAL-TRANSMISSION-ID')
    timestamp = request.headers.get('PAYPAL-TRANSMISSION-TIME')
    actual_signature = request.headers.get('PAYPAL-TRANSMISSION-SIG')
    cert_url = request.headers.get('PAYPAL-CERT-URL')
    auth_algo = request.headers.get('PAYPAL-AUTH-ALGO')
    webhook_id = settings.PAYPAL_WEBHOOK_ID  
    
    
    try:
        data = json.loads(request.body)
        
        
        is_valid = paypalrestsdk.WebhookEvent.verify(
            transmission_id, timestamp, webhook_id, request.body.decode('utf-8'),
            cert_url, actual_signature, auth_algo
        )

        if is_valid:
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
                try:
                    profile = Profile.objects.get(paypal_subscription_id=subscription_id)
                    profile.is_premium = False
                    profile.save()
                except Profile.DoesNotExist:
                    pass
                    
            return HttpResponse(status=200)
        else:
            return HttpResponse(status=400) 

    except Exception:
        return HttpResponse(status=500)
    
        
#premium_info

def premium_info(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info.html', context)

def premium_infoZHHANT(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_zh_hant.html', context)

def premium_infoVI(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_vi.html', context)

def premium_infoTH(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_th.html', context)

def premium_infoPT(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_pt.html', context)

def premium_infoPTBR(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_pt_br.html', context)

def premium_infoKO(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_ko.html', context)

def premium_infoJA(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_ja.html', context)

def premium_infoIT(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_it.html', context)

def premium_infoFR(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_fr.html', context)

def premium_infoESMX(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_es_mx.html', context)

def premium_infoESES(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_es_es.html', context)

def premium_infoENIN(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_en_in.html', context)

def premium_infoDE(request):
    context = {
        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
        'paypal_plan_id': settings.PAYPAL_PLAN_ID,
    }
    return render(request, 'thank_japan_app/premium/premium_info_de.html', context)


#thankyou
@login_required
def thank_you(request):
    return render(request, 'thank_japan_app/thankyou/thank_you.html')

@login_required
def thank_youZHHANT(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_zh_hant.html')

@login_required
def thank_youVI(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_vi.html')

@login_required
def thank_youTH(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_th.html')

@login_required
def thank_youPT(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_pt.html')

@login_required
def thank_youPTBR(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_pt_br.html')

@login_required
def thank_youKO(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_ko.html')

@login_required
def thank_youJA(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_ja.html')

@login_required
def thank_youIT(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_it.html')

@login_required
def thank_youFR(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_fr.html')

@login_required
def thank_youESMX(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_es_mx.html')

@login_required
def thank_youESES(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_es_es.html')

@login_required
def thank_youENIN(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_en_in.html')

@login_required
def thank_youDE(request):
    return render(request, 'thank_japan_app/thankyou/thank_you_de.html')


#account_settings
@login_required
def account_settings(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings.html', context)

@login_required
def account_settingsZHHANT(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_zh_hant.html', context)

@login_required
def account_settingsVI(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_vi.html', context)

@login_required
def account_settingsTH(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_th.html', context)

@login_required
def account_settingsPT(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_pt.html', context)

@login_required
def account_settingsPTBR(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_pt_br.html', context)

@login_required
def account_settingsKO(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_ko.html', context)

@login_required
def account_settingsJA(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_ja.html', context)

@login_required
def account_settingsIT(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_it.html', context)

@login_required
def account_settingsFR(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_fr.html', context)

@login_required
def account_settingsESMX(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_es_mx.html', context)

@login_required
def account_settingsESES(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_es_es.html', context)

@login_required
def account_settingsENIN(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_en_in.html', context)

@login_required
def account_settingsDE(request):
    profile = request.user.profile
    context = {
        'total_score': profile.total_score,
    }
    return render(request, 'thank_japan_app/account/account_settings_de.html', context)


@login_required
@require_POST
def downgrade_premium(request):
    profile = request.user.profile
    profile.is_premium = False
    profile.save()

    next_success_url = request.POST.get('downgrade_url_name', 'downgrade_success')
    return redirect(next_success_url)


@login_required
@require_POST
def delete_account(request):
    username_to_delete = request.user.username
    
    next_success_url = request.POST.get('success_url_name', 'delete_success')

    user_to_delete = request.user
    Player.objects.filter(username=username_to_delete).delete()
    user_to_delete.delete()

    return redirect(next_success_url)


def downgrade_success(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success.html')

def downgrade_successZHHANT(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_zh_hant.html')

def downgrade_successVI(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_vi.html')

def downgrade_successTH(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_th.html')

def downgrade_successPT(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_pt.html')

def downgrade_successPTBR(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_pt_br.html')

def downgrade_successKO(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_ko.html')

def downgrade_successJA(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_ja.html')

def downgrade_successIT(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_it.html')

def downgrade_successFR(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_fr.html')

def downgrade_successESMX(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_es_mx.html')

def downgrade_successESES(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_es_es.html')

def downgrade_successENIN(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_en_in.html')

def downgrade_successDE(request):
    return render(request, 'thank_japan_app/downgrade/downgrade_success_de.html')


#delete_success

def delete_success(request):
    return render(request, 'thank_japan_app/delete/delete_success.html')

def delete_successZHHANT(request):
    return render(request, 'thank_japan_app/delete/delete_success_zh_hant.html')

def delete_successVI(request):
    return render(request, 'thank_japan_app/delete/delete_success_vi.html')

def delete_successTH(request):
    return render(request, 'thank_japan_app/delete/delete_success_th.html')

def delete_successPT(request):
    return render(request, 'thank_japan_app/delete/delete_success_pt.html')

def delete_successPTBR(request):
    return render(request, 'thank_japan_app/delete/delete_success_pt_br.html')

def delete_successKO(request):
    return render(request, 'thank_japan_app/delete/delete_success_ko.html')

def delete_successJA(request):
    return render(request, 'thank_japan_app/delete/delete_success_ja.html')

def delete_successIT(request):
    return render(request, 'thank_japan_app/delete/delete_success_it.html')

def delete_successFR(request):
    return render(request, 'thank_japan_app/delete/delete_success_fr.html')

def delete_successESMX(request):
    return render(request, 'thank_japan_app/delete/delete_success_es_mx.html')

def delete_successESES(request):
    return render(request, 'thank_japan_app/delete/delete_success_es_es.html')

def delete_successENIN(request):
    return render(request, 'thank_japan_app/delete/delete_success_en_in.html')

def delete_successDE(request):
    return render(request, 'thank_japan_app/delete/delete_success_de.html')



class DailyConversationView(ListView):
    template_name = "thank_japan_app/dairy_conversation.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="DailyConversation").order_by('-timestamp')

class BusinessJapaneseView(ListView):
    template_name = "thank_japan_app/business_japanese.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="BusinessJapanese").order_by('-timestamp')

class LivingInJapanView(ListView):
    template_name = "thank_japan_app/living_in_japan.html"
    paginate_by = 24
    
    def get_queryset(self):
        return ThankJapanPremium.objects.filter(category="LivingInJapan").order_by('-timestamp')


class ImgPremiumDetailView(DetailView):
    template_name = "thank_japan_app/thankjapanmodel_detail_premium.html"
    model = ThankJapanPremium
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    
    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self.object.category != "DailyConversation":
            if not request.user.is_authenticated or not request.user.profile.is_premium:
                return redirect('premium_info')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_item = self.object
        context['related_items'] = ThankJapanPremium.objects.filter(
            category=current_item.category
        ).exclude(
            id=current_item.id
        ).order_by('?')[:6]

        url_name = CATEGORY_URL_MAP.get(current_item.category, 'category_list')
        context['category_list_url'] = reverse(url_name)
        
        return context
    
    
def sitemap_view(request):
    
    premium_items = ThankJapanPremium.objects.all()
    
    free_items = ThankJapanModel.objects.all()

    context = {
        'premium_items': premium_items,
        'free_items': free_items,
    }
    
    return render(request, 'sitemap.xml', context, content_type='application/xml')
    
