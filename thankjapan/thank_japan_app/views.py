from django.shortcuts import render
from django.views import View
from django.views.generic import ListView
from .models import ThankJapanModel

class TopView(ListView):
    template_name = "thank_japan_app/toppage.html"
    model = ThankJapanModel

class FoodView(ListView):
    template_name = "thank_japan_app/food.html"
    model = ThankJapanModel

class NatureView(ListView):
    template_name = "thank_japan_app/Nature.html"
    model = ThankJapanModel

class FashionView(ListView):
    template_name = "thank_japan_app/fashion.html"
    model = ThankJapanModel    

class CultureView(ListView):
    template_name = "thank_japan_app/culture.html"
    model = ThankJapanModel

class CookView(ListView):
    template_name = "thank_japan_app/cook.html"
    model = ThankJapanModel

class AppliancesView(ListView):
    template_name = "thank_japan_app/appliances.html"
    model = ThankJapanModel

class AnimalView(ListView):
    template_name = "thank_japan_app/animal.html"
    model = ThankJapanModel