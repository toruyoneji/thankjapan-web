from django.urls import path
from django.conf.urls.i18n import i18n_patterns
from payment.views import (create_payment_view, cancel_view, 
                           payment_success, select_amount_view, select_amount_view_ja,
                           select_amount_view_vi,select_amount_view_fr,select_amount_view_it, 
                           select_amount_view_pt,select_amount_view_zhhant, select_amount_view_ko, 
                           select_amount_view_esES,select_amount_view_de, select_amount_view_th, 
                           select_amount_view_ptBR, select_amount_view_esMX, select_amount_view_enIN, paypal_webhook)

urlpatterns = [
    path('', select_amount_view, name='select_amount'),
    path('ja/', select_amount_view_ja, name='select_amount_ja'),
    path('vi/', select_amount_view_vi, name='select_amount_vi'),
    path('fr/', select_amount_view_fr, name='select_amount_fr'),
    path('it/', select_amount_view_it, name='select_amount_it'),
    path('pt/', select_amount_view_pt, name='select_amount_pt'),
    path('zh-hant/', select_amount_view_zhhant, name='select_amount_zhHant'),
    path('ko/', select_amount_view_ko, name='select_amount_ko'),
    path('es-es/', select_amount_view_esES, name='select_amount_esES'),
    path('de/', select_amount_view_de, name='select_amount_de'),
    path('th/', select_amount_view_th, name='select_amount_th'),
    path('pt-br/', select_amount_view_ptBR, name='select_amount_ptBR'),
    path('es-mx/', select_amount_view_esMX, name='select_amount_esMX'),
    path('en-in/', select_amount_view_enIN, name='select_amount_enIN'),
    
    
    path('create/', create_payment_view, name='create_payment'),
    path('cancel/', cancel_view, name='payment_cancel'),
    path('success/', payment_success, name='payment_success'),
    path('webhook/', paypal_webhook, name='paypal_webhook'),
    
]