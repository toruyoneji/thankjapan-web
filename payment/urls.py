from django.urls import path
from  payment.views import create_checkout_session, success, cancel, checkout

urlpatterns = [
    path('create-checkout-session/', create_checkout_session, name='create_checkout_session'),
    path('success/', success, name='success'),
    path('cancel/', cancel, name='cancel'),
    path('checkout/', checkout, name='checkout'),
]
