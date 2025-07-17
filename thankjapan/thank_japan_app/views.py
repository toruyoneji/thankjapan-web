from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import ListView, DetailView, FormView
from django.views.generic.edit import FormView
from .models import ThankJapanModel
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth import logout
from django.contrib import messages
from .forms import DeleteUserForm, CompanyForm
import logging

logger = logging.getLogger(__name__)

class TopView(ListView):
    template_name = "thank_japan_app/toppage.html"
    model = ThankJapanModel

class ImgDetailView(DetailView):
    tamplate_name = "thank_japan_app/detail.html"
    model = ThankJapanModel
    
class KiyakuView(ListView):
    template_name = "thank_japan_app/riyoukiyaku.html"
    model = ThankJapanModel

class UserDeleteFormView(FormView):
    template_name = "thank_japan_app/userdelete.html"
    form_class = DeleteUserForm
    success_url = "thank_japan_app/toppage"

class UserDeleteView(LoginRequiredMixin, View):

    def post(self, request, *args, **kwargs):
        form = DeleteUserForm(request.POST)
        if form.is_valid():
            user = request.user
            logout(request)
            user.delete()
            return redirect(reverse_lazy("toppage"))
        
#company infomation
class CompanyFormView(FormView):
     template_name = 'thank_japan_app/company.html'
     form_class = CompanyForm
     success_url = reverse_lazy('infomationpage')
     
     def form_valid(self, form):
         form.send_email()
         messages.success(self.request, 'send success!!')
         logger.info('Contact sent by {}'.format(form.cleaned_data['username']))
         return super().form_valid(form)

# def form_company(request):
#     if request.method == 'POST':
#         form = CompanyForm(request.POST)
#         if form.is_valid():
#             username = form.cleaned_data['username']
#             email = form.cleaned_data['email']
#             title = form.cleaned_data['title']
#             message = form.cleaned_data['message']
#             print('username: ', username)
#             return render(request, 'thank_japan_app/company.html', {'form': CompanyForm()})
#     else:
#         form = CompanyForm()
#     return render(request, 'thank_japan_app/company.html', {'form':form})
                
                
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