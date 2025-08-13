import stripe
from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse

stripe.api_key = settings.STRIPE_TEST_SECRET_KEY

def create_checkout_session(request):
    YOUR_DOMAIN = "http://www.thankjapan.com" 

    checkout_session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[
            {
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'ThankJapan support',  
                    },
                    'unit_amount': 2000,
                },
                'quantity': 1,
            },
        ],
        mode='payment',
        success_url=YOUR_DOMAIN + '/payment/success/',
        cancel_url=YOUR_DOMAIN + '/payment/cancel/',
    )

    return JsonResponse({
        'id': checkout_session.id
    })

def checkout(request):
    return render(request, 'payment/checkout.html', {
        'stripe_publishable_key': settings.STRIPE_TEST_PUBLISHABLE_KEY
    })
    

def success(request):
    return render(request, 'success.html')

def cancel(request):
    return render(request, 'cancel.html')
