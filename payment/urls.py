from django.urls import path
from payment.views import (create_payment_view, cancel_view, 
                           payment_success, select_amount_view, select_amount_view_ja,
                           select_amount_view_vi,select_amount_view_fr,select_amount_view_it, 
                           select_amount_view_pt,select_amount_view_zhhant, paypal_webhook)

urlpatterns = [
    path('', select_amount_view, name='select_amount'),
    path('ja/', select_amount_view_ja, name='select_amount_ja'),
    path('vi/', select_amount_view_vi, name='select_amount_vi'),
    path('fr/', select_amount_view_fr, name='select_amount_fr'),
    path('it/', select_amount_view_it, name='select_amount_it'),
    path('pt/', select_amount_view_it, name='select_amount_it'),
    path('zh-hant/', select_amount_view_zhhant, name='select_amount_zhhant'),
    
    path('create/', create_payment_view, name='create_payment'),
    path('cancel/', cancel_view, name='payment_cancel'),
    path('success/', payment_success, name='payment_success'),
    path('webhook/', paypal_webhook, name='paypal_webhook'),
    
]
