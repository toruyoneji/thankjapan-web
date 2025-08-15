import paypalrestsdk
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.conf import settings  
from django.views.decorators.csrf import csrf_exempt
import json
import os
import logging
from django.core.mail import send_mail


logger = logging.getLogger(__name__)

paypalrestsdk.configure({
    "mode": settings.PAYPAL_MODE,  
    "client_id": settings.PAYPAL_CLIENT_ID,
    "client_secret": settings.PAYPAL_CLIENT_SECRET
})


def create_payment(amount="2.00", currency="USD"):
    payment = paypalrestsdk.Payment({
        "intent": "sale",
        "payer": {"payment_method": "paypal"},
        "redirect_urls": {
            "return_url": "https://www.thankjapan.com/payment/success",
            "cancel_url": "https://www.thankjapan.com/payment/cancel"
        },
        "transactions": [{
            "amount": {"total": amount, "currency": currency},
            "description": f"ThankJapan Support {amount} {currency}"
        }]
    })

    if payment.create():
        for link in payment.links:
            if link.method == "REDIRECT":
                return link.href
    else:
        logger.error(f"Payment creation failed: {payment.error}")
        return payment.error

def create_payment_view(request):
    amount = request.GET.get("amount", "2.00")
    redirect_url_or_error = create_payment(amount)
    
    if isinstance(redirect_url_or_error, dict):
        return HttpResponse(f"Payment creation failed: {redirect_url_or_error}")
    return redirect(redirect_url_or_error)


def success_view(request):
    return render(request, 'payment/success.html')

def cancel_view(request):
    return render(request, 'payment/cancel.html')

def select_amount_view(request):
    return render(request, "payment/kurikku_payment.html")


def payment_success(request):
    payment_id = request.GET.get("paymentId")
    payer_id = request.GET.get("PayerID")
    payment = paypalrestsdk.Payment.find(payment_id)

    if payment.execute({"payer_id": payer_id}):
        logger.info(f"Payment executed successfully: {payment_id}")
        return render(request, "payment/success.html", {"payment": payment})
    else:
        logger.error(f"Payment execution failed: {payment.error}")
        return render(request, "payment/failure.html", {"error": payment.error})


WEBHOOK_ID = os.environ.get("PAYPAL_WEBHOOK_ID")  

@csrf_exempt
def paypal_webhook(request):
    payload = request.body.decode('utf-8')
    headers = {k: v for k, v in request.headers.items()}

    
    if not paypalrestsdk.WebhookEvent.verify(payload, headers, WEBHOOK_ID):
        logger.warning("⚠️ Webhook signature verification failed")
        return HttpResponse(status=400)

    event = json.loads(payload)
    event_type = event.get('event_type')

    if event_type == 'PAYMENT.SALE.COMPLETED':
        sale = event['resource']
        payment_id = sale['id']
        payer_email = sale['payer']['payer_info']['email']
        amount = sale['amount']['total']
        currency = sale['amount']['currency']

        logger.info(f"✅ Payment received: {amount} {currency} from {payer_email}")

        
        try:
            send_mail(
                subject=f"New Payment Received: {amount} {currency}",
                message=f"A payment has been received.\n\nPayment ID: {payment_id}\nAmount: {amount} {currency}\nPayer Email: {payer_email}",
                from_email=os.environ.get("DEFAULT_FROM_EMAIL"),
                recipient_list=[os.environ.get("DEFAULT_FROM_EMAIL")],
                fail_silently=False,
            )
            logger.info("Payment notification email sent successfully")
        except Exception as e:
            logger.error(f"Failed to send payment notification email: {e}")

    return HttpResponse(status=200)
