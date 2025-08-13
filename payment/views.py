import stripe
from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import logging

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

@csrf_exempt
def create_checkout_session(request):
    YOUR_DOMAIN = "https://www.thankjapan.com"  # 本番のURLを確認

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'ThankJapan support',
                    },
                    'unit_amount': 2000,  # 価格は最小単位（例: 2000 = 20 USD）
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=YOUR_DOMAIN + '/payment/success/',
            cancel_url=YOUR_DOMAIN + '/payment/cancel/',
        )

        return JsonResponse({
            'id': checkout_session.id
        })

    except stripe.error.StripeError as e:
        # Stripeのエラーを捕まえる
        logger.error(f"Stripe error: {str(e)}")
        return JsonResponse({'error': f"Stripe error: {str(e)}"}, status=500)
    
    except Exception as e:
        # 一般的なエラーを捕まえる
        logger.error(f"General error: {str(e)}")
        return JsonResponse({'error': f"General error: {str(e)}"}, status=500)

def checkout(request):
    return render(request, 'payment/checkout.html', {
        'stripe_publishable_key': settings.STRIPE_TEST_PUBLISHABLE_KEY
    })
    

def success(request):
    return render(request, 'payment/success.html')

def cancel(request):
    return render(request, 'payment/cancel.html')


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META['HTTP_STRIPE_SIGNATURE']
    endpoint_secret = settings.STRIPE_ENDPOINT_SECRET

    
    event = None
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError as e:
        return JsonResponse({'error': 'Invalid payload'}, status=400)
    except stripe.error.SignatureVerificationError as e:
        return JsonResponse({'error': 'Invalid signature'}, status=400)

    
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        print('PaymentIntent was successful!', payment_intent)
    

    elif event['type'] == 'payment_intent.failed':
        payment_intent = event['data']['object']
        print('PaymentIntent failed.', payment_intent)

    return JsonResponse({'status': 'success'}, status=200)

