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
from ratelimit.decorators import ratelimit
from django.http import Http404
import logging
import random

logger = logging.getLogger(__name__)

def robots_txt(request):
    content = """User-agent: *
Disallow: /
Allow: /$
"""
    return HttpResponse(content, content_type="text/plain")

class LegalNoticeView(TemplateView):
    template_name = "thank_japan_app/legal_notice.html"
    
class PrivacyPolicy(TemplateView):
    template_name = "thank_japan_app/privacy_policy.html"

class TopView(ListView):
    template_name = "thank_japan_app/toppage.html"
    model = ThankJapanModel

class ImgDetailView(DetailView):
    template_name = "thank_japan_app/thankjapanmodel_detail.html"
    model = ThankJapanModel
    
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

            # Gmail 送信用
            full_message = f"From: {name} <{email}>\nTitle: {title}\n\n{message}"

            send_mail(
                subject=f"[Support] {title}",
                message=full_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=['yonetoru0@gmail.com'],  # 送信先
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
from ratelimit.decorators import ratelimit

@ratelimit(key='ip', rate='5/m', block=True)
def game_start(request):
    if request.session.get('player_id'):
        return redirect('game_play')

    if request.method == 'POST':
        form = UsernameForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            country = form.cleaned_data['country']

            player, created = Player.objects.get_or_create(username=username)
            if not created:
                messages.error(request, "This username is already taken. Please choose another.")
                return render(request, 'thank_japan_app/game_start.html', {'form': form})

            player.country = country
            player.save()

            request.session['player_id'] = player.id
            request.session['game_score'] = 0
            request.session['game_current_index'] = 0

            question_ids = list(ThankJapanModel.objects.values_list('id', flat=True))
            random.shuffle(question_ids)
            request.session['game_question_ids'] = question_ids[:10]

            return redirect('game_play')
    else:
        form = UsernameForm()

    return render(request, 'thank_japan_app/game_start.html', {'form': form})

           
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

    return render(request, 'thank_japan_app/game_play.html', {
        'object': question,
        'form': form,
        'current_index': index + 1,
        'score': request.session.get('game_score', 0),
        'message': message,
        'player': player,
    })


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
    else:
        
        return render(request, 'thank_japan_app/game_play.html', {
            'object': question,
            'form': form,
            'message': "Please enter a valid answer."
        })


def game_result(request):

    player_id = request.session.get('player_id')
    score = request.session.get('game_score', 0)

    player = get_object_or_404(Player, id=player_id)
    player.total_score += score
    player.save()

    for key in ['game_question_ids', 'game_current_index', 'game_score']:
        request.session.pop(key, None)

    ranking = Player.objects.order_by('-total_score')[:20]

    return render(request, 'thank_japan_app/game_result.html', {
        'player': player,
        'score': score,
        'ranking': ranking
    })

def game_restart(request):
    
    keys_to_clear = ['game_question_ids', 'game_current_index', 'game_score']
    for key in keys_to_clear:
        request.session.pop(key, None)
    
    question_ids = list(ThankJapanModel.objects.values_list('id', flat=True))
    random.shuffle(question_ids)
    request.session['game_question_ids'] = question_ids[:10]
    request.session['game_current_index'] = 0
    request.session['game_score'] = 0

    return redirect('game_play')


def logout_player(request):
    
    for key in ['player_id', 'game_question_ids', 'game_current_index', 'game_score']:
        request.session.pop(key, None)
    return redirect('game_start')  


@require_POST
def delete_player(request):
    player_id = request.session.get('player_id')
    if not player_id:
        return redirect('game_start')  

    player = get_object_or_404(Player, id=player_id)
    player.delete()

    request.session.flush()

    messages.error(request, "Your account has been deleted.")
    return redirect('game_start')
            
                                
class FoodView(ListView):
    template_name = "thank_japan_app/food.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="food").order_by('-timestamp')

class NatureView(ListView):
    template_name = "thank_japan_app/nature.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="nature").order_by('-timestamp')

class FashionView(ListView):
    template_name = "thank_japan_app/fashion.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="fashion").order_by('-timestamp')

class CultureView(ListView):
    template_name = "thank_japan_app/culture.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="culture").order_by('-timestamp')
    
class CookView(ListView):
    template_name = "thank_japan_app/cook.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="cook").order_by('-timestamp')
    
class AppliancesView(ListView):
    template_name = "thank_japan_app/appliances.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="appliances").order_by('-timestamp')

class AnimalView(ListView):
    template_name = "thank_japan_app/animal.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="animal").order_by('-timestamp')

class BuildingView(ListView):
    template_name = "thank_japan_app/building.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="building").order_by('-timestamp')

class FlowerView(ListView):
    template_name = "thank_japan_app/flower.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="flower").order_by('-timestamp')

class HouseholdItemsView(ListView):
    template_name = "thank_japan_app/householditems.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="householditems").order_by('-timestamp')

class SportsView(ListView):
    template_name = "thank_japan_app/sports.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="sports").order_by('-timestamp')
    
class WorkView(ListView):
    template_name = "thank_japan_app/work.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="work").order_by('-timestamp')