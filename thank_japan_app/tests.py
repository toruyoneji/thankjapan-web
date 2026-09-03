import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import context_processors
from .models import Profile, ThankJapanModel, ThankJapanPremium
from .pricing import get_premium_price


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    PAYPAL_PLAN_ID='P-TESTPLAN',
    SECURE_SSL_REDIRECT=False,
)
class UpdatePremiumStatusSecurityTests(TestCase):
    """update_premium_status must verify the subscriptionID with PayPal
    itself before granting premium access, instead of trusting the client."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass12345')
        self.client.login(username='alice', password='pass12345')
        self.url = reverse('update_premium_status')

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_active_matching_subscription_grants_premium(self, mock_post, mock_get):
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(200, {'status': 'ACTIVE', 'plan_id': 'P-TESTPLAN'})

        response = self.client.post(
            self.url, data=json.dumps({'subscriptionID': 'I-REALSUB1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.is_premium)
        self.assertEqual(profile.paypal_subscription_id, 'I-REALSUB1')

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_unknown_subscription_is_rejected(self, mock_post, mock_get):
        # PayPal has never heard of this subscription id (e.g. a made-up value).
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(404, {})

        response = self.client.post(
            self.url, data=json.dumps({'subscriptionID': 'I-FAKE'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)
        self.assertIsNone(profile.paypal_subscription_id)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_inactive_subscription_is_rejected(self, mock_post, mock_get):
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(200, {'status': 'SUSPENDED', 'plan_id': 'P-TESTPLAN'})

        response = self.client.post(
            self.url, data=json.dumps({'subscriptionID': 'I-SUSPENDED'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_subscription_from_a_different_plan_is_rejected(self, mock_post, mock_get):
        # e.g. a subscription to a cheaper/unrelated PayPal plan on the same account.
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(200, {'status': 'ACTIVE', 'plan_id': 'P-CHEAPPLAN'})

        response = self.client.post(
            self.url, data=json.dumps({'subscriptionID': 'I-WRONGPLAN'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_paypal_lookup_failure_does_not_grant_premium(self, mock_post, mock_get):
        # get_paypal_access_token() itself fails (no access_token in response).
        mock_post.return_value = _mock_response(200, {})
        mock_get.return_value = _mock_response(200, {'status': 'ACTIVE', 'plan_id': 'P-TESTPLAN'})

        response = self.client.post(
            self.url, data=json.dumps({'subscriptionID': 'I-REALSUB1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    PAYPAL_WEBHOOK_ID='WH-TEST-ID',
    SECURE_SSL_REDIRECT=False,
)
class PaypalWebhookSecurityTests(TestCase):
    """paypal_webhook must verify PayPal's transmission signature before
    trusting the payload enough to downgrade a user's premium status."""

    def setUp(self):
        self.user = User.objects.create_user(username='bob', password='pass12345')
        self.user.profile.is_premium = True
        self.user.profile.paypal_subscription_id = 'I-REALSUB1'
        self.user.profile.save()
        self.url = reverse('paypal_webhook')
        self.event_body = {
            'event_type': 'BILLING.SUBSCRIPTION.CANCELLED',
            'resource': {'id': 'I-REALSUB1'},
        }
        self.paypal_headers = {
            'HTTP_PAYPAL_AUTH_ALGO': 'SHA256withRSA',
            'HTTP_PAYPAL_CERT_URL': 'https://api.paypal.com/cert',
            'HTTP_PAYPAL_TRANSMISSION_ID': 'txn-1',
            'HTTP_PAYPAL_TRANSMISSION_SIG': 'sig',
            'HTTP_PAYPAL_TRANSMISSION_TIME': '2026-08-29T00:00:00Z',
        }

    @staticmethod
    def _post_side_effect(verification_status):
        def side_effect(url, **kwargs):
            if 'oauth2/token' in url:
                return _mock_response(200, {'access_token': 'tok'})
            if 'verify-webhook-signature' in url:
                return _mock_response(200, {'verification_status': verification_status})
            raise AssertionError(f'unexpected POST to {url}')
        return side_effect

    @patch('thank_japan_app.views.requests.post')
    def test_valid_signature_processes_event(self, mock_post):
        mock_post.side_effect = self._post_side_effect('SUCCESS')

        response = self.client.post(
            self.url,
            data=json.dumps(self.event_body),
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_premium)

    @patch('thank_japan_app.views.requests.post')
    def test_invalid_signature_is_rejected_and_profile_untouched(self, mock_post):
        # An attacker (or a broken sender) whose signature PayPal itself rejects.
        mock_post.side_effect = self._post_side_effect('FAILURE')

        response = self.client.post(
            self.url,
            data=json.dumps(self.event_body),
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(response.status_code, 401)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_premium)

    @patch('thank_japan_app.views.requests.post')
    def test_missing_transmission_headers_is_rejected(self, mock_post):
        # No PAYPAL-TRANSMISSION-* headers at all, e.g. a forged direct POST.
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})

        response = self.client.post(
            self.url,
            data=json.dumps(self.event_body),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_premium)
        mock_post.assert_not_called()

    @override_settings(PAYPAL_WEBHOOK_ID='')
    def test_missing_webhook_id_config_is_rejected_without_calling_paypal(self):
        response = self.client.post(
            self.url,
            data=json.dumps(self.event_body),
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(response.status_code, 500)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_premium)

    def test_invalid_json_body_is_rejected(self):
        response = self.client.post(
            self.url,
            data='not-json',
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(response.status_code, 400)


class HasPremiumAccessBackwardCompatTests(TestCase):
    """has_premium_access must not instantly log out accounts that predate
    premium_expires_at (existing paying members get premium_expires_at=NULL
    the moment this migration lands)."""

    def test_existing_premium_member_with_null_expiry_still_has_access(self):
        user = User.objects.create_user(username='legacy', password='pass12345')
        user.profile.is_premium = True
        user.profile.premium_expires_at = None
        user.profile.save()

        self.assertTrue(user.profile.has_premium_access)

    def test_non_premium_user_has_no_access(self):
        user = User.objects.create_user(username='free', password='pass12345')
        self.assertFalse(user.profile.has_premium_access)

    def test_premium_with_future_expiry_has_access(self):
        user = User.objects.create_user(username='active', password='pass12345')
        user.profile.is_premium = True
        user.profile.premium_expires_at = timezone.now() + timedelta(days=3)
        user.profile.save()

        self.assertTrue(user.profile.has_premium_access)

    def test_premium_with_past_expiry_has_no_access(self):
        user = User.objects.create_user(username='lapsed', password='pass12345')
        user.profile.is_premium = True
        user.profile.premium_expires_at = timezone.now() - timedelta(days=1)
        user.profile.save()

        self.assertFalse(user.profile.has_premium_access)


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    PAYPAL_PLAN_ID='P-TESTPLAN',
    PAYPAL_WEBHOOK_ID='WH-TEST-ID',
    SECURE_SSL_REDIRECT=False,
)
class PaypalFreeTrialLifecycleTests(TestCase):
    """End-to-end: trial starts via update_premium_status, then PayPal's
    auto-billing at day 7 re-fires BILLING.SUBSCRIPTION.ACTIVATED, which must
    flip is_trial off and refresh premium_expires_at to the new billing date,
    while trial_used stays permanently set."""

    def setUp(self):
        self.user = User.objects.create_user(username='trial_user', password='pass12345')
        self.client.login(username='trial_user', password='pass12345')
        self.paypal_headers = {
            'HTTP_PAYPAL_AUTH_ALGO': 'SHA256withRSA',
            'HTTP_PAYPAL_CERT_URL': 'https://api.paypal.com/cert',
            'HTTP_PAYPAL_TRANSMISSION_ID': 'txn-2',
            'HTTP_PAYPAL_TRANSMISSION_SIG': 'sig',
            'HTTP_PAYPAL_TRANSMISSION_TIME': '2026-09-05T00:00:00Z',
        }

    @staticmethod
    def _subscription_payload(next_billing_time, trial_completed):
        return {
            'status': 'ACTIVE',
            'plan_id': 'P-TESTPLAN',
            'billing_info': {
                'next_billing_time': next_billing_time,
                'cycle_executions': [
                    {
                        'tenure_type': 'TRIAL', 'sequence': 1,
                        'cycles_completed': 1 if trial_completed else 0, 'total_cycles': 1,
                    },
                    {
                        'tenure_type': 'REGULAR', 'sequence': 2,
                        'cycles_completed': 1 if trial_completed else 0, 'total_cycles': 0,
                    },
                ],
            },
        }

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_trial_start_then_conversion_to_regular_updates_expiry(self, mock_post, mock_get):
        # --- Day 0: subscribe, currently in the TRIAL cycle ---
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        trial_end = timezone.now() + timedelta(days=7)
        mock_get.return_value = _mock_response(
            200, self._subscription_payload(trial_end.isoformat(), trial_completed=False)
        )

        response = self.client.post(
            reverse('update_premium_status'),
            data=json.dumps({'subscriptionID': 'I-TRIALSUB'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.is_premium)
        self.assertTrue(profile.is_trial)
        self.assertTrue(profile.trial_used)
        self.assertTrue(profile.has_premium_access)
        self.assertAlmostEqual(profile.premium_expires_at, trial_end, delta=timedelta(seconds=5))

        # --- Day 7: PayPal auto-bills the regular cycle and re-fires ACTIVATED ---
        def post_side_effect(url, **kwargs):
            if 'oauth2/token' in url:
                return _mock_response(200, {'access_token': 'tok'})
            if 'verify-webhook-signature' in url:
                return _mock_response(200, {'verification_status': 'SUCCESS'})
            raise AssertionError(f'unexpected POST to {url}')
        mock_post.side_effect = post_side_effect

        regular_next_billing = timezone.now() + timedelta(days=30)
        mock_get.return_value = _mock_response(
            200, self._subscription_payload(regular_next_billing.isoformat(), trial_completed=True)
        )

        webhook_response = self.client.post(
            reverse('paypal_webhook'),
            data=json.dumps({
                'event_type': 'BILLING.SUBSCRIPTION.ACTIVATED',
                'resource': {'id': 'I-TRIALSUB'},
            }),
            content_type='application/json',
            **self.paypal_headers,
        )
        self.assertEqual(webhook_response.status_code, 200)

        profile.refresh_from_db()
        self.assertTrue(profile.is_premium)
        self.assertFalse(profile.is_trial)
        self.assertTrue(profile.trial_used)  # never reset once consumed
        self.assertTrue(profile.has_premium_access)
        self.assertAlmostEqual(profile.premium_expires_at, regular_next_billing, delta=timedelta(seconds=5))


@override_settings(SECURE_SSL_REDIRECT=False)
class GooglePlayTrialReuseTests(TestCase):
    """A user who already consumed their free trial must not be able to get
    a second one through Google Play, even if Play Console's own "never
    purchased before" eligibility check is somehow bypassed."""

    def setUp(self):
        self.user = User.objects.create_user(username='replay_user', password='pass12345')
        self.client.login(username='replay_user', password='pass12345')
        self.url = reverse('verify_android_subscription')

    @staticmethod
    def _mock_google_play_service(payment_state, expiry_ms):
        service = MagicMock()
        service.purchases().subscriptions().get().execute.return_value = {
            'paymentState': payment_state,
            'acknowledgementState': 1,
            'expiryTimeMillis': str(expiry_ms),
        }
        return service

    @override_settings(
        GOOGLE_PLAY_KEY_DICT={'dummy': 'creds'},
        PACKAGE_NAME='com.thankjapan.www.twa',
        GOOGLE_PLAY_PRODUCT_ID='premium_monthly',
    )
    @patch('thank_japan_app.views.build')
    @patch('thank_japan_app.views.service_account.Credentials.from_service_account_info')
    def test_reusing_trial_is_rejected(self, mock_creds, mock_build):
        self.user.profile.trial_used = True
        self.user.profile.save()

        future_expiry_ms = int((timezone.now() + timedelta(days=7)).timestamp() * 1000)
        mock_build.return_value = self._mock_google_play_service(payment_state=2, expiry_ms=future_expiry_ms)

        response = self.client.post(
            self.url,
            data=json.dumps({'purchaseToken': 'replayed-trial-token'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)
        self.assertIsNone(profile.google_play_purchase_token)

    @override_settings(
        GOOGLE_PLAY_KEY_DICT={'dummy': 'creds'},
        PACKAGE_NAME='com.thankjapan.www.twa',
        GOOGLE_PLAY_PRODUCT_ID='premium_monthly',
    )
    @patch('thank_japan_app.views.build')
    @patch('thank_japan_app.views.service_account.Credentials.from_service_account_info')
    def test_first_time_trial_is_granted_and_marks_trial_used(self, mock_creds, mock_build):
        future_expiry_ms = int((timezone.now() + timedelta(days=7)).timestamp() * 1000)
        mock_build.return_value = self._mock_google_play_service(payment_state=2, expiry_ms=future_expiry_ms)

        response = self.client.post(
            self.url,
            data=json.dumps({'purchaseToken': 'first-trial-token'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.is_premium)
        self.assertTrue(profile.is_trial)
        self.assertTrue(profile.trial_used)
        self.assertEqual(profile.google_play_purchase_token, 'first-trial-token')

    @override_settings(
        GOOGLE_PLAY_KEY_DICT={'dummy': 'creds'},
        PACKAGE_NAME='com.thankjapan.www.twa',
        GOOGLE_PLAY_PRODUCT_ID='premium_monthly',
    )
    @patch('thank_japan_app.views.build')
    @patch('thank_japan_app.views.service_account.Credentials.from_service_account_info')
    def test_regular_paid_purchase_is_unaffected_by_trial_used(self, mock_creds, mock_build):
        # A returning customer paying full price (paymentState 1) must not be
        # blocked just because they used a trial previously.
        self.user.profile.trial_used = True
        self.user.profile.save()

        future_expiry_ms = int((timezone.now() + timedelta(days=30)).timestamp() * 1000)
        mock_build.return_value = self._mock_google_play_service(payment_state=1, expiry_ms=future_expiry_ms)

        response = self.client.post(
            self.url,
            data=json.dumps({'purchaseToken': 'regular-paid-token'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.is_premium)
        self.assertFalse(profile.is_trial)


class GetPremiumPriceLocalCurrencyTests(SimpleTestCase):
    """price_usd is always what's actually billed to PayPal and must stay
    USD regardless of country. price_display is display-only and switches
    to a native-currency string for countries in LOCAL_CURRENCY_DISPLAY -
    added alongside Cambodia/Myanmar joining COUNTRY_TIERS and the
    Nigeria/Myanmar/Egypt/Ghana rate check on 2026-08-30."""

    CLOUDFLARE_IP = '173.245.48.1'

    def _price_for(self, country_code=None):
        kwargs = {'REMOTE_ADDR': self.CLOUDFLARE_IP, 'HTTP_X_FORWARDED_FOR': self.CLOUDFLARE_IP}
        if country_code:
            kwargs['HTTP_CF_IPCOUNTRY'] = country_code
        request = RequestFactory().get('/premium/', **kwargs)
        return get_premium_price(request)

    def test_japan_shows_yen_but_bills_usd(self):
        result = self._price_for('JP')
        self.assertEqual(result['price_usd'], '5.00')
        self.assertEqual(result['price_display'], '¥800')
        self.assertIsNone(result['price_tier'])

    def test_cambodia_and_myanmar_are_tier_a_with_local_display(self):
        for country, expected_display in (('KH', '៛7,200'), ('MM', 'Ks3,700')):
            with self.subTest(country=country):
                result = self._price_for(country)
                self.assertEqual(result['price_usd'], '1.75')
                self.assertEqual(result['price_tier'], 'A')
                self.assertEqual(result['price_display'], expected_display)

    def test_tier_countries_without_a_currency_entry_still_bill_correctly(self):
        # Every LOCAL_CURRENCY_DISPLAY entry must actually match a real
        # country/tier pairing, but not every tiered country needs a
        # display entry - price_display just falls back to USD for those.
        result = self._price_for('MN')
        self.assertEqual(result['price_usd'], '1.75')
        self.assertEqual(result['price_tier'], 'A')

    def test_untiered_country_falls_back_to_usd_display(self):
        result = self._price_for('TH')
        self.assertEqual(result['price_usd'], '5.00')
        self.assertEqual(result['price_display'], '$5.00')

    def test_no_country_detected_falls_back_to_usd_display(self):
        request = RequestFactory().get('/premium/', REMOTE_ADDR='8.8.8.8')
        result = get_premium_price(request)
        self.assertEqual(result['price_usd'], '5.00')
        self.assertEqual(result['price_display'], '$5.00')
        self.assertIsNone(result['detected_country'])


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    PAYPAL_PLAN_ID='P-TESTPLAN',
    SECURE_SSL_REDIRECT=False,
)
class CreatePaypalSubscriptionTests(TestCase):
    """The JS SDK's client-side actions.subscription.create() rejects a
    'plan' override with 403 NOT_AUTHORIZED ("Billing Plan Override is not
    allowed due to insufficient permissions") - confirmed against the live
    REST API, where the exact same override succeeds (201) every time when
    sent with a server-side OAuth token instead. So the region-price
    override has to happen here, server-side, not in a button's
    createSubscription callback."""

    # A real Cloudflare edge IP, so pricing.get_premium_price() trusts the
    # CF-IPCountry header instead of falling back to the USD default.
    CLOUDFLARE_IP = '173.245.48.1'

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass12345')
        self.client.login(username='alice', password='pass12345')
        self.url = reverse('create_paypal_subscription')

    @patch('thank_japan_app.views.requests.post')
    def test_default_region_creates_subscription_at_base_price(self, mock_post):
        mock_post.side_effect = [
            _mock_response(200, {'access_token': 'tok'}),
            _mock_response(201, {
                'id': 'I-NEWSUB',
                'status': 'APPROVAL_PENDING',
                'links': [{'rel': 'approve', 'href': 'https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-X'}],
            }),
        ]

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['approve_url'], 'https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-X')

        create_call = mock_post.call_args_list[1]
        body = create_call.kwargs['json']
        self.assertEqual(body['plan_id'], 'P-TESTPLAN')
        cycle = body['plan']['billing_cycles'][0]
        self.assertEqual(cycle['sequence'], 1)
        self.assertEqual(cycle['pricing_scheme']['fixed_price']['value'], '5.00')
        self.assertNotIn('total_cycles', cycle)

        self.assertEqual(self.client.session['pending_paypal_subscription_id'], 'I-NEWSUB')

    @patch('thank_japan_app.views.requests.post')
    def test_discounted_region_overrides_to_tier_price(self, mock_post):
        mock_post.side_effect = [
            _mock_response(200, {'access_token': 'tok'}),
            _mock_response(201, {
                'id': 'I-NEWSUB2',
                'status': 'APPROVAL_PENDING',
                'links': [{'rel': 'approve', 'href': 'https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-Y'}],
            }),
        ]

        response = self.client.post(
            self.url,
            REMOTE_ADDR=self.CLOUDFLARE_IP,
            HTTP_X_FORWARDED_FOR=self.CLOUDFLARE_IP,
            HTTP_CF_IPCOUNTRY='ID',  # tier A -> $1.75
        )

        self.assertEqual(response.status_code, 200)
        create_call = mock_post.call_args_list[1]
        body = create_call.kwargs['json']
        self.assertEqual(body['plan']['billing_cycles'][0]['pricing_scheme']['fixed_price']['value'], '1.75')

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(self.url)
        self.assertNotEqual(response.status_code, 200)

    @patch('thank_japan_app.views.requests.post')
    def test_paypal_error_surfaces_as_502(self, mock_post):
        mock_post.side_effect = [
            _mock_response(200, {'access_token': 'tok'}),
            _mock_response(400, {'name': 'INVALID_REQUEST'}),
        ]

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['status'], 'error')


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    PAYPAL_PLAN_ID='P-TESTPLAN',
    SECURE_SSL_REDIRECT=False,
)
class PaypalSubscriptionReturnTests(TestCase):
    """The redirect back from PayPal must re-verify with PayPal using the
    subscription_id this server itself stashed in the session before
    sending the buyer to PayPal - never an ID taken from the redirect's own
    query string, mirroring update_premium_status's same never-trust-the-
    client verification."""

    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='pass12345')
        self.client.login(username='alice', password='pass12345')

    def _set_pending(self, subscription_id):
        session = self.client.session
        session['pending_paypal_subscription_id'] = subscription_id
        session.save()

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_active_subscription_activates_premium_and_redirects_to_thank_you(self, mock_post, mock_get):
        self._set_pending('I-RETURNED')
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(200, {'status': 'ACTIVE', 'plan_id': 'P-TESTPLAN'})

        response = self.client.get(reverse('paypal_subscription_return'))

        self.assertRedirects(response, reverse('thank_you'), fetch_redirect_response=False)
        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.is_premium)
        self.assertEqual(profile.paypal_subscription_id, 'I-RETURNED')
        self.assertNotIn('pending_paypal_subscription_id', self.client.session)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_redirects_to_localized_thank_you_page(self, mock_post, mock_get):
        self._set_pending('I-RETURNED-JA')
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(200, {'status': 'ACTIVE', 'plan_id': 'P-TESTPLAN'})

        response = self.client.get(reverse('paypal_subscription_return'), {'lang': 'ja'})

        self.assertRedirects(response, reverse('thank_youja'), fetch_redirect_response=False)

    def test_missing_pending_id_redirects_to_premium_with_error(self):
        response = self.client.get(reverse('paypal_subscription_return'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('paypal_error=1', response.url)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_inactive_subscription_does_not_activate_premium(self, mock_post, mock_get):
        self._set_pending('I-CANCELLED')
        mock_post.return_value = _mock_response(200, {'access_token': 'tok'})
        mock_get.return_value = _mock_response(200, {'status': 'CANCELLED', 'plan_id': 'P-TESTPLAN'})

        response = self.client.get(reverse('paypal_subscription_return'))

        self.assertIn('paypal_error=1', response.url)
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.is_premium)


class TrialEndedPopupStatusTests(TestCase):
    """trial_ended_popup_status must fire only when the trial itself expired
    naturally, never when the user converted the trial to a real
    subscription and later cancelled that (regression: see
    project_paypal_checkout_state memory 2026-09-02, reported by real
    trial -> PayPal payment -> downgrade flow wrongly showing "your trial
    has ended"), and it must fire the instant access actually ends rather
    than waiting for expire_premium_subscriptions' next daily run (regression
    #2: gating on the stored is_trial field instead of the computed
    has_premium_access property left up to a ~24h gap where a QA account with
    premium_expires_at manually moved into the past still didn't see the
    popup, because is_trial only flips on the batch's next run)."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username='trial_popup_user', password='pass12345')

    def _status(self):
        request = self.factory.get('/')
        request.user = self.user
        return context_processors.trial_ended_popup_status(request)['show_trial_ended_popup']

    def test_natural_trial_expiry_shows_popup(self):
        """The state expire_premium_subscriptions leaves a trial profile in
        once it *has* run: is_trial already flipped False."""
        profile = self.user.profile
        profile.trial_used = True
        profile.is_trial = False
        profile.is_premium = False
        profile.save()

        self.assertTrue(self._status())

    def test_expired_but_unbatched_trial_shows_popup_immediately(self):
        """The gap this fix closes: premium_expires_at has already passed,
        but is_trial/is_premium are still the stale pre-batch values (True) -
        has_premium_access is computed, so it already reflects reality and
        the popup must show without waiting for the next 18:00 UTC batch."""
        profile = self.user.profile
        profile.trial_used = True
        profile.is_trial = True
        profile.is_premium = True
        profile.premium_expires_at = timezone.now() - timedelta(minutes=1)
        profile.save()

        self.assertTrue(self._status())

    def test_trial_converted_to_paypal_then_downgraded_does_not_show_popup(self):
        profile = self.user.profile
        profile.trial_used = True
        profile.is_trial = False  # flipped False on conversion, before downgrade
        profile.is_premium = False  # then downgraded
        profile.paypal_subscription_id = 'I-REALSUB'  # never cleared by downgrade
        profile.save()

        self.assertFalse(self._status())

    def test_trial_converted_to_google_play_then_cancelled_does_not_show_popup(self):
        profile = self.user.profile
        profile.trial_used = True
        profile.is_trial = False
        profile.is_premium = False
        profile.google_play_purchase_token = 'token-abc'  # never cleared on cancellation
        profile.save()

        self.assertFalse(self._status())

    def test_still_in_trial_does_not_show_popup(self):
        profile = self.user.profile
        profile.trial_used = True
        profile.is_trial = True
        profile.is_premium = True
        profile.premium_expires_at = timezone.now() + timedelta(days=3)
        profile.save()

        self.assertFalse(self._status())

    def test_already_dismissed_does_not_show_popup_again(self):
        profile = self.user.profile
        profile.trial_used = True
        profile.is_trial = False
        profile.is_premium = False
        profile.trial_ended_popup_dismissed = True
        profile.save()

        self.assertFalse(self._status())

    def test_trial_never_used_does_not_show_popup(self):
        self.assertFalse(self._status())


@override_settings(SECURE_SSL_REDIRECT=False)
class TrialCancelLoopholeTests(TestCase):
    """A trial user must never be able to hit "Cancel Subscription" (there's
    nothing to cancel - a trial just lapses after 7 days on its own), and
    trial_used must permanently block a second trial. Regression for the
    reported loophole: the account settings page showed the real-subscriber
    "Cancel Premium Subscription" button during an app-managed trial too
    (its is_premium check didn't exclude is_trial), and downgrade_premium had
    no server-side guard against being hit directly while on a trial."""

    def setUp(self):
        self.user = User.objects.create_user(username='cancel_loophole_user', password='pass12345')
        self.client.login(username='cancel_loophole_user', password='pass12345')

    def test_trial_user_does_not_see_cancel_button(self):
        profile = self.user.profile
        profile.is_premium = True
        profile.is_trial = True
        profile.trial_used = True
        profile.premium_expires_at = timezone.now() + timedelta(days=7)
        profile.save()

        response = self.client.get(reverse('account_settings'))
        body = response.content.decode()

        self.assertNotIn('Cancel Premium Subscription', body)
        self.assertIn('Upgrade to Premium', body)

    def test_real_paying_user_sees_cancel_button(self):
        profile = self.user.profile
        profile.is_premium = True
        profile.is_trial = False
        profile.paypal_subscription_id = 'I-REALSUB'
        profile.premium_expires_at = timezone.now() + timedelta(days=20)
        profile.save()

        response = self.client.get(reverse('account_settings'))
        body = response.content.decode()

        self.assertIn('Cancel Premium Subscription', body)
        self.assertNotIn('Upgrade to Premium', body)

    def test_downgrade_endpoint_rejects_direct_hit_during_trial(self):
        profile = self.user.profile
        profile.is_premium = True
        profile.is_trial = True
        profile.trial_used = True
        profile.premium_expires_at = timezone.now() + timedelta(days=7)
        profile.save()

        response = self.client.post(reverse('downgrade_premium'))

        self.assertEqual(response.status_code, 400)
        profile.refresh_from_db()
        self.assertTrue(profile.is_premium)
        self.assertTrue(profile.is_trial)

    def test_downgrade_endpoint_still_works_for_real_subscribers(self):
        profile = self.user.profile
        profile.is_premium = True
        profile.is_trial = False
        profile.premium_expires_at = timezone.now() + timedelta(days=20)
        profile.save()

        response = self.client.post(reverse('downgrade_premium'))

        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(profile.is_trial)

    def test_trial_used_permanently_blocks_a_second_trial(self):
        profile = self.user.profile
        profile.trial_used = True
        profile.is_premium = False
        profile.is_trial = False
        profile.save()

        response = self.client.post(reverse('start_premium_trial'))

        self.assertEqual(response.status_code, 400)
        profile.refresh_from_db()
        self.assertFalse(profile.is_premium)
        self.assertFalse(profile.is_trial)

    def test_first_time_trial_still_succeeds(self):
        profile = self.user.profile
        profile.trial_used = False
        profile.is_premium = False
        profile.save()

        response = self.client.post(reverse('start_premium_trial'))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertTrue(profile.is_premium)
        self.assertTrue(profile.is_trial)
        self.assertTrue(profile.trial_used)


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    SECURE_SSL_REDIRECT=False,
)
class DowngradePremiumKeepsAccessUntilExpiryTests(TestCase):
    """Cancelling a real PayPal subscription must only stop future
    auto-renewal (via PayPal's own cancel API) - it must not revoke access
    before the already-paid-for premium_expires_at. This mirrors how Google
    Play cancellation already behaves (access continues to the end of the
    current billing period; our app has no code path that revokes early for
    Google Play, since there's no cancellation webhook wired up - the only
    thing that ever changes premium state for a Google Play user is the
    purchase-verify endpoint and expire_premium_subscriptions' re-verify
    branch, neither of which fires on cancellation itself)."""

    def setUp(self):
        self.user = User.objects.create_user(username='real_subscriber', password='pass12345')
        self.client.login(username='real_subscriber', password='pass12345')
        self.profile = self.user.profile
        self.profile.is_premium = True
        self.profile.is_trial = False
        self.profile.paypal_subscription_id = 'I-REALSUB'
        self.expiry = timezone.now() + timedelta(days=20)
        self.profile.premium_expires_at = self.expiry
        self.profile.save()

    @patch('thank_japan_app.views.requests.post')
    def test_cancel_keeps_access_until_expiry_and_stops_paypal_auto_renewal(self, mock_post):
        def post_side_effect(url, **kwargs):
            if 'oauth2/token' in url:
                return _mock_response(200, {'access_token': 'tok'})
            if url.endswith('/I-REALSUB/cancel'):
                return _mock_response(204, {})
            raise AssertionError(f'unexpected POST to {url}')
        mock_post.side_effect = post_side_effect

        response = self.client.post(reverse('downgrade_premium'))

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_premium)
        self.assertTrue(self.profile.has_premium_access)
        self.assertAlmostEqual(self.profile.premium_expires_at, self.expiry, delta=timedelta(seconds=5))

        cancel_calls = [
            c for c in mock_post.call_args_list
            if c.args and c.args[0].endswith('/I-REALSUB/cancel')
        ]
        self.assertEqual(len(cancel_calls), 1, "PayPal's cancel endpoint must be called exactly once")

    def test_expiry_batch_reverts_access_once_premium_expires_at_passes(self):
        # Simulate time having passed the already-paid-for period without a
        # renewal (as if PayPal's auto-renewal had indeed been stopped by an
        # earlier cancellation).
        self.profile.premium_expires_at = timezone.now() - timedelta(minutes=1)
        self.profile.save()

        call_command('expire_premium_subscriptions')

        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_premium)
        self.assertFalse(self.profile.has_premium_access)


LOCALE_QUERY_PARAMS = [
    'en', 'ja', 'de', 'en-in', 'es-es', 'es-mx', 'fr', 'it', 'ko',
    'pt', 'pt-br', 'th', 'vi', 'zh-hant', 'zh-cn',
]


@override_settings(SECURE_SSL_REDIRECT=False)
class GameResultReviewPromptTests(TestCase):
    """The in-app review prompt on the game_result screen (replacing the
    now-retired top-page version - see base_top.html/review_prompt_status
    history) must show only for TWA + logged-in + at least one correct
    answer + never shown before, and must permanently gate itself via
    Profile.review_prompt_shown the instant it's shown, not depend on any
    follow-up client action."""

    def setUp(self):
        self.user = User.objects.create_user(username='review_prompt_user', password='pass12345')
        self.client.login(username='review_prompt_user', password='pass12345')

    def _set_game_session(self, is_twa=True, correct=True):
        session = self.client.session
        session['game_history'] = [{
            'index': 0,
            'is_correct': correct,
            'points': 10 if correct else 0,
            'question_id': 999999,
            'combo': 1,
            'combo_bonus': 0,
        }]
        session['game_difficulty'] = 'normal'
        if is_twa:
            session['is_twa'] = True
        session.save()

    def test_shows_for_twa_logged_in_user_with_a_correct_answer(self):
        self._set_game_session(is_twa=True, correct=True)

        response = self.client.get(reverse('game_result'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'reviewPromptOverlay')
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.review_prompt_shown)

    def test_shown_flag_prevents_a_second_display(self):
        self.user.profile.review_prompt_shown = True
        self.user.profile.save()
        self._set_game_session(is_twa=True, correct=True)

        response = self.client.get(reverse('game_result'))

        self.assertNotContains(response, 'reviewPromptOverlay')

    def test_does_not_show_on_web_non_twa(self):
        self._set_game_session(is_twa=False, correct=True)

        response = self.client.get(reverse('game_result'))

        self.assertNotContains(response, 'reviewPromptOverlay')
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.review_prompt_shown)

    def test_does_not_show_with_zero_correct_answers(self):
        self._set_game_session(is_twa=True, correct=False)

        response = self.client.get(reverse('game_result'))

        self.assertNotContains(response, 'reviewPromptOverlay')
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.review_prompt_shown)

    def test_does_not_show_for_guests(self):
        self.client.logout()
        session = self.client.session
        session['game_history'] = [{
            'index': 0, 'is_correct': True, 'points': 10, 'question_id': 999999,
        }]
        session['game_difficulty'] = 'normal'
        session['is_twa'] = True
        session.save()

        response = self.client.get(reverse('game_result'))

        self.assertNotContains(response, 'reviewPromptOverlay')

    def test_links_to_play_store_review_page(self):
        self._set_game_session(is_twa=True, correct=True)

        response = self.client.get(reverse('game_result'))

        self.assertContains(response, 'https://play.google.com/store/apps/details?id=com.thankjapan.www.twa')

    def test_renders_without_error_in_all_15_locales(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                # Fresh eligibility each time - a prior iteration's render
                # already flips review_prompt_shown to True.
                self.user.profile.review_prompt_shown = False
                self.user.profile.save()
                self._set_game_session(is_twa=True, correct=True)

                response = self.client.get(reverse('game_result'), {'lang': lang})

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'reviewPromptOverlay')


@override_settings(SECURE_SSL_REDIRECT=False)
class WordDetailPageTests(TestCase):
    """Regression: CategoryDetailView/ImgPremiumDetailView.get_context_data
    used to call track_word_view() for the (now-retired) review-prompt word
    count. Removing that function without also removing these two call sites
    left every word-detail page (free and premium) throwing NameError on
    every request - caught in production via 5xx alerts, not by this test
    suite, since nothing had previously exercised these views at all."""

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='test-animal-dog', englishname='Dog', jpname='犬',
            category='Animal', slug='animal-test-dog',
            description='desc', history='hist',
        )
        self.premium_word = ThankJapanPremium.objects.create(
            name='test-premium-word', englishname='Premium Word', jpname='テスト',
            category='TourismEtiquette', slug='premium-test-word',
            description='desc', history='hist',
        )

    def test_free_word_detail_page_loads(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))

        self.assertEqual(response.status_code, 200)

    def test_premium_word_detail_page_loads(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))

        self.assertEqual(response.status_code, 200)
