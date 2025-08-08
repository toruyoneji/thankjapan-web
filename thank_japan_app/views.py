from django.shortcuts import render, redirect, get_object_or_404, HttpResponse
from django.views import View
from django.views.generic import ListView, DetailView, FormView, TemplateView
from django.views.generic.edit import FormView
from .models import ThankJapanModel
from django.contrib import messages
from .forms import AnswerForm
from django.http import Http404
import logging
import random

logger = logging.getLogger(__name__)

class TopView(ListView):
    template_name = "thank_japan_app/toppage.html"
    model = ThankJapanModel

class ImgDetailView(DetailView):
    template_name = "thank_japan_app/detail.html"
    model = ThankJapanModel
    
class KiyakuView(ListView):
    template_name = "thank_japan_app/riyoukiyaku.html"
    model = ThankJapanModel

        
#company infomation
class CompanyFormView(TemplateView):
     template_name = 'thank_japan_app/company.html'

#Game
class GameView(FormView):
    template_name = 'thank_japan_app/game.html'
    form_class = AnswerForm

    
    def get_context_data(self, **kwargs):
         objects = ThankJapanModel.objects.all()
         if not objects.exists():
             raise Http404("Sorry No data...")     
         obj = random.choice(objects)
         
         context = super().get_context_data(**kwargs)
         context['object'] = obj
         return context
     
def answer(request, pk):
    answer = get_object_or_404(ThankJapanModel, id=pk)
    if request.method == 'POST':
        form = AnswerForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data['answer'].strip().lower()
            corret_answer = answer.name.strip().lower()
            if data == corret_answer:
                message = "Correct!!"
            else:
                message = f'Incorrect!! Answer --> {answer.name}'
                
            return render(request, 'thank_japan_app/game.html',
                          {'message':message, 'object': answer })
        else:
            return render(request, 'thank_japan_app/game.html',
                          {'form':form, 'object': answer })
    
    form = AnswerForm()
    return render(request, 'thank_japan_app/game.html', {'form': form})
            
                
         
    
   
                
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