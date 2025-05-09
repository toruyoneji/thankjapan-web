from django.shortcuts import render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView
from .models import ThankJapanModel
from django.contrib.auth.mixins import LoginRequiredMixin

class TopView(ListView):
    template_name = "thank_japan_app/toppage.html"
    model = ThankJapanModel

class ImgDetailView(DetailView):
    tamplate_name = "thank_japan_app/detail.html"
    model = ThankJapanModel
    

class FoodView(ListView):
    template_name = "thank_japan_app/food.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="food")

class NatureView(ListView):
    template_name = "thank_japan_app/Nature.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="nature")

class FashionView(ListView):
    template_name = "thank_japan_app/fashion.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="fashion")

class CultureView(ListView):
    template_name = "thank_japan_app/culture.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="culture")
    
class CookView(ListView):
    template_name = "thank_japan_app/cook.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="cook")
    
class AppliancesView(ListView):
    template_name = "thank_japan_app/appliances.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="appliances")

class AnimalView(ListView):
    template_name = "thank_japan_app/animal.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="animal")

class BuildingView(ListView):
    template_name = "thank_japan_app/building.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="building")

class FlowerView(ListView):
    template_name = "thank_japan_app/flower.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="flower")

class HouseholdItemsView(ListView):
    template_name = "thank_japan_app/householditems.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="householditems")

class SportsView(ListView):
    template_name = "thank_japan_app/sports.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="sports")
    
class WorkView(ListView):
    template_name = "thank_japan_app/work.html"
    
    def get_queryset(self):
        return ThankJapanModel.objects.filter(category="work")