from django.shortcuts import render, redirect, get_object_or_404, HttpResponse, HttpResponseRedirect
from django.views import View
from django.views.generic import ListView, DetailView, FormView, TemplateView
from django.views.generic.edit import FormView
from .models import ThankJapanModel, Player
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from .forms import AnswerForm, ContactForm, UsernameForm
from django.views.decorators.http import require_POST
from django.http import Http404
from django.urls import reverse
from django.utils.safestring import mark_safe
import logging
import random
import re

logger = logging.getLogger(__name__)

def robots_txt(request):
    content = """User-agent: *
Disallow: /payment/create/
Disallow: /payment/success/
Disallow: /payment/cancel/
Disallow: /payment/webhook/
Disallow: /game/play/
Disallow: /game/result/
Disallow: /game/start/
Disallow: /login/
Disallow: /register/

Sitemap: https://www.thankjapan.com/sitemap.xml
"""
    return HttpResponse(content, content_type="text/plain")

import re

def strip_parentheses(text):
    return re.sub(r'\(.*?\)', '', text).strip()

def extract_base_name(text):
    return strip_parentheses(text)

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
    if not text:
        return ""
    text = text.lower().strip()
    text = normalize_consonants(text)
    return text

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

#Game

def player_register(request):
    if request.method == "POST":
        form = UsernameForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            raw_password = form.cleaned_data['password']
            country = form.cleaned_data['country']

            if Player.objects.filter(username=username).exists():
                messages.error(request, "This username is already taken.")
                return redirect('player_register')

            player = Player(username=username, country=country)
            player.set_password(raw_password)
            player.save()
            
            messages.success(request, "Account created! Please log in to start playing and save your scores.")
            
            for key in ['is_guest', 'game_score', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty', 'player_id']:
                request.session.pop(key, None)
            
            return redirect('player_login') 
    else:
        form = UsernameForm()

    return render(request, 'thank_japan_app/player_register.html', {'form': form})

def player_login(request):
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')

        try:
            player = Player.objects.get(username=username)
            if player.check_password(password):
                for key in ['is_guest', 'game_score', 'game_question_ids', 'game_current_index', 'game_message', 'last_question_info', 'game_difficulty']:
                    request.session.pop(key, None)
                
                request.session['player_id'] = player.id
                request.session['is_guest'] = False 

                messages.success(request, "Logged in successfully! Your scores from now on will be saved.")
                return redirect('game_start')
            else:
                messages.error(request, "Incorrect password.")
        except Player.DoesNotExist:
            messages.error(request, "Username not found.")
    return render(request, 'thank_japan_app/player_login.html')


def player_logout(request):
    for key in ['player_id', 'game_question_ids', 'game_current_index', 'game_score', 'game_message', 'last_question_info', 'game_difficulty']:
        request.session.pop(key, None)
    
    request.session['is_guest'] = True 

    messages.info(request, "You have been logged out.")
    return redirect('player_login')

def delete_player_confirm(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('player_login')

    return render(request, 'thank_japan_app/delete_player.html')


@require_POST
def delete_player(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('player_login')

    password = request.POST.get('password')
    player = get_object_or_404(Player, id=player_id)

    if player.check_password(password):
        player.delete()
        request.session.flush()
        messages.success(request, "Your account has been deleted.")
    else:
        messages.error(request, "Incorrect password. Account not deleted.")

    return redirect('player_login')


DIFFICULTY_SETTINGS = {
    'easy': {
        'category_filter': ['food', 'cook', 'animal', 'sports'],
        'length_regex': r'^.{3,6}$',
        'num_questions': 3,
    },
    'normal': {
        'category_filter': [
            'food', 'cook', 'animal', 'sports', 
            'culture', 'nature', 'fashion', 'building'
        ],
        'length_regex': r'^.{3,8}$',
        'num_questions': 5,
    },
    'hard': {
        'category_filter': None,
        'length_regex': r'^.{2,9}$',
        'num_questions': 7,
    },
    'super_hard': {
        'category_filter': None, 
        'length_regex': None, 
        'num_questions': 10, 
    }
}


def get_current_player_info(request):
    player_id = request.session.get('player_id')
    player = None
    is_guest = True

    if player_id:
        try:
            player = Player.objects.get(id=player_id)
            is_guest = False
            request.session.pop('is_guest', None) 
        except Player.DoesNotExist:
            request.session.pop('player_id', None)
            is_guest = True
    else:
        if 'is_guest' not in request.session:
            request.session['is_guest'] = True
        is_guest = request.session['is_guest']
    
    if is_guest and not player:
        player = Player(username='guest', country='Guestland') 
    
    return player, is_guest


def game_start(request):
    player, is_guest = get_current_player_info(request)
    
    return render(request, 'thank_japan_app/game_start.html', {
        'player': player,
        'is_guest': is_guest
    })


def game_play(request):
    player, is_guest = get_current_player_info(request)

    index = request.session.get('game_current_index', 0)
    ids = request.session.get('game_question_ids', [])
    total_questions = len(ids)

    if not ids or index >= total_questions:
        messages.info(request, "Game session ended or not started. Please select a difficulty to begin.")
        return redirect('game_start')

    question_id = ids[index]
    question = get_object_or_404(ThankJapanModel, id=question_id)

    form = AnswerForm()
    
    current_difficulty = request.session.get('game_difficulty', 'normal')
    max_questions_for_difficulty = DIFFICULTY_SETTINGS.get(current_difficulty, {}).get('num_questions', total_questions)

    original_name = question.name.lower()
    romaji_for_judge = extract_base_name(original_name)
    
    english_text = question.englishname.strip().lower() if question.englishname else ""
    romaji_text = original_name.strip()
    
    show_english = True
    if english_text == romaji_text:
        show_english = False
    elif english_text.replace('k', 'c') == romaji_text.replace('k', 'c'):
        show_english = False
    elif (english_text in romaji_text or romaji_text in english_text) and abs(len(english_text) - len(romaji_text)) <= 1:
        show_english = False

    hint_length = len(romaji_for_judge)

    return render(request, 'thank_japan_app/game_play.html', {
        'object': question,
        'form': form,
        'current_index': index + 1,
        'total_questions': max_questions_for_difficulty,
        'score': request.session.get('game_score', 0),
        'player': player,
        'is_guest': is_guest,
        'hint_length': hint_length,
        'difficulty': current_difficulty,
        'show_english': show_english,
    })

def game_answer(request, pk):
    if request.method != 'POST':
        return redirect('game_play')

    player, is_guest = get_current_player_info(request)
    question = get_object_or_404(ThankJapanModel, id=pk)
    form = AnswerForm(request.POST)

    current_difficulty = request.session.get('game_difficulty', 'normal')

    original_name = question.name.lower()
    romaji_for_hint = extract_base_name(original_name)
    hint_length = len(romaji_for_hint)

    if form.is_valid():
        user_input = form.cleaned_data['answer'].strip().lower()
        db_answer = extract_base_name(question.name).lower()  # <- ここを変更

        judge_user = normalize_for_judge(user_input)
        judge_db = normalize_for_judge(db_answer)

        is_correct = False
        
        if current_difficulty == 'super_hard':
            if user_input == question.name.lower():  # super_hard は完全一致
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
        })

    else:
        messages.warning(request, "⚠️ Please enter a valid answer.")
        return redirect('game_play')
    
def game_next_question(request):
    
    current_index = request.session.get('game_current_index', 0)
    request.session['game_current_index'] = current_index + 1
    
    
    ids = request.session.get('game_question_ids', [])
    if request.session['game_current_index'] >= len(ids):
        return redirect('game_result')
    
    return redirect('game_play')    
    

def game_restart(request):
    difficulty = request.GET.get('difficulty', 'normal') 
    
    player, is_guest = get_current_player_info(request)
    
    if difficulty == 'super_hard':
        current_total_score = player.total_score if not is_guest else 0
        if current_total_score < 300:
            messages.error(request, "🔒 You need 300 Total Points to unlock MANIA mode! Keep playing!")
            return redirect('game_start')
    
    if difficulty not in DIFFICULTY_SETTINGS:
        messages.error(request, "Invalid difficulty selected. Defaulting to Normal.")
        difficulty = 'normal'

    settings = DIFFICULTY_SETTINGS[difficulty]
    
    questions_queryset = ThankJapanModel.objects.all()

    if settings['category_filter']:
        questions_queryset = questions_queryset.filter(category__in=settings['category_filter'])

    if settings['length_regex']:
        questions_queryset = questions_queryset.filter(name__iregex=settings['length_regex'])

    all_question_ids = list(questions_queryset.values_list('id', flat=True))

    if len(all_question_ids) < settings['num_questions']:
        messages.warning(request, f"Not enough questions for '{difficulty}' difficulty. Displaying all available questions.")

    random.shuffle(all_question_ids)

    selected_question_ids = all_question_ids[:settings['num_questions']]

    if not selected_question_ids:
        messages.error(request, "No questions available for the selected difficulty. Please try another or contact support.")
        return redirect('game_start')

    keys_to_clear = [
        'game_question_ids', 'game_current_index', 'game_score', 
        'game_difficulty', 'game_message', 'game_history', 'score_saved'
    ]
    for key in keys_to_clear:
        request.session.pop(key, None)

    request.session['game_question_ids'] = selected_question_ids
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0
    request.session['game_difficulty'] = difficulty
    request.session['game_history'] = [] 

    messages.info(request, f"🚀 Starting a new game on {difficulty.upper()} difficulty!")
    return redirect('game_play')


def game_result(request):
    current_game_score = request.session.get('game_score', 0)
    player, is_guest = get_current_player_info(request)

    if not request.session.get('score_saved', False):
        if not is_guest:
            player.total_score += current_game_score
            player.last_score = current_game_score
            player.save()
        
        request.session['score_saved'] = True

    game_history = request.session.get('game_history', [])
    
    played_question_ids = [item['question_id'] for item in game_history]
    played_questions = ThankJapanModel.objects.in_bulk(played_question_ids)

    review_data = []
    for item in game_history:
        q_obj = played_questions.get(item['question_id'])
        if q_obj:
            review_data.append({
                'object': q_obj,
                'question_id': item['question_id'],
                'is_correct': item['is_correct'],
                'user_input': item['user_input'],
                'correct_answer': item['correct_answer'],
            })

    ranking = Player.objects.order_by('-total_score')[:20]

    return render(request, 'thank_japan_app/game_result.html', {
        'player': player,
        'score': current_game_score,
        'ranking': ranking,
        'is_guest': is_guest,
        'review_data': review_data,
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
    
