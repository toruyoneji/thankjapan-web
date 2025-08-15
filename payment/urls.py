from django.urls import path
from payment.views import (create_payment_view, cancel_view, 
                           payment_success, select_amount_view, paypal_webhook)

urlpatterns = [
    path('', select_amount_view, name='select_amount'),
    path('create/', create_payment_view, name='create_payment'),
    path('cancel/', cancel_view, name='payment_cancel'),
    path('success/', payment_success, name='payment_success'),
    path('webhook/', paypal_webhook, name='paypal_webhook'),
    
]
