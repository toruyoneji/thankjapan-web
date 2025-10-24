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
import logging
import random

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

class LegalNoticeView(TemplateView):
    template_name = "thank_japan_app/legal_notice.html"
    
class PrivacyPolicy(TemplateView):
    template_name = "thank_japan_app/privacy_policy.html"

class TopView(ListView):
    template_name = "thank_japan_app/toppage.html"
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

    
class KiyakuView(ListView):
    template_name = "thank_japan_app/riyoukiyaku.html"
    model = ThankJapanModel

        
#company infomation
class CompanyFormView(TemplateView):
     template_name = 'thank_japan_app/company.html'
     

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

# --- 認証関連 ---

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
            request.session['player_id'] = player.id
            return redirect('game_start')
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
                request.session['player_id'] = player.id
                return redirect('game_start')
            else:
                messages.error(request, "Incorrect password.")
        except Player.DoesNotExist:
            messages.error(request, "Username not found.")
    return render(request, 'thank_japan_app/player_login.html')


def player_logout(request):
    for key in ['player_id', 'game_question_ids', 'game_current_index', 'game_score']:
        request.session.pop(key, None)
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


def game_start(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('player_login')

    player = get_object_or_404(Player, id=player_id)
    return render(request, 'thank_japan_app/game_start.html', {'player': player})



def game_play(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('game_start')

    player = get_object_or_404(Player, id=player_id)

    index = request.session.get('game_current_index', 0)
    ids = request.session.get('game_question_ids', [])

    if index >= len(ids):
        return redirect('game_result')

    question_id = ids[index]
    question = get_object_or_404(ThankJapanModel, id=question_id)

    form = AnswerForm()
    message = request.session.pop('game_message', None)

    context = {
        'object': question,
        'form': form,
        'current_index': index + 1,
        'score': request.session.get('game_score', 0),
        'message': message,
        'player': player,
    }

    response = render(request, 'thank_japan_app/game_play.html', context)
    response['X-Robots-Tag'] = 'noindex, nofollow'  
    return response

def game_answer(request, pk):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('game_start')

    question = get_object_or_404(ThankJapanModel, id=pk)
    form = AnswerForm(request.POST)

    if form.is_valid():
        user_input = form.cleaned_data['answer'].strip().lower()
        correct_answer = question.name.strip().lower()

        if user_input == correct_answer:
            message = "Correct!"
            request.session['game_score'] += 1
        else:
            message = f"Incorrect! The correct answer was: {question.name}"

        request.session['game_message'] = message
        request.session['game_current_index'] += 1
        return redirect('game_play')

    return render(request, 'thank_japan_app/game_play.html', {
        'object': question,
        'form': form,
        'message': "Please enter a valid answer."
    })


def game_result(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('game_start')

    score = request.session.get('game_score', 0)
    player = get_object_or_404(Player, id=player_id)
    player.total_score += score
    player.last_score = score
    player.save()

    for key in ['game_question_ids', 'game_current_index', 'game_score']:
        request.session.pop(key, None)

    ranking = Player.objects.order_by('-total_score')[:10]

    context =  {
        'player': player,
        'score': score,
        'ranking': ranking,
    }

    response = render(request, 'thank_japan_app/game_result.html', context)
    response['X-Robots-Tag'] = 'noindex, nofollow'  
    return response

def game_restart(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('game_start')

    question_ids = list(ThankJapanModel.objects.values_list('id', flat=True))
    random.shuffle(question_ids)

    request.session['game_question_ids'] = question_ids[:10]
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0

    return redirect('game_play')
            
                                
class FoodView(ListView):
    template_name = "thank_japan_app/food.html"
    
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
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="live").order_by('-timestamp')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['seo_title'] = "Living in Japan | Lifestyle, Housing & Daily Life | ThankJapan"
        context['seo_description'] = "Learn about daily life in Japan, housing, and lifestyle, from traditional to modern practices."
        context['seo_og_title'] = "Living in Japan - Lifestyle & Daily Life | ThankJapan"
        context['seo_og_description'] = context['seo_description']
        return context
    
    
