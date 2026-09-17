import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import quote as urlquote

from dateutil.relativedelta import relativedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import context_processors, tile_split
from .models import DailyQuestion, Player, Profile, PremiumRenewalReward, ThankJapanModel, ThankJapanPremium, DailyActivitySummary
from .views import (
    combo_bonus_for_streak, build_combo_share_message, _sample_easy_ids, FIRST_TASTE_QUESTION_COUNT,
    grant_renewal_rewards_if_due, PREMIUM_RENEWAL_REWARD_POINTS, PREMIUM_RENEWAL_REWARD_MAX_CATCHUP,
    DAILY_QUESTION_SHARE_LOCALE_ALIAS, DAILY_QUESTION_SHARE_HEADING, DAILY_QUESTION_SHARE_INVITE,
    DAILY_QUESTION_SHARE_CTA, DAILY_QUESTION_SHARE_CHUNK_STEPS, DAILY_QUESTION_SHARE_ANSWER_LABEL,
    DAILY_QUESTION_HASHTAG_TERM,
)
from .pricing import (
    get_premium_price, get_top_page_seo_override,
    DEFAULT_TOP_PAGE_TITLE, DEFAULT_TOP_PAGE_DESCRIPTION,
)


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

# Expected strings transcribed independently from each template/dict, keyed
# per supported locale - used by the 2026-09-05 renewal's multi-language
# audit tests below to catch a silent English fallback (the exact bug
# reported for Korean before the tile-game rewrite), not just a crash.
# 2026-09-12: DAILY_SHARE_TEXT_BY_LOCALE/DAILY_HASHTAGS_BY_LOCALE (word-
# centric "assembly process" reveal, same text for correct/incorrect) is
# built directly from views.py's own per-piece dicts (imported above) rather
# than duplicated here - see DailyQuestionShareAfterTileConversionTests and
# RenewalMultiLanguageAuditTests.test_daily_question_share_text_and_hashtags_per_locale.

DAILY_CAPTION_BY_LOCALE = {
    'ja': 'タイルを正しい順番に並べ替えてください',
    'zh-hant': '請將方塊排成正確的順序',
    'zh-cn': '请将方块排成正确的顺序',
    'ko': '타일을 올바른 순서로 배열하세요',
    'de': 'Ordne die Kacheln in der richtigen Reihenfolge an',
    'fr': 'Réorganisez les tuiles dans le bon ordre',
    'es-es': 'Ordena las fichas en el orden correcto',
    'es-mx': 'Ordena las fichas en el orden correcto',
    'it': "Disponi le tessere nell'ordine corretto",
    'pt': 'Organize as peças na ordem correta',
    'pt-br': 'Organize as peças na ordem correta',
    'vi': 'Sắp xếp các ô theo đúng thứ tự',
    'th': 'จัดเรียงไทล์ให้ถูกลำดับ',
    'en': 'Arrange the tiles in the correct order',
    'en-in': 'Arrange the tiles in the correct order',
}

NO_TIME_LIMIT_BY_LOCALE = {
    'ja': '時間制限なし', 'zh-hant': '無時間限制', 'zh-cn': '无时间限制',
    'ko': '시간 제한 없음', 'de': 'Ohne Zeitlimit', 'fr': 'Sans limite de temps',
    'es-es': 'Sin límite de tiempo', 'es-mx': 'Sin límite de tiempo',
    'it': 'Senza limite di tempo', 'pt': 'Sem limite de tempo', 'pt-br': 'Sem limite de tempo',
    'vi': 'Không giới hạn thời gian', 'th': 'ไม่จำกัดเวลา',
    'en': 'No Time Limit', 'en-in': 'No Time Limit',
}

SIXTY_SEC_CHALLENGE_BY_LOCALE = {
    'ja': '60秒チャレンジ', 'zh-hant': '60秒挑戰', 'zh-cn': '60秒挑战',
    'ko': '60초 챌린지', 'de': '60-Sekunden-Challenge', 'fr': 'Défi 60 secondes',
    'es-es': 'Desafío de 60 segundos', 'es-mx': 'Desafío de 60 segundos',
    'it': 'Sfida di 60 secondi', 'pt': 'Desafio de 60 segundos', 'pt-br': 'Desafio de 60 segundos',
    'vi': 'Thử thách 60 giây', 'th': 'ท้าทาย 60 วินาที',
    'en': '60s Challenge', 'en-in': '60s Challenge',
}


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


class ReviewPromptSurvivesTileGameRenewalTests(TestCase):
    """2026-09-05: user asked us to reconfirm the in-app review prompt
    (game_result: is_twa AND logged in AND correct_count>=1 AND not
    review_prompt_shown) still works after the tile-rearrangement renewal.
    GameResultReviewPromptTests above proves the gating logic itself by
    hand-crafting session['game_history'] directly; this instead plays a
    real question through game_question_data/game_submit_answer so the
    'correct_count>=1' half of the gate is exercised via the actual tile
    scoring path, not a fixture standing in for it."""

    def setUp(self):
        self.user = User.objects.create_user(username='review_tile_tester', password='pass12345')
        self.client.login(username='review_tile_tester', password='pass12345')
        ThankJapanModel.objects.create(
            name='review-tile-word', englishname='Egg', jpname='たまご',
            kanji_name='卵', katakana_name='タマゴ', category='food',
            slug='review-tile-word', description='d', history='h')

    def _play_correctly_via_single_mode(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'review-tile-word',
        })
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        correct_order = sorted(data['tiles'], key=lambda t: 'たまご'.index(t))
        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)
        self.assertTrue(result['is_correct'], msg=result)

    def test_real_correct_tile_answer_yields_correct_count_and_shows_prompt_for_twa(self):
        self._play_correctly_via_single_mode()
        session = self.client.session
        session['is_twa'] = True
        session.save()

        response = self.client.get(reverse('game_result'))

        self.assertEqual(response.context['correct_count'], 1)
        self.assertContains(response, 'reviewPromptOverlay')
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.review_prompt_shown)

    def test_x_requested_with_header_signal_also_shows_prompt(self):
        # the header-based TWA signal (set by Chrome's TWA handshake),
        # independent of the session-flag path exercised above.
        self._play_correctly_via_single_mode()

        response = self.client.get(reverse('game_result'), HTTP_X_REQUESTED_WITH=settings.PACKAGE_NAME)

        self.assertContains(response, 'reviewPromptOverlay')

    def test_correct_tile_answer_alone_does_not_show_prompt_without_twa_signal(self):
        self._play_correctly_via_single_mode()

        response = self.client.get(reverse('game_result'))

        self.assertEqual(response.context['correct_count'], 1)
        self.assertNotContains(response, 'reviewPromptOverlay')
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.review_prompt_shown)

    def test_incorrect_tile_answer_does_not_show_prompt_even_when_twa(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'review-tile-word',
        })
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        wrong_order = list(reversed(data['tiles']))
        if wrong_order == sorted(data['tiles'], key=lambda t: 'たまご'.index(t)):
            self.skipTest('reversed order happened to already be correct')

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': wrong_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        self.assertFalse(json.loads(response.content)['is_correct'])

        session = self.client.session
        session['is_twa'] = True
        session.save()
        response = self.client.get(reverse('game_result'))

        self.assertEqual(response.context['correct_count'], 0)
        self.assertNotContains(response, 'reviewPromptOverlay')


class SingleModeReviewReturnsToDetailPageTests(TestCase):
    """2026-09-06: the tile-rearrangement renewal (c73a465a) rewrote
    game_play-v2.html from scratch and dropped the old per-answer 'Finish &
    Back to Lesson' button that mode=single (word-detail-page 'review this
    word') used to show - after that, finishing a single-mode review fell
    through to the same generic game_result -> game_start flow as a normal
    timed game, instead of returning the player to the word detail page they
    reviewed from. game_restart now stashes a single_return_url in the
    session for mode=single, and game_play-v2.html's finish modal reads it
    back as the JS constant SINGLE_RETURN_URL."""

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='single-return-word', englishname='Umbrella', jpname='かさ',
            category='dailyactions', slug='single-return-word',
            description='desc', history='hist',
        )
        self.premium_word = ThankJapanPremium.objects.create(
            name='single-return-premium-word', englishname='Premium Umbrella', jpname='かさ',
            category='TourismEtiquette', slug='single-return-premium-word',
            description='desc', history='hist',
        )

    def test_free_single_mode_stores_return_url_to_word_detail_page(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'single-return-word',
        })

        expected_url = reverse('category_detail', kwargs={
            'category': 'dailyactions', 'slug': 'single-return-word',
        })
        self.assertEqual(self.client.session['single_return_url'], expected_url)

    def test_premium_single_mode_stores_return_url_to_premium_detail_page(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'premium', 'slug': 'single-return-premium-word',
        })

        expected_url = reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'single-return-premium-word',
        })
        self.assertEqual(self.client.session['single_return_url'], expected_url)

    def test_game_play_embeds_return_url_as_js_constant_for_single_mode(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'single-return-word',
        })

        response = self.client.get(reverse('game_play'))

        expected_url = reverse('category_detail', kwargs={
            'category': 'dailyactions', 'slug': 'single-return-word',
        })
        # The URL is written into the page through the `escapejs` filter (so
        # e.g. hyphens become -) - assert on the value Django actually
        # resolved into the template context rather than the raw escaped
        # bytes, and separately confirm the JS constant itself is present.
        self.assertEqual(response.context['single_return_url'], expected_url)
        self.assertContains(response, 'const SINGLE_RETURN_URL = "')
        self.assertContains(response, 'id="i18n-single-finish-btn"')
        self.assertContains(response, 'function showSingleFinishModal')

    def test_normal_timeattack_game_has_no_return_url_and_does_not_leak_a_stale_one(self):
        # Play a single-mode review first so session['single_return_url'] is
        # set, then start a real game - it must not leak into the new game.
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'single-return-word',
        })
        self.client.get(reverse('game_restart'))

        self.assertNotIn('single_return_url', self.client.session)
        response = self.client.get(reverse('game_play'))
        self.assertContains(response, 'const SINGLE_RETURN_URL = null;')

    def test_finishing_the_single_question_shows_finish_modal_instead_of_advancing(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'single-return-word',
        })
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        correct_order = sorted(data['tiles'], key=lambda t: 'かさ'.index(t))

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )

        result = json.loads(response.content)
        self.assertTrue(result['is_correct'], msg=result)
        # game_submit_answer itself never redirects (it's AJAX) - the actual
        # redirect-vs-modal branching happens client-side in game_play-v2.html
        # based on SINGLE_RETURN_URL, verified above.
        self.assertFalse(result['game_over'])


class GameResultTimeLimitLabelTests(TestCase):
    """2026-09-05: the result screen always said '60s Challenge' even for
    untimed modes (mode=single practice, and the new first_taste trial) -
    that's just wrong for a game that never had a clock running. The label
    must reflect whether the just-played game actually had a time limit."""

    def _play_one_question(self, difficulty):
        session = self.client.session
        session['game_history'] = [{
            'index': 0, 'is_correct': True, 'points': 1, 'question_id': 999999,
            'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
        }]
        session['game_difficulty'] = difficulty
        session.save()
        return self.client.get(reverse('game_result'))

    def test_timeattack_shows_60_second_challenge_label(self):
        response = self._play_one_question('timeattack')
        self.assertContains(response, '60s Challenge')
        self.assertNotContains(response, 'No Time Limit')

    def test_single_practice_shows_no_time_limit_label(self):
        response = self._play_one_question('single')
        self.assertContains(response, 'No Time Limit')
        self.assertNotContains(response, '60s Challenge')

    def test_first_taste_shows_no_time_limit_label(self):
        response = self._play_one_question('first_taste')
        self.assertContains(response, 'No Time Limit')
        self.assertNotContains(response, '60s Challenge')


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

    def test_free_word_detail_page_has_seo_title_and_description(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()

        self.assertIn('Dog Meaning &amp; Pronunciation | Photo Quiz - ThankJapan', body)
        self.assertIn('<meta name="description" content="What does Dog mean in Japanese?', body)

    def test_premium_word_detail_page_has_seo_title_and_description(self):
        """Regression: the premium template used to hardcode "{englishname} -
        Premium Lesson | ThankJapan" and had no <meta name="description"> at
        all - the same GSC-identified gap fixed for the free tier
        (commit 43cfedc5) but never extended to premium words."""
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()

        self.assertIn('Premium Word Meaning &amp; Pronunciation | Photo Quiz - ThankJapan', body)
        self.assertIn('<meta name="description" content="What does Premium Word mean in Japanese?', body)


class WordDetailReviewInviteTests(TestCase):
    """2026-09-06: the word-detail page's 'Practice This Word' invite still
    described the old 4-choice game ('choose one of the 2 images') even
    though the tile-rearrangement renewal (c73a465a) replaced that game mode
    weeks ago - fixed to describe the actual tile-rearrangement mechanic,
    and the button itself was restyled to match the game's own C-plan
    (sugoroku x jewel) look instead of the old flat gold pill."""

    OLD_DESCRIPTION_SUBSTRINGS = ['2つの画像', '2 images', '2 imágenes', '2 Bilder']

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='invite-test-cat', englishname='Cat', jpname='ねこ',
            category='animal', slug='invite-test-cat',
            description='desc', history='hist',
        )
        self.premium_word = ThankJapanPremium.objects.create(
            name='invite-test-premium', englishname='Premium Word', jpname='テスト',
            category='TourismEtiquette', slug='invite-test-premium',
            description='desc', history='hist',
        )

    def _get_free(self, lang=None):
        params = {'lang': lang} if lang else {}
        return self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'invite-test-cat',
        }), params)

    def _get_premium(self, lang=None):
        params = {'lang': lang} if lang else {}
        return self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'invite-test-premium',
        }), params)

    def test_old_four_choice_description_is_gone_free_and_premium(self):
        free_body = self._get_free().content.decode()
        premium_body = self._get_premium().content.decode()

        for old_text in self.OLD_DESCRIPTION_SUBSTRINGS:
            self.assertNotIn(old_text, free_body)
            self.assertNotIn(old_text, premium_body)

    def test_new_tile_rearrangement_description_present_per_locale(self):
        expected_by_lang = {
            'ja': '文字を並べ替えて正解を目指そう', 'zh-hant': '排列文字', 'zh-cn': '排列文字',
            'ko': '글자를 배열해서', 'de': 'Ordne die Buchstaben',
            'fr': 'Réorganise les lettres', 'es-es': 'Ordena las letras',
            'it': 'Riordina le lettere', 'pt': 'Reorganize as letras',
            'vi': 'Sắp xếp lại các chữ cái', 'th': 'เรียงตัวอักษรใหม่',
            'en': 'Rearrange the tiles to find the correct answer',
        }
        for lang, expected in expected_by_lang.items():
            with self.subTest(lang=lang, page='free'):
                self.assertIn(expected, self._get_free(lang).content.decode())
            with self.subTest(lang=lang, page='premium'):
                self.assertIn(expected, self._get_premium(lang).content.decode())

    def test_review_button_uses_tile_icon_not_old_keyboard_icon(self):
        free_body = self._get_free().content.decode()
        premium_body = self._get_premium().content.decode()

        self.assertNotIn('⌨️', free_body)
        self.assertNotIn('⌨️', premium_body)
        self.assertIn('🧩', free_body)
        self.assertIn('🧩', premium_body)

    def test_review_button_has_new_gem_theme_animation(self):
        free_body = self._get_free().content.decode()
        premium_body = self._get_premium().content.decode()

        for body in (free_body, premium_body):
            self.assertIn('game-invite-btn-pulse', body)
            self.assertIn('game-invite-btn-shine', body)
            self.assertIn('rgba(46,196,182,', body)  # jewel-teal glow, matches the game's own palette


class WordDetailDirectLayoutCloseButtonTests(TestCase):
    """2026-09-06: mode=single's finish button (see
    SingleModeReviewReturnsToDetailPageTests) navigates the browser straight
    to the word-detail page's own URL - a real, standalone page load, not
    the JS-driven modal (#detailModal in e.g. animal.html) that clicking a
    flashcard from a category list normally opens. That standalone
    'direct-layout' render had no close/back affordance of its own at all
    (the modal's X button lives entirely in the category list page's own
    markup, never in the detail template), leaving the player with no way
    back to the word grid after finishing a review. base_detail.html/
    base_detail_premium.html now render their own '<->' back-to-list button
    whenever not embedded in that modal."""

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='close-btn-test-word', englishname='Cat', jpname='ねこ',
            category='animal', slug='close-btn-test-word',
            description='desc', history='hist',
        )
        self.premium_word = ThankJapanPremium.objects.create(
            name='close-btn-test-premium', englishname='Premium Word', jpname='テスト',
            category='TourismEtiquette', slug='close-btn-test-premium',
            description='desc', history='hist',
        )

    def test_direct_navigation_to_free_word_page_shows_close_button_to_category_list(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'close-btn-test-word',
        }))
        body = response.content.decode()

        self.assertIn('class="detail-back-close-btn"', body)
        self.assertIn('href="/animal/', body)

    def test_direct_navigation_to_premium_word_page_shows_close_button_to_category_list(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'close-btn-test-premium',
        }))
        body = response.content.decode()

        self.assertIn('class="detail-back-close-btn"', body)
        self.assertIn('href="/tourismetiquette/', body)

    def test_close_button_link_actually_resolves_to_the_category_list_page(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'close-btn-test-word',
        }))
        body = response.content.decode()
        self.assertIn('href="/animal/', body)

        list_response = self.client.get('/animal/')
        self.assertEqual(list_response.status_code, 200)

    def test_close_button_design_matches_the_category_list_modal_close_button(self):
        """2026-09-06: the button initially shipped as a plain circular X,
        visually inconsistent with the gold pill '[CLOSE] X' button every
        category list page's own #detailModal already uses (see e.g.
        animal.html's .modal-close-btn). Locked in here to the same visual
        declarations (color, shape, shadow, spacing) so the two never drift
        apart again - positioning (`position`/`z-index`) is intentionally
        NOT compared here: a later fix (see
        WordDetailStandaloneWidthTests) deliberately made the standalone
        page's button `position: absolute` inside the card instead of the
        modal button's `position: fixed`, so it sits near the image instead
        of floating level with the page header."""
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'close-btn-test-word',
        }), {'lang': 'ja'})
        body = response.content.decode()

        for declaration in [
            'height: 36px !important;',
            'padding: 0 15px !important;',
            'border-radius: 20px !important;',
            'background: #d4af37 !important;',
            'color: #000000 !important;',
            'box-shadow: 0 4px 10px rgba(0,0,0,0.3) !important;',
        ]:
            self.assertIn(declaration, body)
        self.assertIn('閉じる', body)  # ja CLOSE label, same wording as the modal button
        self.assertIn('✕', body)

    def test_premium_close_button_design_also_matches(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'close-btn-test-premium',
        }))
        body = response.content.decode()

        for declaration in [
            'height: 36px !important;',
            'background: #d4af37 !important;',
        ]:
            self.assertIn(declaration, body)


class WordDetailStandaloneWidthTests(TestCase):
    """2026-09-06: on wide (PC/tablet) screens, the standalone word-detail
    page (reached e.g. via the mode=single finish button) looked
    noticeably narrower than the ~94vw full-screen modal the same content
    renders in when opened from a category list - the standalone page used
    the site-wide 700px card cap shared by every other direct-layout page.
    Widened the cap to 960px for direct-layout word-detail pages only (a
    deliberate partial narrowing of the gap, not full parity - see
    conversation), leaving every other direct-layout page's 700px cap and
    the modal's own ~94vw sizing untouched."""

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='width-test-word', englishname='Cat', jpname='ねこ',
            category='animal', slug='width-test-word',
            description='desc', history='hist',
        )
        self.premium_word = ThankJapanPremium.objects.create(
            name='width-test-premium', englishname='Premium Word', jpname='テスト',
            category='TourismEtiquette', slug='width-test-premium',
            description='desc', history='hist',
        )

    def test_free_direct_layout_gets_widened_cap(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'width-test-word',
        }))
        body = response.content.decode()

        self.assertIn('#premium-auth-container.direct-layout .app-content-fix {', body)
        self.assertIn('max-width: 960px;', body)

    def test_premium_direct_layout_gets_widened_cap(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'width-test-premium',
        }))
        body = response.content.decode()

        self.assertIn('#premium-auth-container.direct-layout .app-content-fix {', body)
        self.assertIn('max-width: 960px;', body)

    def test_base_shared_700px_cap_is_unchanged(self):
        # The site-wide default (used by modal-layout, and by every other
        # direct-layout page that doesn't override it) must still be 700px -
        # only word-detail's direct-layout case gets the wider cap.
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'width-test-word',
        }))
        body = response.content.decode()

        self.assertIn('max-width: 700px;', body)

    def test_close_button_is_positioned_inside_the_content_card_not_the_viewport(self):
        """2026-09-06 follow-up x2: first made `position: fixed; right: 15px`
        track the 960px card's edge via a tablet/PC-only calc() override -
        that fixed the horizontal alignment, but the button was still
        `position: fixed` relative to the raw viewport, so it landed level
        with the page's own header (top of the screen) instead of near the
        image, looking like part of the header bar rather than the word
        card. Now `position: absolute` inside .app-content-fix (itself
        `position: relative` only for direct-layout, see below), which
        naturally sits just under the header at the card's own top-right
        corner - near the image - at every viewport width, with no calc()
        needed at all."""
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'width-test-word',
        }))
        body = response.content.decode()

        self.assertIn('.detail-back-close-btn {', body)
        self.assertIn('position: absolute !important;', body)
        self.assertNotIn('position: fixed !important;\n        top: 15px !important;\n        right: 15px !important;', body)
        # The button must be nested inside .app-content-fix (which carries
        # its own position: relative for direct-layout) rather than sitting
        # outside it, or absolute positioning would resolve against some
        # unrelated ancestor instead of the card itself.
        btn_pos = body.index('class="detail-back-close-btn"')
        content_fix_pos = body.index('class="app-content-fix"')
        self.assertGreater(btn_pos, content_fix_pos)

    def test_app_content_fix_is_positioned_only_for_direct_layout(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'width-test-word',
        }))
        body = response.content.decode()

        self.assertIn(
            '#premium-auth-container.direct-layout .app-content-fix {\n'
            '    max-width: 960px;\n'
            '    position: relative;\n'
            '}',
            body,
        )

    def test_premium_close_button_is_also_positioned_inside_the_content_card(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'width-test-premium',
        }))
        body = response.content.decode()

        self.assertIn('position: absolute !important;', body)
        btn_pos = body.index('class="detail-back-close-btn"')
        content_fix_pos = body.index('class="app-content-fix"')
        self.assertGreater(btn_pos, content_fix_pos)

    def test_close_button_nudges_down_only_above_768px(self):
        """2026-09-06 follow-up x3/x4: top: 15px reads fine on phones/small
        tablets but sits a bit too close to the card's top edge on PC/large
        tablet. >768px only pushes it down further (30px, then 45px per a
        second follow-up nudge); 768px and below (phones, small tablets)
        must render byte-for-byte the same base `top: 15px` as before this
        change."""
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'width-test-word',
        }))
        body = response.content.decode()

        self.assertIn('top: 15px !important;', body)
        self.assertIn('@media (min-width: 769px)', body)
        self.assertIn(
            '@media (min-width: 769px) {\n        .detail-back-close-btn {\n            top: 45px !important;\n        }\n    }',
            body,
        )

    def test_premium_close_button_also_nudges_down_only_above_768px(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'width-test-premium',
        }))
        body = response.content.decode()

        self.assertIn('top: 15px !important;', body)
        self.assertIn('@media (min-width: 769px)', body)
        self.assertIn('top: 45px !important;', body)


class SelectLanguageHeadingLocalizationTests(TestCase):
    """2026-09-06: the language-picker modal's own heading ('Select
    Language', templates/base/basecategory_top.html) was hardcoded in
    English while everything else on the page it lives on (bottom-nav
    labels, the sibling learn/menu modals' own headings) was already fully
    localized per lang_code - decided to fix the inconsistency rather than
    treat it as an intentional 'pre-language-choice stays English'
    convention, since by the time a visitor reaches this modal the
    surrounding page is already rendered in their language."""

    def setUp(self):
        ThankJapanModel.objects.create(
            name='lang-heading-test', englishname='Dog', jpname='いぬ',
            category='animal', slug='lang-heading-test',
            description='desc', history='hist',
        )

    def test_japanese_locale_shows_translated_heading(self):
        response = self.client.get(reverse('animalpage'), {'lang': 'ja'})
        self.assertContains(response, '言語を選択')
        self.assertNotContains(response, '>Select Language<')

    def test_default_locale_falls_back_to_english(self):
        response = self.client.get(reverse('animalpage'))
        self.assertContains(response, 'Select Language')


SELECT_LANGUAGE_HEADING_BY_LOCALE = {
    'en': 'Select Language', 'ja': '言語を選択', 'de': 'Sprache auswählen',
    'en-in': 'Select Language', 'es-es': 'Seleccionar idioma', 'es-mx': 'Seleccionar idioma',
    'fr': 'Choisir la langue', 'it': 'Seleziona lingua', 'ko': '언어 선택',
    'pt': 'Selecionar idioma', 'pt-br': 'Selecionar idioma', 'th': 'เลือกภาษา',
    'vi': 'Chọn ngôn ngữ', 'zh-hant': '選擇語言', 'zh-cn': '选择语言',
}


class SelectLanguageHeadingOnBaseTopTests(TestCase):
    """2026-09-06: the 'Select Language' gap fixed above only covered
    templates/base/basecategory_top.html (used by the per-category list
    pages like animal.html). base/base_top.html - the base actually used
    by the top page, account settings, category_list, legal/info/thank-you
    pages, and more - had the exact same hardcoded English h3 and was
    missed entirely. Fixed there too; this locks in all 15 locales so a
    future edit to one base template doesn't quietly leave the other one
    English-only again."""

    def test_all_15_locales_render_translated_heading_on_toppage(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = self.client.get(reverse('toppage'), {'lang': lang})
                self.assertContains(response, SELECT_LANGUAGE_HEADING_BY_LOCALE[lang])


class SingleModeIncorrectAnswerRevealTests(TestCase):
    """2026-09-06: a wrong answer during mode=single review (practicing one
    word from its detail page) silently moved on to the same generic
    'Review Complete!' finish modal as a correct answer, with no way to
    learn what the right answer actually was - defeats the point of a
    'review' flow. game_play-v2.html's finish modal now takes is_correct/
    correct_answer and shows the latter when wrong."""

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='reveal-test-word', englishname='Umbrella', jpname='かさ',
            category='dailyactions', slug='reveal-test-word',
            description='desc', history='hist',
        )

    def test_game_play_wires_correct_answer_into_finish_modal(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'reveal-test-word',
        })

        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn('id="i18n-single-correct-answer-label"', body)
        self.assertIn('showSingleFinishModal(res.is_correct, res.correct_answer)', body)
        self.assertIn('function buildSingleFinishContent(isCorrect, correctAnswer)', body)
        self.assertIn('function escapeHtml', body)

    def test_submit_answer_response_includes_correct_answer_when_wrong(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'reveal-test-word',
        })
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        wrong_order = list(reversed(data['tiles']))
        if wrong_order == sorted(data['tiles'], key=lambda t: 'かさ'.index(t)):
            self.skipTest('reversed order happened to already be correct')

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': wrong_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)

        self.assertFalse(result['is_correct'])
        self.assertEqual(result['correct_answer'], 'かさ')


class TopPageSeoOverrideTests(SimpleTestCase):
    """The top page's <title>/<meta name="description"> switch to a
    bilingual "{local} (Learn Japanese) | ThankJapan" pair for visitors from
    28 countries without one of the 15 fully-translated locales, based on
    the same Cloudflare-verified CF-IPCountry signal as get_premium_price -
    but must never affect pricing (separate dict, same detection helper
    only) nor any of the 15 locale-specific top pages (this only wires into
    TopView, see views.py)."""

    CLOUDFLARE_IP = '173.245.48.1'

    def _seo_for(self, country_code=None):
        kwargs = {'REMOTE_ADDR': self.CLOUDFLARE_IP, 'HTTP_X_FORWARDED_FOR': self.CLOUDFLARE_IP}
        if country_code:
            kwargs['HTTP_CF_IPCOUNTRY'] = country_code
        request = RequestFactory().get('/', **kwargs)
        return get_top_page_seo_override(request)

    def test_high_confidence_country_gets_bilingual_title(self):
        result = self._seo_for('ID')
        self.assertEqual(result['seo_title'], "Belajar Bahasa Jepang (Learn Japanese) | ThankJapan")
        self.assertIn('Belajar kosakata Jepang', result['seo_description'])

    def test_shared_language_countries_get_the_same_content(self):
        # CI/SN/CM all map to the same French copy; TZ/KE to the same Swahili.
        fr_titles = {self._seo_for(c)['seo_title'] for c in ('CI', 'SN', 'CM')}
        self.assertEqual(len(fr_titles), 1)
        sw_titles = {self._seo_for(c)['seo_title'] for c in ('TZ', 'KE')}
        self.assertEqual(len(sw_titles), 1)

    def test_medium_confidence_country_gets_bilingual_title(self):
        result = self._seo_for('MM')
        self.assertEqual(result['seo_title'], "ဂျပန်စာလေ့လာမယ် (Learn Japanese) | ThankJapan")

    def test_all_28_countries_have_working_overrides(self):
        countries = [
            'ID', 'PH', 'CI', 'SN', 'CM', 'IN', 'EG', 'MA', 'AE', 'SA', 'OM',
            'NL', 'PL', 'RU', 'SE', 'NO', 'DK', 'GR', 'TR', 'CH',
            'MM', 'KH', 'MN', 'PK', 'BD', 'TZ', 'KE', 'LK',
        ]
        for country in countries:
            with self.subTest(country=country):
                result = self._seo_for(country)
                self.assertNotEqual(result['seo_title'], DEFAULT_TOP_PAGE_TITLE)
                self.assertIn('ThankJapan', result['seo_title'])
                self.assertIn('Learn Japanese vocabulary', result['seo_description'])

    def test_ng_gh_za_deliberately_stay_on_the_english_default(self):
        for country in ('NG', 'GH', 'ZA'):
            with self.subTest(country=country):
                result = self._seo_for(country)
                self.assertEqual(result['seo_title'], DEFAULT_TOP_PAGE_TITLE)
                self.assertEqual(result['seo_description'], DEFAULT_TOP_PAGE_DESCRIPTION)

    def test_country_not_in_the_list_falls_back_to_english_default(self):
        result = self._seo_for('US')
        self.assertEqual(result['seo_title'], DEFAULT_TOP_PAGE_TITLE)
        self.assertEqual(result['seo_description'], DEFAULT_TOP_PAGE_DESCRIPTION)

    def test_one_of_the_15_existing_locales_also_falls_back_to_english_default(self):
        # These countries have their own dedicated top-page view/template
        # (e.g. Japan -> TopViewJA) that never calls get_top_page_seo_override
        # at all; this only documents that the function itself is harmless
        # if ever called for one.
        result = self._seo_for('JP')
        self.assertEqual(result['seo_title'], DEFAULT_TOP_PAGE_TITLE)

    def test_undetectable_country_falls_back_to_english_default(self):
        result = self._seo_for(None)
        self.assertEqual(result['seo_title'], DEFAULT_TOP_PAGE_TITLE)
        self.assertEqual(result['seo_description'], DEFAULT_TOP_PAGE_DESCRIPTION)

    def test_non_cloudflare_request_is_not_trusted_even_with_country_header(self):
        # A request that bypasses Cloudflare (e.g. hitting the raw Heroku
        # domain directly) must not let a spoofed CF-IPCountry header choose
        # the SEO copy - same trust boundary as get_premium_price.
        request = RequestFactory().get(
            '/', REMOTE_ADDR='8.8.8.8', HTTP_X_FORWARDED_FOR='8.8.8.8', HTTP_CF_IPCOUNTRY='ID',
        )
        result = get_top_page_seo_override(request)
        self.assertEqual(result['seo_title'], DEFAULT_TOP_PAGE_TITLE)

    def test_pricing_is_unaffected_by_the_seo_override_refactor(self):
        # detect_cf_country() was factored out of get_premium_price for
        # reuse here - must not change its behavior.
        request = RequestFactory().get('/premium/', **{
            'REMOTE_ADDR': self.CLOUDFLARE_IP, 'HTTP_X_FORWARDED_FOR': self.CLOUDFLARE_IP,
            'HTTP_CF_IPCOUNTRY': 'ID',
        })
        result = get_premium_price(request)
        self.assertEqual(result['price_usd'], '1.75')
        self.assertEqual(result['price_tier'], 'A')
        self.assertEqual(result['detected_country'], 'ID')


@override_settings(SECURE_SSL_REDIRECT=False)
class TopPageRendersSeoOverrideTests(TestCase):
    """Integration check: TopView actually wires seo_title/seo_description
    into the rendered page, and the 15 locale-specific top pages (which
    don't call get_top_page_seo_override at all) are untouched."""

    CLOUDFLARE_IP = '173.245.48.1'

    def test_root_page_shows_bilingual_title_for_a_covered_country(self):
        response = self.client.get('/', **{
            'REMOTE_ADDR': self.CLOUDFLARE_IP, 'HTTP_X_FORWARDED_FOR': self.CLOUDFLARE_IP,
            'HTTP_CF_IPCOUNTRY': 'ID',
        })
        self.assertContains(response, "Belajar Bahasa Jepang (Learn Japanese) | ThankJapan")

    def test_root_page_shows_english_default_without_a_country_match(self):
        response = self.client.get('/')
        self.assertContains(response, DEFAULT_TOP_PAGE_TITLE.replace('&', '&amp;'))

    def test_japanese_locale_top_page_is_unaffected(self):
        response = self.client.get('/ja/', **{
            'REMOTE_ADDR': self.CLOUDFLARE_IP, 'HTTP_X_FORWARDED_FOR': self.CLOUDFLARE_IP,
            'HTTP_CF_IPCOUNTRY': 'ID',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Belajar Bahasa Jepang")


@override_settings(SECURE_SSL_REDIRECT=False)
class DailyQuestionOgpCacheBustTests(TestCase):
    """/daily/'s og:image is correct on every request (it's just today's
    word's image), but X/other social crawlers cache OGP data keyed by URL,
    not by response - a fixed /daily/ URL kept serving whatever image was
    cached the first time it got crawled, days after that word had changed
    (reported 2026-09-04: stuck on a "職人" image for 2-3 days while the
    actual word had moved on to "受験"). Stamping today's date onto the
    shared URL (and echoing it back as og:url) makes every day a distinct
    URL with nothing stale to serve."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = DailyQuestion.objects.create(
            date=self.today,
            word=ThankJapanModel.objects.create(
                name='test-daily-word', englishname='Exam', jpname='受験',
                category='culture', slug='daily-test-word',
                description='desc', history='hist',
            ),
        ).word

    def test_question_state_og_url_and_share_link_include_todays_date(self):
        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        expected_url = f"https://www.thankjapan.com/daily/?lang=en&date={self.today.isoformat()}"
        self.assertIn(f'<meta property="og:url" content="{expected_url.replace("&", "&amp;")}" />', body)

    def test_result_state_og_url_includes_todays_date(self):
        response = self.client.post(reverse('daily_question'), {'choice_id': self.word.id})
        body = response.content.decode()

        expected_url = f"https://www.thankjapan.com/daily/?lang=en&date={self.today.isoformat()}"
        self.assertIn(f'<meta property="og:url" content="{expected_url.replace("&", "&amp;")}" />', body)

        # 2026-09-12: the share button's own url= now points at the word's
        # permanent detail page instead of this dated /daily/ URL - see
        # DailyQuestionShareLinksToWordDetailPageTests for that behavior.
        # 2026-09-15: carries a &scb=<today> cache-buster too (see
        # _daily_question_word_detail_url) so a permanent URL crawled once
        # with a broken OGP card can't stay stuck showing it forever.
        expected_share_url = (
            f"https://www.thankjapan.com{reverse('category_detail', kwargs={'category': 'culture', 'slug': 'daily-test-word'})}"
            f"?lang=en&scb={self.today.isoformat()}"
        )
        self.assertIn(f'url={urlquote(expected_share_url, safe="/")}', body)

    def test_og_image_reflects_todays_actual_word(self):
        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        # No image uploaded in the test fixture, so it must fall back to the
        # shared default background - the point being it's derived from
        # today's actual word/state on every request, never a stale value.
        self.assertIn('res.cloudinary.com', body)

    def test_share_url_changes_the_day_after(self):
        tomorrow = self.today + timedelta(days=1)
        with patch('thank_japan_app.views.timezone.localdate', return_value=tomorrow):
            DailyQuestion.objects.create(
                date=tomorrow,
                word=ThankJapanModel.objects.create(
                    name='test-daily-word-2', englishname='Craftsman', jpname='職人',
                    category='work', slug='daily-test-word-2',
                    description='desc', history='hist',
                ),
            )
            response = self.client.get(reverse('daily_question'))

        self.assertIn(f'date={tomorrow.isoformat()}', response.content.decode())
        self.assertNotIn(f'date={self.today.isoformat()}', response.content.decode())


class TileSplitTests(SimpleTestCase):
    """Mora/chunk splitting for the tile-rearrangement game renewal.
    See memory/project_game_renewal_feasibility_2026_09_04.md for why
    chunk-split reads example_sentence_ja instead of the bare reading."""

    def test_split_mora_handles_youon_sokuon_chouon(self):
        self.assertEqual(tile_split.split_mora('とまと'), ['と', 'ま', 'と'])
        self.assertEqual(tile_split.split_mora('ぎょうれつ'), ['ぎょ', 'う', 'れ', 'つ'])
        self.assertEqual(tile_split.split_mora('けっこん'), ['け', 'っ', 'こ', 'ん'])
        self.assertEqual(tile_split.split_mora('こんばん'), ['こ', 'ん', 'ば', 'ん'])
        self.assertEqual(tile_split.split_mora('あぱーと'), ['あ', 'ぱ', 'ー', 'と'])
        self.assertEqual(tile_split.split_mora('しゃしん'), ['しゃ', 'し', 'ん'])

    def test_split_mora_rejects_non_kana(self):
        self.assertIsNone(tile_split.split_mora('走る'))
        self.assertIsNone(tile_split.split_mora('RC'))
        self.assertIsNone(tile_split.split_mora('えーてぃーえむ RC'))
        self.assertIsNone(tile_split.split_mora(''))
        self.assertIsNone(tile_split.split_mora(None))

    def test_classify_route_boundary_is_five_mora(self):
        five = tile_split.classify_route('あいすくり')  # 5 mora
        self.assertEqual(five['route'], 'mora')
        self.assertEqual(five['mora_count'], 5)

        six = tile_split.classify_route('がそりんすたんど')  # 8 mora
        self.assertEqual(six['route'], 'chunk')

    def test_classify_route_alphabet_forces_chunk_even_if_short(self):
        result = tile_split.classify_route('RC')
        self.assertEqual(result['route'], 'chunk')
        self.assertTrue(result['has_alpha'])

    def test_strip_ruby_tags_keeps_kanji_drops_furigana(self):
        html = '<ruby>建設現場<rt>けんせつげんば</rt></ruby>で<ruby>働<rt>はたら</rt></ruby>きます'
        self.assertEqual(tile_split.strip_ruby_tags(html), '建設現場で働きます')

    def test_strip_ruby_tags_passthrough_for_plain_text(self):
        self.assertEqual(tile_split.strip_ruby_tags('こんにちは'), 'こんにちは')
        self.assertIsNone(tile_split.strip_ruby_tags(None))

    def test_split_chunks_matches_hand_picked_examples(self):
        self.assertEqual(
            tile_split.split_chunks('駐輪場のルールを教えてください'),
            ['駐輪場の', 'ルールを', '教えてください'],
        )
        self.assertEqual(tile_split.split_chunks('木造とRC'), ['木造と', 'RC'])

    def test_split_chunks_merges_compound_nouns_and_prefixes(self):
        self.assertEqual(tile_split.split_chunks('緊急連絡先はここです'), ['緊急連絡先は', 'ここです'])
        self.assertEqual(tile_split.split_chunks('ご協力ありがとうございます'), ['ご協力', 'ありがとうございます'])

    def test_split_chunks_empty_input(self):
        self.assertEqual(tile_split.split_chunks(''), [])
        self.assertEqual(tile_split.split_chunks(None), [])

    def test_build_tiles_mora_route_uses_jpname(self):
        result = tile_split.build_tiles('とまと', example_sentence_ja='<ruby>使わない</ruby>')
        self.assertEqual(result['route'], 'mora')
        self.assertEqual(result['tiles'], ['と', 'ま', 'と'])
        self.assertEqual(result['answer_text'], 'とまと')

    def test_build_tiles_force_chunk_overrides_a_short_mora_eligible_reading(self):
        """2026-09-06: premium review (mode=single) wants every premium
        category quizzed from its example sentence, even for a <=5-mora
        reading that would otherwise fall back to a trivial single-word
        mora puzzle (e.g. 'いわてけん', 5 mora) and ignore the example
        sentence entirely - inconsistent with longer premium words like
        'ほっかいどう' (6 mora) that already route to 'chunk' normally."""
        html = '<ruby>岩手県<rt>いわてけん</rt></ruby>は<ruby>山<rt>やま</rt></ruby>が<ruby>多<rt>おお</rt></ruby>いです。'
        without_force = tile_split.build_tiles('いわてけん', example_sentence_ja=html)
        self.assertEqual(without_force['route'], 'mora')  # baseline: still mora without force_chunk

        result = tile_split.build_tiles('いわてけん', example_sentence_ja=html, force_chunk=True)
        self.assertEqual(result['route'], 'chunk')
        self.assertEqual(result['tiles'], ['岩手県は', '山が', '多いです'])
        self.assertEqual(result['answer_text'], ''.join(result['tiles']))

    def test_build_tiles_force_chunk_falls_back_to_mora_when_no_example_sentence(self):
        # Safety net: a premium record missing example_sentence_ja (data
        # gap) must not become an unplayable empty-tile question just
        # because force_chunk was requested.
        result = tile_split.build_tiles('とまと', example_sentence_ja=None, force_chunk=True)
        self.assertEqual(result['route'], 'mora')
        self.assertEqual(result['tiles'], ['と', 'ま', 'と'])

    def test_build_tiles_force_chunk_is_a_no_op_when_already_chunk_route(self):
        html = '<ruby>建設現場<rt>けんせつげんば</rt></ruby>で<ruby>働<rt>はたら</rt></ruby>きます'
        result = tile_split.build_tiles('けんせつげんば', example_sentence_ja=html, force_chunk=True)
        self.assertEqual(result['route'], 'chunk')
        self.assertEqual(result['tiles'], ['建設現場で', '働きます'])

    def test_build_tiles_chunk_route_uses_example_sentence_ja(self):
        html = '<ruby>建設現場<rt>けんせつげんば</rt></ruby>で<ruby>働<rt>はたら</rt></ruby>きます'
        result = tile_split.build_tiles('けんせつげんば', example_sentence_ja=html)
        self.assertEqual(result['route'], 'chunk')
        self.assertEqual(result['answer_text'], '建設現場で働きます')
        self.assertEqual(result['tiles'], ['建設現場で', '働きます'])

    def test_build_tiles_answer_text_matches_tiles_even_with_source_spaces(self):
        # 2026-09-05 bug: some example_sentence_ja values have literal
        # spaces between words for readability, but fugashi/MeCab silently
        # drops whitespace tokens - it never emits a token for them. If
        # answer_text used the raw (space-containing) source text, a
        # player who correctly reassembles every tile shown would still
        # be marked wrong, since the spaces are unreachable from any tile.
        result = tile_split.build_tiles('けがをしました', '指を 切ったので 絆創膏を 貼りました。')
        self.assertEqual(result['tiles'], ['指を切ったので', '絆創膏を', '貼りました'])
        self.assertEqual(result['answer_text'], ''.join(result['tiles']))
        self.assertNotIn(' ', result['answer_text'])

    def test_split_chunks_drops_meaningless_punctuation(self):
        # 2026-09-05: 読点・句点・感嘆符・括弧・中黒・アポストロフィ等は
        # タイルとして出さず、正解文字列からも除外する。
        self.assertEqual(tile_split.split_chunks('犬、猫、鳥が好きです。'), ['犬猫鳥が', '好きです'])
        self.assertEqual(tile_split.split_chunks('それは「いい」と思います！'), ['それはいいと', '思います'])
        self.assertEqual(tile_split.split_chunks("それは'ペン'です"), ['それは', 'ペンです'])
        # the katakana long-vowel mark (ー) must never be treated as a
        # symbol to strip - it's part of the word's own token, not a
        # separate punctuation token.
        self.assertEqual(tile_split.split_chunks('エレキギターを弾きます'), ['エレキギターを', '弾きます'])

    def test_build_tiles_answer_text_excludes_punctuation(self):
        html = '<ruby>犬<rt>いぬ</rt></ruby>、<ruby>猫<rt>ねこ</rt></ruby>が<ruby>好<rt>す</rt></ruby>きです。'
        result = tile_split.build_tiles('いぬとねこ', example_sentence_ja=html)
        self.assertNotIn('、', result['answer_text'])
        self.assertNotIn('。', result['answer_text'])
        self.assertEqual(result['answer_text'], ''.join(result['tiles']))

    def test_build_tiles_handles_two_sentences_joined_by_period_and_space(self):
        """2026-09-06: reported as a suspected regression of the same class
        as the "エレキギター" space-drop bug (see
        test_build_tiles_answer_text_matches_tiles_even_with_source_spaces)
        and the punctuation-exclusion fix above - a real premium word
        (ThankJapanPremium id=281, slug='niigataken', category=Prefectures)
        whose example_sentence_ja is two full sentences joined by '。 '
        (a sentence-final period followed by a literal space before the
        second sentence starts). Confirmed via production data that this is
        the exact live content. answer_text must still be exactly the
        tile-reconstructible text with no leftover punctuation/whitespace,
        and the chunk boundary at the sentence break must not swallow or
        merge words from the following sentence."""
        html = (
            '<ruby>新潟県<rt>にいがたけん</rt></ruby>はお<ruby>米<rt>こめ</rt></ruby>と'
            'お<ruby>酒<rt>さけ</rt></ruby>がとてもおいしいです。 '
            '<ruby>冬<rt>ふゆ</rt></ruby>はスキーに<ruby>行<rt>い</rt></ruby>きたいですね。'
        )
        result = tile_split.build_tiles('にいがたけん', example_sentence_ja=html)

        self.assertEqual(result['route'], 'chunk')
        self.assertEqual(
            result['tiles'],
            ['新潟県は', 'お米と', 'お酒が', 'とても', 'おいしいです', '冬は', 'スキーに行きたいですね'],
        )
        self.assertEqual(result['answer_text'], ''.join(result['tiles']))
        self.assertNotIn(' ', result['answer_text'])
        self.assertNotIn('。', result['answer_text'])
        # The word immediately after the period ('冬は') must be its own
        # tile, not fused onto the end of the first sentence's last chunk.
        self.assertIn('冬は', result['tiles'])


class ComboBonusTests(SimpleTestCase):
    """2026-09-06: simplified from the tiered escalation (2:+1/5:+2/7:+3)
    to a flat +1 once the streak reaches 3, with no further increase no
    matter how long the streak runs - no negative bonus for a miss either,
    that was explicitly left out of scope."""

    def test_combo_bonus_is_flat_one_from_three_onward(self):
        expected = {1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1, 20: 1, 100: 1}
        for combo, bonus in expected.items():
            self.assertEqual(combo_bonus_for_streak(combo), bonus, f"combo={combo}")

    def test_share_message_mid_tier_now_starts_at_three_not_two(self):
        # 'mid' tier ('Max combo achieved!' style message) used to fire from
        # max_combo>=2 (the old first bonus tier) - moved to >=3 to match
        # where the (now flat) bonus actually starts, so the share text
        # never brags about a bonus that wasn't actually earned.
        none_msg = build_combo_share_message('en', 2, 10)
        mid_msg = build_combo_share_message('en', 3, 10)
        high_msg = build_combo_share_message('en', 7, 10)

        self.assertNotIn('combo', none_msg.lower())
        self.assertIn('Max combo', mid_msg)
        self.assertIn('combo streak', high_msg)


class GamePlayComboEffectsSimplificationTests(TestCase):
    """2026-09-06: the confetti color/size tiers, the 'X COMBO!' popup
    color tiers, and the ki(combo) gauge were all keyed to the old tiered
    bonus escalation (2/5/7) - simplified to match the new flat +1-at-3+
    bonus: two states only (below the bonus threshold vs. at/above it), and
    the once-only 'combo>=7' hanko-stamp celebration (tied to a tier that
    no longer exists) removed outright rather than left dangling on a
    threshold with no scoring meaning."""

    def setUp(self):
        ThankJapanModel.objects.create(
            name='combo-fx-word', englishname='Word', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='combo-fx-word', description='d', history='h')

    def test_ki_gauge_js_is_capped_at_three_not_seven(self):
        self.client.get(reverse('game_restart'))
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn('Math.min(combo, 3)', body)
        self.assertIn("fill.style.width = (capped / 3 * 100)", body)
        self.assertIn("fill.classList.toggle('maxed', combo >= 3)", body)
        self.assertNotIn('Math.min(combo, 7)', body)

    def test_old_ki_gauge_tick_marks_are_gone(self):
        self.client.get(reverse('game_restart'))
        response = self.client.get(reverse('game_play'))
        self.assertNotContains(response, 'ki-tick')

    def test_confetti_and_popup_tiers_are_two_state_not_three(self):
        self.client.get(reverse('game_restart'))
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn("if (res.combo >= 3) { comboColors", body)
        self.assertNotIn("res.combo >= 7", body)
        self.assertNotIn("res.combo >= 5", body)
        self.assertIn("const tier = combo >= 3 ? 'tier-orange' : 'tier-yellow'", body)
        self.assertNotIn('tier-rainbow', body)

    def test_combo_popup_shows_starting_at_two_with_no_upper_cutoff(self):
        self.client.get(reverse('game_restart'))
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn('if (res.combo >= 2) showComboPopup(res.combo)', body)

    def test_goku_celebration_removed(self):
        self.client.get(reverse('game_restart'))
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertNotIn('playGokuCelebration', body)
        self.assertNotIn('hanko-stamp"', body)


class GameResultComboGaugeAndMessageTests(TestCase):
    """2026-09-06: the result screen's own 'max combo' gauge/message used
    the same old /7 scale and >=2 bonus-earned gate as the in-game HUD -
    updated in lockstep so a max_combo of 3-6 doesn't show a half-empty
    gauge despite the bonus having been active the whole time."""

    def _play_with_combo(self, combo_len):
        self.client.get(reverse('game_restart'))
        self.client.get(reverse('game_play'))
        history = []
        for i in range(combo_len):
            combo = i + 1
            bonus = combo_bonus_for_streak(combo)
            history.append({
                'index': i, 'question_id': 1, 'question_model': 'free',
                'is_correct': True, 'user_input': 'x', 'correct_answer': 'x',
                'combo': combo, 'combo_bonus': bonus, 'points': 1 + bonus,
            })
        session = self.client.session
        session['game_history'] = history
        session['game_max_combo'] = combo_len
        session['game_difficulty'] = 'timeattack'
        session.save()
        return self.client.get(reverse('game_result'))

    def test_gauge_is_full_and_maxed_at_combo_three(self):
        response = self._play_with_combo(3)
        self.assertEqual(response.context['combo_gauge_capped'], 3)
        self.assertContains(response, 'ki-result-fill maxed')
        self.assertContains(response, 'width: 100%')

    def test_gauge_is_not_maxed_at_combo_two(self):
        response = self._play_with_combo(2)
        self.assertEqual(response.context['combo_gauge_capped'], 2)
        self.assertNotContains(response, 'ki-result-fill maxed')

    def test_bonus_message_shows_from_combo_three_not_two(self):
        response_two = self._play_with_combo(2)
        response_three = self._play_with_combo(3)

        self.assertNotContains(response_two, 'Combo Bonus')
        self.assertContains(response_three, 'Combo Bonus')


class GameStartComboRuleTextTests(TestCase):
    """2026-09-06: the game_start rules caption spelled out the old tiered
    escalation (2問+1点/5問+2点/7問+3点等) in all 15 locales - rewritten to
    describe the new flat +1-at-3+ bonus, and the old text must be gone so
    a returning player isn't told a rule that no longer applies."""

    def test_new_flat_bonus_text_present_and_old_tiers_gone(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = self.client.get(reverse('game_start'), {'lang': lang})
                body = response.content.decode()
                self.assertIn('+1', body)
                self.assertNotIn('5問+2点', body)
                self.assertNotIn('7問+3点', body)
                self.assertNotIn('+2pt', body)
                self.assertNotIn('+3pt', body)


GAME_START_DOUBLE_POINTS_TEXT_BY_LOCALE = {
    'en': 'Premium words earn double points', 'ja': 'プレミアム単語は2倍ポイント',
    'de': 'Premium-Wörter geben doppelte Punkte', 'en-in': 'Premium words earn double points',
    'es-es': 'Las palabras premium dan el doble de puntos',
    'es-mx': 'Las palabras premium dan el doble de puntos',
    'fr': 'Les mots premium rapportent le double de points',
    'it': 'Le parole premium danno il doppio dei punti', 'ko': '프리미엄 단어는 2배 포인트',
    'pt': 'Palavras premium dão pontos em dobro', 'pt-br': 'Palavras premium dão pontos em dobro',
    'th': 'คำศัพท์พรีเมียมได้คะแนนเป็น 2 เท่า', 'vi': 'Từ premium được gấp đôi điểm',
    'zh-hant': '進階單字可獲得雙倍分數！', 'zh-cn': '进阶单词可获得双倍分数！',
}

# premium_info's plan-features <li> wraps the key phrase in <strong> with an
# &nbsp; before it (e.g. 'Premium Words Give&nbsp;<strong>Double
# Points</strong>') - assert on just the <strong>-wrapped phrase itself
# rather than the full sentence, since &nbsp;/tag markup between words
# means the full sentence is never one contiguous substring of the raw HTML.
PREMIUM_INFO_DOUBLE_POINTS_PHRASE_BY_LOCALE = {
    'en': 'Double Points', 'ja': '2倍ポイント', 'de': 'doppelte Punkte',
    'en-in': 'Double Points', 'es-es': 'el doble de puntos', 'es-mx': 'el doble de puntos',
    'fr': 'points doublés', 'it': 'punti doppi', 'ko': '2배 포인트',
    'pt': 'pontos em dobro', 'pt-br': 'pontos em dobro',
    'th': 'คะแนน 2 เท่า', 'vi': 'gấp đôi điểm',
    'zh-hant': '雙倍點數', 'zh-cn': '双倍积分',
}


class GameStartAndPremiumInfoDoublePointsDisclosureTests(TestCase):
    """2026-09-06: the 'premium words earn double points' scoring rule was
    never documented anywhere on the site - added to game_start's rules
    caption (right under the combo bonus line) and to the premium_info
    plan-features list (both the free 'web' branch and the TWA branch), in
    all 15 locales."""

    def test_game_start_discloses_double_points_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = self.client.get(reverse('game_start'), {'lang': lang})
                self.assertContains(response, GAME_START_DOUBLE_POINTS_TEXT_BY_LOCALE[lang])

    def test_premium_info_discloses_double_points_per_locale_both_branches(self):
        for url_name in PREMIUM_INFO_URL_NAMES:
            lang = 'en' if url_name == 'premium_info' else url_name.replace('premium_info', '')
            lang_key = {
                'de': 'de', 'enIN': 'en-in', 'esES': 'es-es', 'esMX': 'es-mx', 'fr': 'fr',
                'it': 'it', 'ja': 'ja', 'ko': 'ko', 'pt': 'pt', 'ptBR': 'pt-br', 'th': 'th',
                'vi': 'vi', 'zhHANT': 'zh-hant', 'zhCN': 'zh-cn',
            }.get(lang, 'en')
            expected = PREMIUM_INFO_DOUBLE_POINTS_PHRASE_BY_LOCALE[lang_key]
            with self.subTest(url_name=url_name, branch='web'):
                response = self.client.get(reverse(url_name))
                self.assertContains(response, expected)
            with self.subTest(url_name=url_name, branch='twa'):
                session = self.client.session
                session['is_twa'] = True
                session.save()
                response = self.client.get(reverse(url_name))
                self.assertContains(response, expected)
                session = self.client.session
                del session['is_twa']
                session.save()


class GameStartLongLocaleTextScrollFixTests(TestCase):
    """2026-09-06 (mode=single game_start page): reported bug - the 'Back
    to Home' link fell off the bottom of the screen and was completely
    unreachable (not just requiring a scroll) on longer-text locales like
    French/Vietnamese, worse now that the rules caption grew an extra line
    for the double-points disclosure above. Root cause: the shared base
    template sets html{overflow:hidden;height:100vh} for pages meant to
    fit one viewport; game_start's own body{overflow-y:auto} override was
    silently neutralized because the ROOT element's overflow (not body's)
    governs whether the viewport can scroll at all. Fixed by also
    overriding html's overflow here."""

    def test_html_overflow_override_present(self):
        response = self.client.get(reverse('game_start'))
        self.assertContains(response, 'overflow-y: auto !important;')
        # Must appear inside an `html { ... }` rule, not just anywhere on
        # the page (e.g. some unrelated scrollable inner panel).
        body = response.content.decode()
        html_rule_start = body.index('html {')
        self.assertIn(
            'overflow-y: auto !important;',
            body[html_rule_start:html_rule_start + 1200],
        )


class GameStartPhoneCompactionTests(TestCase):
    """2026-09-06 follow-up: rather than rely solely on the scroll fallback
    above, the phone layout (both the general <=767px rules and the extra
    <=359px squeeze for the smallest common width) was tightened - smaller
    fonts, reduced margins/padding throughout the title/play-button/rules-
    card/player-info/back-home stack - so that on real small AND large
    phones, everything from the title through 'Back to Home' fits in one
    viewport without scrolling, even for the longest-text locales (French,
    German, Vietnamese...), which was verified by hand against a live
    local server at 320-390px widths. This test locks in that the compact
    ruleset stays present and the scroll-safety-net from the previous fix
    isn't accidentally removed while iterating on it further."""

    def test_phone_media_query_has_the_compact_overrides(self):
        response = self.client.get(reverse('game_start'))
        body = response.content.decode()

        media_start = body.index('@media (max-width: 767px)')
        # Only need to reach the closing brace of this block, not the next
        # media block too - 2000 chars comfortably covers it.
        block = body[media_start:media_start + 2000]

        for declaration in [
            '.container { margin-top:', '.start-game-title { font-size:',
            '.play-btn { font-size:', '.rules-card { margin-top:',
            '.rules-caption { font-size:', '.player-info { margin-top:',
            '.back-home-wrap {', '.btn-more { padding:',
        ]:
            self.assertIn(declaration, block, msg=f"missing {declaration!r} in phone media block")

    def test_extra_small_phone_breakpoint_present(self):
        response = self.client.get(reverse('game_start'))
        self.assertContains(response, '@media (max-width: 359px)')

    def test_scroll_safety_net_still_present_alongside_compaction(self):
        # The compaction is a best-effort fit - html/body must still allow
        # scrolling for whatever combination doesn't quite make it (see
        # GameStartLongLocaleTextScrollFixTests).
        response = self.client.get(reverse('game_start'))
        self.assertContains(response, 'overflow-y: auto !important;')

    def test_all_locales_still_render_successfully(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = self.client.get(reverse('game_start'), {'lang': lang})
                self.assertEqual(response.status_code, 200)


class TileGameDifficultyProgressionTests(TestCase):
    """2026-09-06: PREMIUM (subscribers) / long-word (everyone else)
    questions used to start at Q5 - pushed back to Q6 so NORMAL/HARD gets
    a 3rd question (Q3=NORMAL, Q4=NORMAL, Q5=HARD) before the switch,
    instead of just Q3=NORMAL/Q4=HARD."""

    def setUp(self):
        # EASY pool: category in easy's filter, <=4 mora reading.
        ThankJapanModel.objects.create(
            name='progress-easy-1', englishname='Easy1', jpname='いぬ',
            category='animal', slug='progress-easy-1', description='d', history='h')
        ThankJapanModel.objects.create(
            name='progress-easy-2', englishname='Easy2', jpname='ねこ',
            category='animal', slug='progress-easy-2', description='d', history='h')
        # NORMAL pool: category in normal's filter, name <=9 chars.
        ThankJapanModel.objects.create(
            name='normal1', englishname='Normal1', jpname='のーまるいち',
            category='cook', slug='progress-normal-1', description='d', history='h')
        ThankJapanModel.objects.create(
            name='normal2', englishname='Normal2', jpname='のーまるに',
            category='cook', slug='progress-normal-2', description='d', history='h')
        # HARD pool: any category, name <=9 chars.
        ThankJapanModel.objects.create(
            name='hardone', englishname='Hard1', jpname='はーどいち',
            category='nature', slug='progress-hard-1', description='d', history='h')
        self.premium_word = ThankJapanPremium.objects.create(
            name='progress-premium-1', englishname='Premium1', jpname='ぷれみあむ',
            category='TourismEtiquette', slug='progress-premium-1', description='d', history='h')

        self.user = User.objects.create_user(username='progression_tester', password='pass12345')
        self.user.profile.is_premium = True
        self.user.profile.save()

    def test_premium_player_gets_five_free_questions_then_switches_at_six(self):
        self.client.login(username='progression_tester', password='pass12345')
        self.client.get(reverse('game_restart'))

        questions = self.client.session['game_question_ids']

        self.assertEqual(len(questions), 6)
        self.assertTrue(all(q['model'] == 'free' for q in questions[:5]), msg=questions)
        self.assertEqual(questions[5]['model'], 'premium')

    def test_non_premium_player_still_gets_five_tiered_free_questions_first(self):
        # Everyone (premium or not) gets the same Q1-5 EASY/NORMAL/HARD
        # progression - only what happens from Q6 onward differs.
        guest_user = User.objects.create_user(username='progression_guest', password='pass12345')
        self.client.login(username='progression_guest', password='pass12345')
        self.client.get(reverse('game_restart'))

        questions = self.client.session['game_question_ids']

        self.assertGreaterEqual(len(questions), 5)
        self.assertTrue(all(q['model'] == 'free' for q in questions[:5]), msg=questions)


class EasyPoolMoraFilterTests(TestCase):
    """2026-09-05: the EASY tier's category filter alone let through rare
    long words in the right category (e.g. chinpanjii/chimpanzee is
    'animal' but 6 mora) - Q1-2 of a brand new tile game showed a hard
    word. _sample_easy_ids must additionally cap mora count."""

    def setUp(self):
        ThankJapanModel.objects.create(
            name='inu', englishname='Dog', jpname='いぬ', kanji_name='犬', katakana_name='イヌ',
            category='animal', slug='dog-inu', description='d', history='h')
        ThankJapanModel.objects.create(
            name='chinpanjii', englishname='Chimpanzee', jpname='ちんぱんじー', kanji_name='チンパンジー',
            katakana_name='チンパンジー', category='animal', slug='chimp-chinpanjii', description='d', history='h')

    def test_long_word_excluded_from_easy_pool_even_in_right_category(self):
        for _ in range(20):
            ids = _sample_easy_ids(['animal'], count=2)
            names = set(ThankJapanModel.objects.filter(id__in=ids).values_list('name', flat=True))
            self.assertNotIn('chinpanjii', names)

    def test_short_word_still_eligible(self):
        ids = _sample_easy_ids(['animal'], count=2)
        names = set(ThankJapanModel.objects.filter(id__in=ids).values_list('name', flat=True))
        self.assertIn('inu', names)


class ChunkAnswerCachingTests(TestCase):
    """2026-09-05: a reported 'correctly ordered tiles marked wrong' bug.
    Root cause not conclusively reproduced, but game_submit_answer used to
    independently recompute build_tiles() from scratch rather than reusing
    the exact computation game_question_data had just served - any future
    drift between those two calls (a tile_split.py change deployed
    mid-game, or non-determinism introduced later) would silently break
    scoring. Now the answer is computed once and cached in the session at
    serve time; submit_answer must validate against that cached value."""

    def setUp(self):
        self.user = User.objects.create_user(username='chunk_answer_tester', password='pass12345')
        ThankJapanModel.objects.create(
            name='erekigitaa', englishname='Electric guitar', jpname='えれきぎたー',
            kanji_name='エレキギター', katakana_name='エレキギター', category='appliances',
            slug='electric-guitar-erekigitaa', description='d', history='h',
            example_sentence_ja='放課後にエレキギターの練習をします')

    def _start_single_word_game(self):
        self.client.login(username='chunk_answer_tester', password='pass12345')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'electric-guitar-erekigitaa',
        })
        self.client.get(reverse('game_play'))

    def test_correctly_ordered_chunks_are_accepted(self):
        self._start_single_word_game()
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertEqual(data['route'], 'chunk')
        correct_order = sorted(data['tiles'], key=lambda chunk: '放課後にエレキギターの練習をします'.index(chunk))

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)
        self.assertTrue(result['is_correct'], msg=f"tiles={data['tiles']} order_tried={correct_order} result={result}")

    def test_submit_uses_cached_answer_not_a_fresh_recompute(self):
        """Even if tile_split.py's output were to change between the two
        calls, submit_answer must still validate against what was actually
        shown, because it reuses the cached value rather than recomputing."""
        self._start_single_word_game()
        self.client.get(reverse('game_question_data'))
        session = self.client.session
        self.assertIn('current_answer_text', session)
        self.assertEqual(session['current_answer_text'], '放課後にエレキギターの練習をします')

        # simulate tile_split.py somehow producing a different answer if it
        # were recomputed now - the cached value must still win
        session['current_answer_text'] = '差し替えられた別解答'
        session.save()
        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': ['差し替えられた別解答'], 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)
        self.assertTrue(result['is_correct'])


class ChunkAnswerWithSourceSpacesTests(TestCase):
    """End-to-end reproduction of the 'correctly ordered tiles marked
    wrong' bug: example_sentence_ja here has literal spaces between words
    (a real pattern in production content), which fugashi/MeCab drops
    during tokenization. See tile_split.build_tiles for the fix."""

    def setUp(self):
        self.user = User.objects.create_user(username='space_bug_tester', password='pass12345')
        ThankJapanModel.objects.create(
            name='bansoukou', englishname='Adhesive Bandage', jpname='ばんそうこう',
            kanji_name='絆創膏', katakana_name='バンソウコウ', category='householditems',
            slug='adhesive-bandage-bansoukou', description='d', history='h',
            example_sentence_ja='指を 切ったので 絆創膏を 貼りました。')

    def test_correctly_reassembled_tiles_are_marked_correct(self):
        self.client.login(username='space_bug_tester', password='pass12345')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'adhesive-bandage-bansoukou',
        })
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertEqual(data['route'], 'chunk')

        full_sentence = '指を切ったので絆創膏を貼りました。'
        correct_order = sorted(data['tiles'], key=lambda chunk: full_sentence.index(chunk))

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)
        self.assertTrue(result['is_correct'], msg=f"tiles={data['tiles']} order_tried={correct_order} result={result}")


class ChunkAnswerAcrossSentenceBoundaryTests(TestCase):
    """2026-09-06: reported bug - a real premium word (ThankJapanPremium
    id=281, slug='niigataken', category=Prefectures) whose example_sentence_ja
    is two sentences joined by '。 ' (period + literal space) was suspected
    of scoring a correctly-ordered answer as wrong, by analogy with the
    エレキギター space-drop bug. End-to-end reproduction with the exact
    production content (verified against the live database) via mode=single,
    same as ChunkAnswerWithSourceSpacesTests above but for a two-sentence
    example instead of a single sentence with mid-sentence spaces."""

    def setUp(self):
        self.user = User.objects.create_user(username='niigata_tester', password='pass12345')
        self.user.profile.is_premium = True
        self.user.profile.save()
        ThankJapanPremium.objects.create(
            name='niigataken', englishname='Niigata Prefecture', jpname='にいがたけん',
            category='Prefectures', slug='niigataken', description='d', history='h',
            example_sentence_ja=(
                '<ruby>新潟県<rt>にいがたけん</rt></ruby>はお<ruby>米<rt>こめ</rt></ruby>と'
                'お<ruby>酒<rt>さけ</rt></ruby>がとてもおいしいです。 '
                '<ruby>冬<rt>ふゆ</rt></ruby>はスキーに<ruby>行<rt>い</rt></ruby>きたいですね。'
            ),
        )

    def test_correctly_reassembled_tiles_are_marked_correct(self):
        self.client.login(username='niigata_tester', password='pass12345')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'premium', 'slug': 'niigataken',
        })
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertEqual(data['route'], 'chunk')

        full_sentence = '新潟県はお米とお酒がとてもおいしいです冬はスキーに行きたいですね'
        correct_order = sorted(data['tiles'], key=lambda chunk: full_sentence.index(chunk))

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)
        self.assertTrue(result['is_correct'], msg=f"tiles={data['tiles']} order_tried={correct_order} result={result}")


class PremiumReviewAlwaysUsesExampleSentenceTests(TestCase):
    """2026-09-06: reviewing a premium word (mode=single) used to route to
    'mora' (single-word reading tiles, example sentence entirely ignored)
    for any premium word whose reading is <=5 mora - e.g. 'いわてけん'
    (Iwate Prefecture, 5 mora) - while a longer-reading premium word like
    'ほっかいどう' (Hokkaido, 6 mora) already got the richer example-
    sentence-based puzzle. Inconsistent for paid content, so mode=single
    now always forces the chunk/example-sentence route for
    ThankJapanPremium regardless of mora count. Free words and the main
    timed game (any difficulty other than 'single') are unaffected -
    see game_question_data/game_submit_answer's force_chunk wiring."""

    def setUp(self):
        self.user = User.objects.create_user(username='iwate_tester', password='pass12345')
        self.user.profile.is_premium = True
        self.user.profile.save()
        self.premium_word = ThankJapanPremium.objects.create(
            name='iwateken', englishname='Iwate Prefecture', jpname='いわてけん',
            category='Prefectures', slug='iwateken', description='d', history='h',
            example_sentence_ja=(
                '<ruby>岩手県<rt>いわてけん</rt></ruby>は<ruby>山<rt>やま</rt></ruby>が'
                '<ruby>多<rt>おお</rt></ruby>いです。'
            ),
        )
        self.free_word = ThankJapanModel.objects.create(
            name='tomato-review-test', englishname='Tomato', jpname='とまと',
            category='food', slug='tomato-review-test', description='d', history='h',
            example_sentence_ja='<ruby>使わない</ruby>',
        )

    def test_premium_single_mode_review_forces_chunk_route(self):
        self.client.login(username='iwate_tester', password='pass12345')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'premium', 'slug': 'iwateken',
        })

        data = json.loads(self.client.get(reverse('game_question_data')).content)

        self.assertEqual(data['route'], 'chunk')
        self.assertEqual(sorted(data['tiles']), sorted(['岩手県は', '山が', '多いです']))

    def test_premium_single_mode_review_scores_the_forced_chunk_answer_correctly(self):
        self.client.login(username='iwate_tester', password='pass12345')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'premium', 'slug': 'iwateken',
        })
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        full_sentence = '岩手県は山が多いです'
        correct_order = sorted(data['tiles'], key=lambda chunk: full_sentence.index(chunk))

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)

        self.assertTrue(result['is_correct'], msg=result)
        self.assertEqual(result['correct_answer'], full_sentence)

    def test_free_single_mode_review_still_uses_mora_route(self):
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': 'tomato-review-test',
        })

        data = json.loads(self.client.get(reverse('game_question_data')).content)

        self.assertEqual(data['route'], 'mora')
        self.assertEqual(sorted(data['tiles']), sorted(['と', 'ま', 'と']))

    def test_main_timed_game_premium_word_still_uses_mora_route(self):
        # Bypass the random question picker (_build_tile_game_questions) to
        # deterministically exercise this exact premium word through the
        # normal (non-single) game path.
        self.client.login(username='iwate_tester', password='pass12345')
        session = self.client.session
        session['game_question_ids'] = [{'id': self.premium_word.id, 'model': 'premium'}]
        session['game_current_index'] = 0
        session['game_score'] = 0
        session['game_difficulty'] = 'timeattack'
        session['game_history'] = []
        session.save()

        data = json.loads(self.client.get(reverse('game_question_data')).content)

        self.assertEqual(data['route'], 'mora')
        self.assertEqual(sorted(data['tiles']), sorted(['い', 'わ', 'て', 'け', 'ん']))


class TileGameFreePreviewPremiumInclusionTests(TestCase):
    """2026-09-11: DailyConversation/slang/TourismEtiquette/Entertainment
    are completely free to browse (see game_play's mode=single handling and
    ImgPremiumDetailView.get_object, neither of which gates them behind
    is_premium), but the main timed game's non-premium Q6+ pool used to
    pull only from ThankJapanModel - these categories were reachable only
    via each word's own detail page / single-mode review, never from the
    main game, unless the player was a paying subscriber (whose Q6+ pool
    was always unfiltered ThankJapanPremium and so already included them).
    Now mixed into the non-premium pool too, scored the same 2pt as any
    other premium-model question (explicit product decision - not
    special-cased to 1pt despite being free content)."""

    def setUp(self):
        ThankJapanModel.objects.create(
            name='preview-easy-1', englishname='Easy1', jpname='いぬ',
            category='animal', slug='preview-easy-1', description='d', history='h')
        ThankJapanModel.objects.create(
            name='preview-easy-2', englishname='Easy2', jpname='ねこ',
            category='animal', slug='preview-easy-2', description='d', history='h')
        ThankJapanModel.objects.create(
            name='normal1', englishname='Normal1', jpname='のーまるいち',
            category='cook', slug='preview-normal-1', description='d', history='h')
        ThankJapanModel.objects.create(
            name='normal2', englishname='Normal2', jpname='のーまるに',
            category='cook', slug='preview-normal-2', description='d', history='h')
        ThankJapanModel.objects.create(
            name='hardone', englishname='Hard1', jpname='はーどいち',
            category='nature', slug='preview-hard-1', description='d', history='h')
        self.slang_word = ThankJapanPremium.objects.create(
            name='preview-slang-1', englishname='Slang1', jpname='がち',
            category='slang', slug='preview-slang-1', description='d', history='h')
        self.entertainment_word = ThankJapanPremium.objects.create(
            name='preview-ent-1', englishname='Ent1', jpname='えんため',
            category='Entertainment', slug='preview-ent-1', description='d', history='h')

    def test_free_preview_categories_appear_in_non_premium_main_game_pool(self):
        guest = User.objects.create_user(username='preview_guest', password='pass12345')
        self.client.login(username='preview_guest', password='pass12345')
        self.client.get(reverse('game_restart'))

        questions = self.client.session['game_question_ids']

        self.assertEqual(len(questions), 7, msg=questions)
        self.assertTrue(all(q['model'] == 'free' for q in questions[:5]), msg=questions)
        remaining_pairs = {(q['id'], q['model']) for q in questions[5:]}
        self.assertIn((self.slang_word.id, 'premium'), remaining_pairs)
        self.assertIn((self.entertainment_word.id, 'premium'), remaining_pairs)

    def test_free_preview_premium_question_still_scores_two_points(self):
        user = User.objects.create_user(username='preview_scorer', password='pass12345')
        self.client.login(username='preview_scorer', password='pass12345')
        session = self.client.session
        session['game_question_ids'] = [{'id': self.slang_word.id, 'model': 'premium'}]
        session['game_current_index'] = 0
        session['game_score'] = 0
        session['game_difficulty'] = 'timeattack'
        session['game_history'] = []
        session['game_combo'] = 0
        session['game_max_combo'] = 0
        session.save()

        data = json.loads(self.client.get(reverse('game_question_data')).content)
        full_reading = 'がち'
        correct_order = sorted(data['tiles'], key=lambda t: full_reading.index(t))

        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )
        result = json.loads(response.content)

        self.assertTrue(result['is_correct'], msg=result)
        self.assertEqual(result['points'], 2)


class DailyQuestionTileGameTests(TestCase):
    """2026-09-05: '今日の一問' converted from 4-choice to the same
    tile-rearrangement mechanic as the main game (mora/chunk routing,
    symbol exclusion, session-cached answer text)."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='test-daily-tile', englishname='Adhesive Bandage', jpname='ばんそうこう',
            kanji_name='絆創膏', katakana_name='バンソウコウ', category='householditems',
            slug='daily-tile-word', description='d', history='h',
            example_sentence_ja='指を 切ったので 絆創膏を、貼りました。',
        )
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def test_question_state_serves_tiles_not_image_choices(self):
        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'question')
        self.assertNotIn('dq-choice-btn', body)
        self.assertIn('dq-tile-bank', body)
        tiles = json.loads(response.context['tiles_json'])
        self.assertTrue(tiles)
        # symbol-exclusion + space-drop fixes must both apply here too
        self.assertTrue(all(' ' not in t and '、' not in t and '。' not in t for t in tiles))

    def test_correctly_ordered_tiles_marked_correct_and_scored(self):
        user = User.objects.create_user(username='daily_tile_tester', password='pass12345')
        self.client.login(username='daily_tile_tester', password='pass12345')

        get_response = self.client.get(reverse('daily_question'))
        tiles = json.loads(get_response.context['tiles_json'])
        full_answer = '指を切ったので絆創膏を貼りました'
        correct_order = sorted(tiles, key=lambda chunk: full_answer.index(chunk))

        post_response = self.client.post(reverse('daily_question'), {
            'answer_tiles': json.dumps(correct_order),
        })
        self.assertEqual(post_response.context['daily_state'], 'result')
        self.assertTrue(post_response.context['is_correct'])

        user.profile.refresh_from_db()
        self.assertEqual(user.profile.total_score, 1)

    def test_incorrect_order_marked_incorrect(self):
        get_response = self.client.get(reverse('daily_question'))
        tiles = json.loads(get_response.context['tiles_json'])
        scrambled = list(reversed(tiles)) if len(tiles) > 1 else tiles

        full_answer = '指を切ったので絆創膏を貼りました'
        if scrambled == sorted(tiles, key=lambda chunk: full_answer.index(chunk)):
            self.skipTest('reversed order happened to already be correct')

        post_response = self.client.post(reverse('daily_question'), {
            'answer_tiles': json.dumps(scrambled),
        })
        self.assertFalse(post_response.context['is_correct'])

    def test_already_answered_gate_still_applies(self):
        get_response = self.client.get(reverse('daily_question'))
        tiles = json.loads(get_response.context['tiles_json'])
        self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(tiles)})

        second_get = self.client.get(reverse('daily_question'))
        self.assertEqual(second_get.context['daily_state'], 'already_answered')

    def test_already_answered_gate_applies_across_language_switch(self):
        # Answer in one lang, then GET again in a different lang with the
        # SAME client/session - must still gate.
        get_response = self.client.get(reverse('daily_question'), {'lang': 'vi'})
        tiles = json.loads(get_response.context['tiles_json'])
        self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(tiles)}, QUERY_STRING='lang=vi')

        second_get = self.client.get(reverse('daily_question'), {'lang': 'th'})
        self.assertEqual(second_get.context['daily_state'], 'already_answered')

    def test_logged_in_user_gate_survives_a_fresh_session(self):
        # 2026-09-17 bug: a logged-in user answered in Vietnamese, closed the
        # site, then reopened in Thai and could answer again. The session
        # itself wasn't necessarily lost - on iOS, each distinct "Add to
        # Home Screen" install gets its own isolated cookie jar for the same
        # origin, so a language switch that lands on a different entry point
        # can genuinely start a brand-new session even while logged in.
        # Simulated here with a fresh test Client (no shared cookies) that
        # logs in as the SAME user - the DB-backed gate must still hold
        # because it's keyed on the user, not the session.
        user = User.objects.create_user(username='daily_multisession', password='pass12345')
        self.client.login(username='daily_multisession', password='pass12345')
        get_response = self.client.get(reverse('daily_question'), {'lang': 'vi'})
        tiles = json.loads(get_response.context['tiles_json'])
        self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(tiles)}, QUERY_STRING='lang=vi')

        fresh_client = Client()
        fresh_client.login(username='daily_multisession', password='pass12345')
        second_get = fresh_client.get(reverse('daily_question'), {'lang': 'th'})
        self.assertEqual(second_get.context['daily_state'], 'already_answered')


class DailyQuestionImageSafetyNetTests(TestCase):
    """2026-09-11: a batch of newly-added words was found with a real,
    working image, but the incident prompted adding a safety net anyway -
    DailyQuestionManager's candidate pool now excludes any word whose
    `image` field is blank, so a future word added without an image (e.g.
    via a bulk-import path that bypasses ModelForm validation) can never be
    picked as '今日の一問'."""

    def test_word_with_blank_image_is_excluded_from_candidate_pool(self):
        ThankJapanModel.objects.create(
            name='blankimg1', englishname='No Image Word', jpname='がぞうなし',
            category='food', slug='blank-image-word', description='d', history='h',
        )
        today = timezone.localdate()

        dq, created = DailyQuestion.objects.get_or_create_for_date(today)

        self.assertIsNone(dq)
        self.assertFalse(created)

    def test_word_with_image_is_still_selected_alongside_a_blank_image_word(self):
        ThankJapanModel.objects.create(
            name='blankimg2', englishname='No Image Word', jpname='がぞうなし',
            category='food', slug='blank-image-word-2', description='d', history='h',
        )
        good_word = ThankJapanModel.objects.create(
            name='hasimage1', englishname='Has Image Word', jpname='がぞうあり',
            category='food', slug='has-image-word', description='d', history='h',
            image='thankjapan/images/dummy_test_asset',
        )
        today = timezone.localdate()

        dq, created = DailyQuestion.objects.get_or_create_for_date(today)

        self.assertTrue(created)
        self.assertEqual(dq.word_id, good_word.id)


class DailyQuestionFreePreviewPremiumInclusionTests(TestCase):
    """2026-09-11: DailyConversation/slang/TourismEtiquette/Entertainment are
    completely free to browse, but 今日の一問 could never select them -
    DailyQuestion.word was a ForeignKey scoped specifically to
    ThankJapanModel, so a ThankJapanPremium row couldn't be referenced at
    all regardless of category filters. Fixed by widening `word` to
    nullable and adding a parallel nullable `premium_word` FK, unified via
    the `selected_word` property (see thank_japan_app/models.py)."""

    def test_selected_word_resolves_to_word_when_set(self):
        free_word = ThankJapanModel.objects.create(
            name='selresolve-free', englishname='Free', jpname='ふりー',
            category='food', slug='selresolve-free', description='d', history='h')
        dq = DailyQuestion.objects.create(date=timezone.localdate(), word=free_word)

        self.assertEqual(dq.selected_word, free_word)
        self.assertIn('Free', str(dq))

    def test_selected_word_resolves_to_premium_word_when_set(self):
        premium_word = ThankJapanPremium.objects.create(
            name='selresolve-premium', englishname='Premium', jpname='ぷれみあむ',
            category='slang', slug='selresolve-premium', description='d', history='h')
        dq = DailyQuestion.objects.create(date=timezone.localdate(), premium_word=premium_word)

        self.assertEqual(dq.selected_word, premium_word)
        self.assertIn('Premium', str(dq))

    def test_only_free_preview_premium_candidate_available_gets_saved_to_premium_word_field(self):
        # name must be <=9 chars to pass NORMAL_LENGTH_REGEX (it filters on
        # `name`, not `jpname` - see the memory note on this exact pitfall).
        premium_word = ThankJapanPremium.objects.create(
            name='onlycand1', englishname='OnlyCandidate', jpname='おんりー',
            category='slang', slug='onlycand-premium', description='d', history='h',
            image='thankjapan/premium/dummy_test_asset')
        today = timezone.localdate()

        dq, created = DailyQuestion.objects.get_or_create_for_date(today)

        self.assertTrue(created)
        self.assertIsNone(dq.word)
        self.assertEqual(dq.premium_word_id, premium_word.id)
        self.assertEqual(dq.selected_word, premium_word)

    def test_recent_exclusion_tracks_each_table_separately(self):
        # A premium word used yesterday must not repeat today, even though
        # it's a totally different table from the free word pool - the two
        # recency lists (word_id / premium_word_id) must not cross-
        # contaminate each other, and only the free word should remain a
        # valid candidate today.
        premium_word = ThankJapanPremium.objects.create(
            name='recentpre', englishname='RecentPremium', jpname='さいきん',
            category='slang', slug='recenttest-premium', description='d', history='h',
            image='thankjapan/premium/dummy_test_asset')
        free_word = ThankJapanModel.objects.create(
            name='recentfre', englishname='RecentFree', jpname='さいきんに',
            category='food', slug='recenttest-free', description='d', history='h',
            image='thankjapan/images/dummy_test_asset')
        yesterday = timezone.localdate() - timedelta(days=1)
        DailyQuestion.objects.create(date=yesterday, premium_word=premium_word)

        today = timezone.localdate()
        dq, created = DailyQuestion.objects.get_or_create_for_date(today)

        self.assertTrue(created)
        self.assertEqual(dq.selected_word, free_word)

    def test_daily_question_page_renders_correctly_when_todays_word_is_a_free_preview_premium_word(self):
        premium_word = ThankJapanPremium.objects.create(
            name='renderpremium1', englishname='Render Premium', jpname='がち',
            category='slang', slug='renderpremium1', description='d', history='h',
            image='thankjapan/premium/dummy_test_asset')
        premium_word.refresh_from_db()  # image needs a DB round-trip to become a usable CloudinaryResource
        DailyQuestion.objects.create(date=timezone.localdate(), premium_word=premium_word)

        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['daily_state'], 'question')
        tiles = json.loads(response.context['tiles_json'])
        self.assertEqual(sorted(tiles), sorted(['が', 'ち']))
        # The image safety net (.exclude(image='')) only stops an
        # image-less word from being selected in the first place - this
        # separately confirms that once a premium_word IS selected, its
        # image actually renders in the page (og:image + the on-page
        # prompt image), not just in the JSON tile data.
        self.assertIn(premium_word.image.url, body)
        self.assertEqual(
            body.count(premium_word.image.url), 3,
            msg='expected twitter:image + og:image meta tags + the on-page prompt image',
        )


class DailyQuestionShareAfterTileConversionTests(TestCase):
    """2026-09-12: X share text redesigned around a word-centric 'assembly
    process' reveal (す→し→寿司(正解)🀄 for mora-route words, an 'N steps'
    summary for chunk-route ones) rather than the old 'I got it right/wrong'
    framing. The same text is used regardless of is_correct - see
    _daily_question_share_text - so these tests answer both ways and expect
    an identical share body each time."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='test-daily-share', englishname='Guitar', jpname='ぎたー',
            kanji_name='ギター', katakana_name='ギター', category='appliances',
            slug='daily-share-word', description='d', history='h',
        )
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def _answer(self, correct, client=None):
        # tiles_json is shuffled display order, not the solved order - sort
        # by position in the word's own reading to get the actual answer.
        client = client or self.client
        tiles = json.loads(client.get(reverse('daily_question')).context['tiles_json'])
        correct_order = sorted(tiles, key=lambda t: self.word.jpname.index(t))
        submitted = correct_order if correct else list(reversed(correct_order))
        return client.post(reverse('daily_question'), {'answer_tiles': json.dumps(submitted)})

    def test_mora_route_assembly_process_is_identical_for_correct_and_incorrect(self):
        # ぎたー splits into 3 mora tiles (ぎ/た/ー); kanji_name 'ギター' is
        # the final reveal after the arrows.
        expected_process = 'ぎ→た→ー→ギター(Answer)🀄'
        for correct in (True, False):
            with self.subTest(correct=correct):
                response = self._answer(correct=correct, client=Client())
                body = response.content.decode()
                self.assertEqual(response.context['is_correct'], correct)
                self.assertIn(urlquote(expected_process, safe=''), body, msg=body)

    def test_share_hashtags_include_word_specific_tag(self):
        response = self._answer(correct=True)
        body = response.content.decode()
        # _daily_question_hashtags appends the word's englishname (spaces
        # stripped) as a dynamic hashtag on top of the fixed daily-question
        # tags - confirms that per-word dynamic portion still comes through.
        self.assertIn(urlquote('ThankJapan,DailyQuestion,Guitar', safe=''), body)

    def test_share_intent_url_and_og_image_are_well_formed(self):
        response = self._answer(correct=True)
        body = response.content.decode()

        self.assertIn('https://x.com/intent/tweet?text=', body)
        self.assertIn('&url=', body)
        self.assertIn('&hashtags=', body)
        self.assertIn('handleShareClick(event, this.href,', body)

        # No image uploaded in this fixture, so OGP must fall back to the
        # shared default rather than break/omit the tag entirely.
        self.assertIn('<meta property="og:image" content="https://res.cloudinary.com', body)

    def test_share_url_is_the_words_own_detail_page_not_daily(self):
        # 2026-09-12: the share link now points at the word's permanent
        # detail page, not the dated /daily/ URL - an old tweet's card would
        # otherwise keep showing this word/image forever (X's per-URL OGP
        # cache) while the link led to whatever word is live *today*. The
        # /daily/ page's own og:url (unrelated to the share button) still
        # uses the dated daily_url, confirmed by the second assertion below.
        response = self._answer(correct=True)
        body = response.content.decode()

        expected_share_url = (
            f"https://www.thankjapan.com{reverse('category_detail', kwargs={'category': 'appliances', 'slug': 'daily-share-word'})}"
            f"?lang=en&scb={self.today.isoformat()}"
        )
        self.assertIn(f'url={urlquote(expected_share_url, safe="/")}', body, msg=body)

        # Rendered HTML entity-escapes the '&' in the query string.
        expected_daily_url = f"https://www.thankjapan.com/daily/?lang=en&amp;date={self.today.isoformat()}"
        self.assertIn(f'<meta property="og:url" content="{expected_daily_url}"', body, msg=body)


class DailyQuestionChunkRouteShareTests(TestCase):
    """2026-09-12: a chunk-route word (long premium-style reading, >5 mora)
    would make the share text balloon if every chunk were spelled out
    arrow-by-arrow, so it collapses to an 'N steps to complete' summary
    instead - this pins down that the step count matches the real number of
    chunks build_tiles produces, and the full phrase is NOT dumped into the
    tweet text."""

    def setUp(self):
        self.today = timezone.localdate()
        self.example_sentence = (
            '<ruby>鹿児島県<rt>かごしまけん</rt></ruby>は<ruby>桜島<rt>さくらじま</rt></ruby>が'
            '<ruby>有名<rt>ゆうめい</rt></ruby>です。'
        )
        self.word = ThankJapanModel.objects.create(
            name='test-daily-chunk', englishname='Kagoshima', jpname='かごしまけん',
            category='live', slug='daily-chunk-word', description='d', history='h',
            example_sentence_ja=self.example_sentence,
        )
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def test_chunk_route_share_text_uses_step_count_not_full_phrase(self):
        tile_data = tile_split.build_tiles(self.word.jpname, self.word.example_sentence_ja)
        self.assertEqual(tile_data['route'], 'chunk', msg='fixture must exercise the chunk route')
        expected_step_line = f"Completed in {len(tile_data['tiles'])} steps!(Answer)🀄"

        tiles = json.loads(self.client.get(reverse('daily_question')).context['tiles_json'])
        correct_order = sorted(tiles, key=lambda t: tile_data['answer_text'].index(t))
        response = self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(correct_order)})
        body = response.content.decode()

        self.assertIn(urlquote(expected_step_line, safe=''), body, msg=body)
        self.assertNotIn(urlquote(tile_data['answer_text'], safe=''), body)


class DailyQuestionShareLinksToWordDetailPageTests(TestCase):
    """2026-09-12: the X share button's link (and therefore the card
    image's click-through target, since that comes from whatever page X
    crawls at that link's own og:url) now points at the word's own
    permanent detail page instead of /daily/. Covers both tables that
    今日の一問 can now select from - a free ThankJapanModel word
    (category_detail) and a free-preview ThankJapanPremium word
    (detail_premium) - since the URL pattern differs by which table the
    word lives in, not by its mora/chunk tile route."""

    def test_free_thankjapanmodel_word_links_to_category_detail(self):
        today = timezone.localdate()
        word = ThankJapanModel.objects.create(
            name='sharelinkfree', englishname='ShareLinkFree', jpname='しぇあ',
            category='food', slug='sharelink-free-word', description='d', history='h')
        DailyQuestion.objects.create(date=today, word=word)

        tiles = json.loads(self.client.get(reverse('daily_question')).context['tiles_json'])
        correct_order = sorted(tiles, key=lambda t: word.jpname.index(t))
        response = self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(correct_order)})
        body = response.content.decode()

        expected_url = (
            f"https://www.thankjapan.com{reverse('category_detail', kwargs={'category': 'food', 'slug': 'sharelink-free-word'})}"
            f"?lang=en"
        )
        self.assertIn(f'url={urlquote(expected_url, safe="/")}', body, msg=body)

    def test_premium_thankjapanpremium_word_links_to_detail_premium(self):
        today = timezone.localdate()
        word = ThankJapanPremium.objects.create(
            name='sharelinkpr', englishname='ShareLinkPremium', jpname='しぇありんくぷれみあむてすと',
            category='slang', slug='sharelink-premium-word', description='d', history='h',
            example_sentence_ja='<ruby>共有<rt>きょうゆう</rt></ruby>リンクのテストです。',
        )
        DailyQuestion.objects.create(date=today, premium_word=word)

        tile_data = tile_split.build_tiles(word.jpname, word.example_sentence_ja)
        self.assertEqual(tile_data['route'], 'chunk', msg='fixture must exercise the chunk route')
        tiles = json.loads(self.client.get(reverse('daily_question')).context['tiles_json'])
        correct_order = sorted(tiles, key=lambda t: tile_data['answer_text'].index(t))
        response = self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(correct_order)})
        body = response.content.decode()

        expected_url = (
            f"https://www.thankjapan.com{reverse('detail_premium', kwargs={'category': 'slang', 'slug': 'sharelink-premium-word'})}"
            f"?lang=en"
        )
        self.assertIn(f'url={urlquote(expected_url, safe="/")}', body, msg=body)


class DailyActivitySummaryTests(TestCase):
    """2026-09-12: DailyActivitySummary is the ground-truth usage counter
    introduced after a GA4-reported "2,295 sessions from Singapore, 90%
    bounce" anomaly turned out to have no corresponding real registered-user
    growth. Replaces the short-lived GameActivityLog (one row per event) -
    that design was judged an unbounded-growth/privacy risk before it ever
    left QA, so this instead keeps one running-total row per (date,
    activity_type), incremented via record() only from inside
    daily_question_view's POST handler and game_submit_answer, i.e. only
    once a real tile submission has been received and judged, never on a
    bare page view."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='activitylog1', englishname='ActivityLog',
            jpname='あいう', category='food', slug='activitylog-word',
            description='d', history='h',
        )

    def _daily_answer(self, correct, client=None):
        client = client or self.client
        tiles = json.loads(client.get(reverse('daily_question')).context['tiles_json'])
        correct_order = sorted(tiles, key=lambda t: self.word.jpname.index(t))
        submitted = correct_order if correct else list(reversed(correct_order))
        return client.post(reverse('daily_question'), {'answer_tiles': json.dumps(submitted)})

    def test_daily_question_correct_guest_answer_increments_summary_row(self):
        DailyQuestion.objects.create(date=self.today, word=self.word)

        self._daily_answer(correct=True)

        row = DailyActivitySummary.objects.get()
        self.assertEqual(row.date, self.today)
        self.assertEqual(row.activity_type, DailyActivitySummary.ACTIVITY_DAILY_QUESTION)
        self.assertEqual(row.total_count, 1)
        self.assertEqual(row.correct_count, 1)
        self.assertEqual(row.guest_count, 1)
        self.assertEqual(row.member_count, 0)

    def test_daily_question_incorrect_member_answer_increments_summary_row(self):
        User.objects.create_user(username='activitylog_member', password='pass12345')
        self.client.login(username='activitylog_member', password='pass12345')
        DailyQuestion.objects.create(date=self.today, word=self.word)

        self._daily_answer(correct=False)

        row = DailyActivitySummary.objects.get()
        self.assertEqual(row.activity_type, DailyActivitySummary.ACTIVITY_DAILY_QUESTION)
        self.assertEqual(row.total_count, 1)
        self.assertEqual(row.correct_count, 0)
        self.assertEqual(row.guest_count, 0)
        self.assertEqual(row.member_count, 1)

    def test_multiple_daily_question_answers_the_same_day_keep_accumulating(self):
        # Three independent guest sessions answering the same day's word -
        # simulates real concurrent usage, not just "does it fire once".
        DailyQuestion.objects.create(date=self.today, word=self.word)

        self._daily_answer(correct=True, client=Client())
        self._daily_answer(correct=False, client=Client())
        self._daily_answer(correct=True, client=Client())

        row = DailyActivitySummary.objects.get()
        self.assertEqual(row.total_count, 3)
        self.assertEqual(row.correct_count, 2)
        self.assertEqual(row.guest_count, 3)

    def test_daily_question_already_answered_replay_does_not_increment_again(self):
        DailyQuestion.objects.create(date=self.today, word=self.word)
        self._daily_answer(correct=True)
        self.assertEqual(DailyActivitySummary.objects.get().total_count, 1)

        # Same client, same day - hits the already_answered gate, which
        # returns before record() runs.
        response = self.client.post(reverse('daily_question'), {'answer_tiles': '[]'})
        self.assertEqual(response.context['daily_state'], 'already_answered')
        self.assertEqual(DailyActivitySummary.objects.get().total_count, 1)

    def _submit_main_game(self, difficulty, client=None):
        client = client or self.client
        session = client.session
        session['game_question_ids'] = [{'id': self.word.id, 'model': 'free'}]
        session['game_current_index'] = 0
        session['game_score'] = 0
        session['game_difficulty'] = difficulty
        session['game_history'] = []
        session['game_combo'] = 0
        session['game_max_combo'] = 0
        session.save()

        data = json.loads(client.get(reverse('game_question_data')).content)
        correct_order = sorted(data['tiles'], key=lambda t: self.word.jpname.index(t))
        return client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': correct_order, 'seconds_left': 60}),
            content_type='application/json',
        )

    def test_timeattack_submission_increments_main_game_summary_row(self):
        self._submit_main_game('timeattack')

        row = DailyActivitySummary.objects.get()
        self.assertEqual(row.activity_type, DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual(row.total_count, 1)
        self.assertEqual(row.correct_count, 1)
        self.assertEqual(row.guest_count, 1)

    def test_first_taste_submission_increments_main_game_summary_row(self):
        self._submit_main_game('first_taste')

        row = DailyActivitySummary.objects.get()
        self.assertEqual(row.activity_type, DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual(row.total_count, 1)

    def test_single_mode_review_does_not_create_a_summary_row(self):
        self._submit_main_game('single')

        self.assertEqual(DailyActivitySummary.objects.count(), 0)

    def test_resubmitting_the_same_question_index_does_not_increment_again(self):
        self._submit_main_game('timeattack')
        self.assertEqual(DailyActivitySummary.objects.get().total_count, 1)

        # Same session, same (now-already-answered) index 0 - game_submit_answer
        # returns the cached result before record() runs.
        response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': ['x'], 'seconds_left': 60}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DailyActivitySummary.objects.get().total_count, 1)

    def test_multiple_main_game_submissions_across_sessions_keep_accumulating(self):
        self._submit_main_game('timeattack', client=Client())
        self._submit_main_game('timeattack', client=Client())
        self._submit_main_game('first_taste', client=Client())

        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual(row.total_count, 3)

    def test_daily_question_and_main_game_get_separate_rows(self):
        DailyQuestion.objects.create(date=self.today, word=self.word)
        self._daily_answer(correct=True)
        self._submit_main_game('timeattack', client=Client())

        self.assertEqual(DailyActivitySummary.objects.count(), 2)
        daily_row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_DAILY_QUESTION)
        game_row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual(daily_row.total_count, 1)
        self.assertEqual(game_row.total_count, 1)


class RenewalMultiLanguageAuditTests(TestCase):
    """2026-09-05: after the tile-rearrangement renewal (mora/chunk split,
    symbol exclusion, 60s timer, combo threshold, daily_question/first_taste
    conversion), confirm every one of the 15 supported locales actually
    renders its own translation - not a silent fallback to English - for
    every piece of user-facing text that changed or was added along the
    way. This is the same bug class as the earlier Korean-shows-English
    report, just re-run against everything touched since then."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='test-daily-locale', englishname='Umbrella', jpname='かさ',
            kanji_name='傘', katakana_name='カサ', category='dailyactions',
            slug='daily-locale-word', description='d', history='h',
        )
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def test_daily_question_caption_renders_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                response = client.get(reverse('daily_question'), {'lang': lang})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['daily_state'], 'question')
                self.assertIn(DAILY_CAPTION_BY_LOCALE[lang], response.content.decode())

    def test_daily_question_share_text_and_hashtags_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                url = f"{reverse('daily_question')}?lang={lang}"
                tiles = json.loads(client.get(url).context['tiles_json'])
                correct_order = sorted(tiles, key=lambda t: self.word.jpname.index(t))

                response = client.post(url, {'answer_tiles': json.dumps(correct_order)})
                self.assertTrue(response.context['is_correct'], msg=f"lang={lang}")
                body = response.content.decode()

                # Umbrella/かさ is a 2-mora word with kanji_name='傘' - the
                # mora-route assembly process is か→さ→傘 for every locale
                # (the process itself isn't translated, only the surrounding
                # text is).
                share_key = DAILY_QUESTION_SHARE_LOCALE_ALIAS.get(lang, lang)
                heading = DAILY_QUESTION_SHARE_HEADING.get(share_key, DAILY_QUESTION_SHARE_HEADING['en'])
                invite = DAILY_QUESTION_SHARE_INVITE.get(share_key, DAILY_QUESTION_SHARE_INVITE['en'])
                cta = DAILY_QUESTION_SHARE_CTA.get(share_key, DAILY_QUESTION_SHARE_CTA['en'])
                answer_label = DAILY_QUESTION_SHARE_ANSWER_LABEL.get(share_key, DAILY_QUESTION_SHARE_ANSWER_LABEL['en'])
                expected_text = f"{heading}\n\nか→さ→傘({answer_label})🀄\n\n{invite}\n\n{cta}"
                self.assertIn(urlquote(expected_text, safe=''), body, msg=f"lang={lang}")

                term = DAILY_QUESTION_HASHTAG_TERM.get(share_key, DAILY_QUESTION_HASHTAG_TERM['en'])
                expected_hashtags = f"ThankJapan,{term},Umbrella"
                self.assertIn(urlquote(expected_hashtags, safe=''), body, msg=f"lang={lang}")

    def test_game_result_no_time_limit_label_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                session = client.session
                session['game_history'] = [{
                    'index': 0, 'is_correct': True, 'points': 1, 'question_id': 999999,
                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                }]
                session['game_difficulty'] = 'first_taste'
                session.save()

                response = client.get(reverse('game_result'), {'lang': lang})
                self.assertIn(NO_TIME_LIMIT_BY_LOCALE[lang], response.content.decode(), msg=f"lang={lang}")

    def test_game_result_60s_challenge_label_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                session = client.session
                session['game_history'] = [{
                    'index': 0, 'is_correct': True, 'points': 1, 'question_id': 999999,
                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                }]
                session['game_difficulty'] = 'timeattack'
                session.save()

                response = client.get(reverse('game_result'), {'lang': lang})
                self.assertIn(SIXTY_SEC_CHALLENGE_BY_LOCALE[lang], response.content.decode(), msg=f"lang={lang}")

    def test_first_taste_flow_renders_without_error_per_locale(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'locale-easy-{i}', englishname=f'Easy {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'locale-easy-{i}', description='d', history='h')

        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.get(reverse('game_restart'), {'difficulty': 'first_taste', 'lang': lang})
                response = client.get(reverse('game_play'), {'lang': lang})
                self.assertEqual(response.status_code, 200, msg=f"lang={lang}")


class FirstTasteTileGameTests(TestCase):
    """2026-09-05: the guest first-visit 3-question trial converted from
    4-choice to tile-rearrangement, restricted to EASY-tier short words,
    untimed (same treatment as mode=single practice)."""

    def setUp(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'easy-word-{i}', englishname=f'Easy {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'easy-word-{i}', description='d', history='h',
            )

    def test_first_taste_builds_three_easy_questions_untimed(self):
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        session = self.client.session
        self.assertEqual(session['game_difficulty'], 'first_taste')
        self.assertEqual(len(session['game_question_ids']), FIRST_TASTE_QUESTION_COUNT)
        self.assertTrue(all(q['model'] == 'free' for q in session['game_question_ids']))

        play_response = self.client.get(reverse('game_play'))
        self.assertFalse(play_response.context['has_timer'])
        self.assertTrue(play_response.context['skip_countdown'])

    def test_first_taste_question_data_has_no_countdown_clock(self):
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNone(data['seconds_left'])

    def test_first_taste_sets_returning_visitor_cookie(self):
        response = self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.assertIn('tj_has_played', response.cookies)

    def test_first_question_includes_a_valid_tutorial_order(self):
        # 2026-09-17: the first-taste tutorial points a finger at each tile
        # in solve order - tutorial_order must be a permutation of the
        # shuffled tiles' indices that reconstructs the answer when applied.
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)

        tutorial_order = data['tutorial_order']
        self.assertIsNotNone(tutorial_order)
        self.assertEqual(sorted(tutorial_order), list(range(len(data['tiles']))))
        reconstructed = ''.join(data['tiles'][i] for i in tutorial_order)
        self.assertEqual(reconstructed, 'いぬ')

    def test_second_question_has_no_tutorial_order(self):
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.client.get(reverse('game_play'))
        tiles = json.loads(self.client.get(reverse('game_question_data')).content)['tiles']  # question 1
        self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': tiles, 'seconds_left': 60}),
            content_type='application/json',
        )

        data = json.loads(self.client.get(reverse('game_question_data')).content)  # question 2
        self.assertIsNone(data.get('tutorial_order'))

    def test_normal_difficulty_never_gets_a_tutorial_order(self):
        ThankJapanModel.objects.create(
            name='timeattack-word', englishname='Cat', jpname='ねこ',
            kanji_name='猫', katakana_name='ネコ', category='animal',
            slug='timeattack-word', description='d', history='h',
        )
        self.client.get(reverse('game_restart'), {'difficulty': 'timeattack'})
        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNone(data.get('tutorial_order'))


EXTEND_TIME_TEXT_BY_LOCALE = {
    'ja': {
        'title': 'タイムアップ！',
        'extend_question': 'ポイントを使って10秒伸ばしますか？それとも終了しますか？',
        'btn_yes_extend': 'はい、延長する (10pt)',
        'btn_no_end': 'いいえ、終了する',
        'not_enough': 'あと__N__ptで10秒延長できます',
        'btn_end_only': '終了する',
        'guest_desc': '会員登録するとポイントを貯めて10秒延長できるようになります！',
        'btn_register': '会員登録する',
    },
    'zh-hant': {
        'title': '時間到！',
        'extend_question': '要使用點數延長10秒，還是要結束遊戲？',
        'btn_yes_extend': '是，延長 (10點)',
        'btn_no_end': '否，結束遊戲',
        'not_enough': '再存__N__點即可延長10秒',
        'btn_end_only': '結束遊戲',
        'guest_desc': '註冊會員即可累積點數，延長10秒遊戲時間！',
        'btn_register': '立即註冊',
    },
    'zh-cn': {
        'title': '时间到！',
        'extend_question': '要使用积分延长10秒，还是结束游戏？',
        'btn_yes_extend': '是，延长 (10积分)',
        'btn_no_end': '否，结束游戏',
        'not_enough': '再攒__N__积分即可延长10秒',
        'btn_end_only': '结束游戏',
        'guest_desc': '注册会员即可累积积分，延长10秒游戏时间！',
        'btn_register': '立即注册',
    },
    'ko': {
        'title': '시간 종료!',
        'extend_question': '포인트를 사용해서 10초 연장하시겠어요, 아니면 종료하시겠어요?',
        'btn_yes_extend': '예, 연장하기 (10pt)',
        'btn_no_end': '아니요, 종료하기',
        'not_enough': '__N__pt만 더 모으면 10초 연장할 수 있어요',
        'btn_end_only': '종료하기',
        'guest_desc': '회원가입하면 포인트를 모아 10초 연장할 수 있어요!',
        'btn_register': '회원가입하기',
    },
    'de': {
        'title': 'Zeit abgelaufen!',
        'extend_question': 'Möchtest du mit Punkten 10 Sekunden verlängern oder das Spiel beenden?',
        'btn_yes_extend': 'Ja, verlängern (10 Punkte)',
        'btn_no_end': 'Nein, beenden',
        'not_enough': 'Noch __N__ Punkte, dann kannst du 10 Sekunden verlängern',
        'btn_end_only': 'Beenden',
        'guest_desc': 'Registriere dich, um Punkte zu sammeln und 10 Sekunden zu verlängern!',
        'btn_register': 'Registrieren',
    },
    'fr': {
        'title': 'Temps écoulé !',
        'extend_question': 'Voulez-vous utiliser des points pour ajouter 10 secondes, ou terminer la partie ?',
        'btn_yes_extend': 'Oui, prolonger (10 pts)',
        'btn_no_end': 'Non, terminer',
        'not_enough': 'Encore __N__ points pour prolonger de 10 secondes',
        'btn_end_only': 'Terminer',
        'guest_desc': 'Inscrivez-vous pour cumuler des points et prolonger de 10 secondes !',
        'btn_register': "S'inscrire",
    },
    'es-es': {
        'title': '¡Se acabó el tiempo!',
        'extend_question': '¿Quieres usar puntos para añadir 10 segundos o terminar la partida?',
        'btn_yes_extend': 'Sí, ampliar (10 pts)',
        'btn_no_end': 'No, terminar',
        'not_enough': 'Consigue __N__ puntos más para ampliar 10 segundos',
        'btn_end_only': 'Terminar',
        'guest_desc': '¡Regístrate para acumular puntos y ampliar 10 segundos!',
        'btn_register': 'Registrarse',
    },
    'it': {
        'title': 'Tempo scaduto!',
        'extend_question': 'Vuoi usare i punti per aggiungere 10 secondi o terminare la partita?',
        'btn_yes_extend': 'Sì, estendi (10 pt)',
        'btn_no_end': 'No, termina',
        'not_enough': 'Ti mancano __N__ punti per estendere di 10 secondi',
        'btn_end_only': 'Termina',
        'guest_desc': 'Registrati per accumulare punti ed estendere di 10 secondi!',
        'btn_register': 'Registrati',
    },
    'pt': {
        'title': 'Tempo esgotado!',
        'extend_question': 'Quer usar pontos para adicionar 10 segundos ou encerrar o jogo?',
        'btn_yes_extend': 'Sim, estender (10 pts)',
        'btn_no_end': 'Não, encerrar',
        'not_enough': 'Faltam __N__ pontos para estender 10 segundos',
        'btn_end_only': 'Encerrar',
        'guest_desc': 'Cadastre-se para acumular pontos e estender 10 segundos!',
        'btn_register': 'Cadastrar-se',
    },
    'vi': {
        'title': 'Hết giờ!',
        'extend_question': 'Bạn muốn dùng điểm để kéo dài thêm 10 giây, hay kết thúc trò chơi?',
        'btn_yes_extend': 'Có, kéo dài (10 điểm)',
        'btn_no_end': 'Không, kết thúc',
        'not_enough': 'Cần thêm __N__ điểm để kéo dài thêm 10 giây',
        'btn_end_only': 'Kết thúc',
        'guest_desc': 'Đăng ký để tích điểm và kéo dài thêm 10 giây!',
        'btn_register': 'Đăng ký',
    },
    'th': {
        'title': 'หมดเวลา!',
        'extend_question': 'ต้องการใช้แต้มเพื่อต่อเวลา 10 วินาที หรือจบเกมเลย?',
        'btn_yes_extend': 'ใช่ ต่อเวลา (10 แต้ม)',
        'btn_no_end': 'ไม่ จบเกม',
        'not_enough': 'อีก __N__ แต้มจะต่อเวลาได้ 10 วินาที',
        'btn_end_only': 'จบเกม',
        'guest_desc': 'สมัครสมาชิกเพื่อสะสมแต้มและต่อเวลาได้ 10 วินาที!',
        'btn_register': 'สมัครสมาชิก',
    },
    'en': {
        'title': "Time's Up!",
        'extend_question': 'Use points to add 10 seconds, or end the game?',
        'btn_yes_extend': 'Yes, extend (10pt)',
        'btn_no_end': 'No, end game',
        'not_enough': 'Earn __N__ more points to extend by 10 seconds',
        'btn_end_only': 'End Game',
        'guest_desc': 'Sign up to earn points and extend by 10 seconds!',
        'btn_register': 'Sign Up',
    },
}
EXTEND_TIME_TEXT_BY_LOCALE['es-mx'] = EXTEND_TIME_TEXT_BY_LOCALE['es-es']
EXTEND_TIME_TEXT_BY_LOCALE['pt-br'] = EXTEND_TIME_TEXT_BY_LOCALE['pt']
EXTEND_TIME_TEXT_BY_LOCALE['en-in'] = EXTEND_TIME_TEXT_BY_LOCALE['en']


class ExtendTimeAllLocalesTests(TestCase):
    """2026-09-05: the 'spend points to add 10 seconds' modal's 8 strings
    (title, extend question, not-enough-points, guest pitch, and the 4
    button labels) must actually render per-locale text, not silently fall
    back to English, in all 15 supported locales - the same bug class as
    the earlier Korean-shows-English report. All 8 strings are rendered
    into hidden <span id="i18n-..."> elements unconditionally regardless of
    which modal variant a given player will see (see ExtendTimeModalContextTests
    for the state-branching itself), so one render per locale covers all 8."""

    def setUp(self):
        ThankJapanModel.objects.create(
            name='extend-locale-word', englishname='Word', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='extend-locale-word', description='d', history='h')

    def test_all_extend_time_strings_render_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.get(reverse('game_restart'), {'lang': lang})
                response = client.get(reverse('game_play'), {'lang': lang})
                body = response.content.decode()
                expected = EXTEND_TIME_TEXT_BY_LOCALE[lang]

                self.assertIn(expected['title'], body, msg=f"lang={lang} title")
                self.assertIn(expected['extend_question'], body, msg=f"lang={lang} extend_question")
                self.assertIn(expected['btn_yes_extend'], body, msg=f"lang={lang} btn_yes_extend")
                self.assertIn(expected['btn_no_end'], body, msg=f"lang={lang} btn_no_end")
                self.assertIn(expected['not_enough'], body, msg=f"lang={lang} not_enough")
                self.assertIn(expected['btn_end_only'], body, msg=f"lang={lang} btn_end_only")
                self.assertIn(expected['guest_desc'], body, msg=f"lang={lang} guest_desc")
                self.assertIn(expected['btn_register'], body, msg=f"lang={lang} btn_register")


class ExtendTimeModalContextTests(TestCase):
    """2026-09-05: 'spend points to add 10 seconds' feature. game_play must
    hand the template enough to pick the right one of the 3 modal variants
    (logged in w/ >=10pt, logged in w/ <10pt, guest) without another
    round-trip - see the is_authenticated/point_balance context below."""

    def setUp(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'extend-word-{i}', englishname=f'Word {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'extend-word-{i}', description='d', history='h')

    def _start_timeattack_game(self):
        self.client.get(reverse('game_restart'))
        return self.client.get(reverse('game_play'))

    def test_guest_context(self):
        response = self._start_timeattack_game()
        self.assertFalse(response.context['is_authenticated'])
        self.assertEqual(response.context['point_balance'], 0)
        self.assertEqual(response.context['time_extend_cost'], 10)
        self.assertEqual(response.context['time_extend_seconds'], 10)

    def test_logged_in_with_enough_points_context(self):
        user = User.objects.create_user(username='extend_rich', password='pass12345')
        user.profile.total_score = 25
        user.profile.save()
        self.client.login(username='extend_rich', password='pass12345')

        response = self._start_timeattack_game()
        self.assertTrue(response.context['is_authenticated'])
        self.assertEqual(response.context['point_balance'], 25)

    def test_logged_in_with_insufficient_points_context(self):
        user = User.objects.create_user(username='extend_poor', password='pass12345')
        user.profile.total_score = 3
        user.profile.save()
        self.client.login(username='extend_poor', password='pass12345')

        response = self._start_timeattack_game()
        self.assertTrue(response.context['is_authenticated'])
        self.assertEqual(response.context['point_balance'], 3)

    def test_modal_markup_and_scripts_present_for_timed_mode(self):
        response = self._start_timeattack_game()
        body = response.content.decode()
        self.assertIn('id="timeup-modal-overlay"', body)
        self.assertIn('requestExtendTime', body)
        self.assertIn('showTimeUpModal', body)


class GameExtendTimeEndpointTests(TestCase):
    """2026-09-05: POST /game/extend-time/ - spends TIME_EXTEND_COST points
    from Profile.total_score and pushes session['game_end_time'] back by
    TIME_EXTEND_SECONDS. No cap on repeat use (see project decision in
    memory) - the player's own point balance is the only limiter."""

    def setUp(self):
        self.user = User.objects.create_user(username='extend_endpoint_user', password='pass12345')
        self.client.login(username='extend_endpoint_user', password='pass12345')
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'extend-ep-word-{i}', englishname=f'Word {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'extend-ep-word-{i}', description='d', history='h')

    def _start_timeattack_game(self):
        self.client.get(reverse('game_restart'))
        self.client.get(reverse('game_play'))
        self.client.get(reverse('game_question_data'))  # sets session['game_end_time']

    def test_extend_succeeds_and_deducts_points(self):
        self.user.profile.total_score = 15
        self.user.profile.save()
        self._start_timeattack_game()

        end_time_before = self.client.session['game_end_time']
        response = self.client.post(reverse('game_extend_time'))
        result = json.loads(response.content)

        self.assertTrue(result['success'])
        self.assertEqual(result['balance'], 5)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 5)
        self.assertAlmostEqual(self.client.session['game_end_time'], end_time_before + 10, delta=1)

    def test_extend_can_be_used_repeatedly_while_points_last(self):
        self.user.profile.total_score = 25
        self.user.profile.save()
        self._start_timeattack_game()
        end_time_before = self.client.session['game_end_time']

        first = json.loads(self.client.post(reverse('game_extend_time')).content)
        second = json.loads(self.client.post(reverse('game_extend_time')).content)
        third = json.loads(self.client.post(reverse('game_extend_time')).content)

        self.assertTrue(first['success'])
        self.assertTrue(second['success'])
        self.assertFalse(third['success'])  # 25 - 10 - 10 = 5, not enough for a 3rd
        self.assertEqual(third['reason'], 'insufficient_points')

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 5)
        self.assertAlmostEqual(self.client.session['game_end_time'], end_time_before + 20, delta=1)

    def test_insufficient_points_rejected_without_deduction(self):
        self.user.profile.total_score = 9
        self.user.profile.save()
        self._start_timeattack_game()
        end_time_before = self.client.session['game_end_time']

        response = self.client.post(reverse('game_extend_time'))
        result = json.loads(response.content)

        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'insufficient_points')
        self.assertEqual(result['balance'], 9)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 9)
        self.assertEqual(self.client.session['game_end_time'], end_time_before)

    def test_exactly_ten_points_is_enough(self):
        self.user.profile.total_score = 10
        self.user.profile.save()
        self._start_timeattack_game()

        result = json.loads(self.client.post(reverse('game_extend_time')).content)

        self.assertTrue(result['success'])
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 0)

    def test_guest_cannot_extend(self):
        self.client.logout()
        response = self.client.post(reverse('game_extend_time'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(json.loads(response.content)['success'])

    def test_rejected_without_an_active_timed_game(self):
        self.user.profile.total_score = 100
        self.user.profile.save()
        # no game_restart/game_play/game_question_data called - no session state
        response = self.client.post(reverse('game_extend_time'))
        result = json.loads(response.content)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'no_active_timed_game')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 100)

    def test_rejected_for_untimed_single_mode(self):
        self.user.profile.total_score = 100
        self.user.profile.save()
        word = ThankJapanModel.objects.get(name='extend-ep-word-0')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': word.slug,
        })
        self.client.get(reverse('game_play'))
        self.client.get(reverse('game_question_data'))

        response = self.client.post(reverse('game_extend_time'))
        result = json.loads(response.content)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'no_active_timed_game')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 100)

    def test_rejected_for_untimed_first_taste_mode(self):
        self.user.profile.total_score = 100
        self.user.profile.save()
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.client.get(reverse('game_play'))
        self.client.get(reverse('game_question_data'))

        response = self.client.post(reverse('game_extend_time'))
        result = json.loads(response.content)

        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'no_active_timed_game')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, 100)

    def test_player_model_score_stays_in_sync_after_extend(self):
        self.user.profile.total_score = 15
        self.user.profile.save()
        Player.objects.create(username=self.user.username, total_score=15)
        self._start_timeattack_game()

        self.client.post(reverse('game_extend_time'))

        player = Player.objects.get(username=self.user.username)
        self.assertEqual(player.total_score, 5)


GAME_PLAY_TITLE_BY_LOCALE = {
    'ja': '日本語クイズに挑戦！(Japanese Quiz) | ThankJapan',
    'zh-hant': '挑戰日文小測驗！(Japanese Quiz) | ThankJapan',
    'zh-cn': '挑战日语小测验！(Japanese Quiz) | ThankJapan',
    'ko': '일본어 퀴즈에 도전!(Japanese Quiz) | ThankJapan',
    'de': 'Japanisch-Quiz-Herausforderung! (Japanese Quiz) | ThankJapan',
    'fr': 'Défie-toi en japonais ! (Japanese Quiz) | ThankJapan',
    'es-es': '¡Desafía tu japonés! (Japanese Quiz) | ThankJapan',
    'it': 'Sfida il tuo giapponese! (Japanese Quiz) | ThankJapan',
    'pt': 'Desafie seu japonês! (Japanese Quiz) | ThankJapan',
    'vi': 'Thử thách đố vui tiếng Nhật! (Japanese Quiz) | ThankJapan',
    'th': 'ท้าทายควิซภาษาญี่ปุ่น! (Japanese Quiz) | ThankJapan',
    'en': 'Japanese Quiz Challenge! | ThankJapan',
}
GAME_PLAY_TITLE_BY_LOCALE['es-mx'] = GAME_PLAY_TITLE_BY_LOCALE['es-es']
GAME_PLAY_TITLE_BY_LOCALE['pt-br'] = GAME_PLAY_TITLE_BY_LOCALE['pt']
GAME_PLAY_TITLE_BY_LOCALE['en-in'] = GAME_PLAY_TITLE_BY_LOCALE['en']

COUNTDOWN_GO_BY_LOCALE = {
    'ja': 'スタート！', 'zh-hant': '開始！', 'zh-cn': '开始！', 'ko': '시작!',
    'de': 'Los!', 'fr': 'Go !', 'es-es': '¡Ya!', 'it': 'Via!', 'pt': 'Vai!',
    'vi': 'Bắt đầu!', 'th': 'เริ่ม!', 'en': 'GO!',
}
COUNTDOWN_GO_BY_LOCALE['es-mx'] = COUNTDOWN_GO_BY_LOCALE['es-es']
COUNTDOWN_GO_BY_LOCALE['pt-br'] = COUNTDOWN_GO_BY_LOCALE['pt']
COUNTDOWN_GO_BY_LOCALE['en-in'] = COUNTDOWN_GO_BY_LOCALE['en']


class GamePlayTitleAndCountdownLocaleTests(TestCase):
    """2026-09-05: game_play's <title> used to interpolate the raw session
    score ('Score {{ score }} | ThankJapan'), reported as reading like a
    leaked internal variable rather than a meaningful page title - and it
    never even reflected the *current* score anyway, since it's rendered
    once at initial page load and never touched again by the AJAX-based
    play loop. Replaced with a static, translated title per locale. Also
    covers the 3-2-1-GO countdown's final beat being translated per locale
    instead of a hardcoded English 'GO!'."""

    def setUp(self):
        ThankJapanModel.objects.create(
            name='title-locale-word', englishname='Word', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='title-locale-word', description='d', history='h')

    @staticmethod
    def _extract_title(body):
        return body.split('<title>', 1)[1].split('</title>', 1)[0].strip()

    def test_title_is_static_and_translated_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.get(reverse('game_restart'), {'lang': lang})
                response = client.get(reverse('game_play'), {'lang': lang})
                title = self._extract_title(response.content.decode())

                self.assertEqual(title, GAME_PLAY_TITLE_BY_LOCALE[lang])
                # the old bug: a raw score number leaking into the title
                self.assertNotIn('0', title)

    def test_countdown_go_text_translated_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.get(reverse('game_restart'), {'lang': lang})
                response = client.get(reverse('game_play'), {'lang': lang})
                body = response.content.decode()

                self.assertIn(COUNTDOWN_GO_BY_LOCALE[lang], body, msg=f"lang={lang}")
                self.assertIn("i18n('i18n-countdown-go')", body)


class UntimedModeHidesTimerRingTests(TestCase):
    """2026-09-05: first_taste (and mode=single) are untimed, but the ring
    timer UI kept showing a frozen '60' since only the countdown *logic*
    was skipped for those modes, not the ring markup itself. The ring
    (id="ring-wrap") must be hidden whenever has_timer is False."""

    def setUp(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'timer-word-{i}', englishname=f'Word {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'timer-word-{i}', description='d', history='h')

    def test_first_taste_hides_the_ring(self):
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        response = self.client.get(reverse('game_play'))
        self.assertFalse(response.context['has_timer'])
        self.assertIn('class="ring-wrap" hidden', response.content.decode())

    def test_single_mode_hides_the_ring(self):
        word = ThankJapanModel.objects.get(name='timer-word-0')
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': word.slug,
        })
        response = self.client.get(reverse('game_play'))
        self.assertFalse(response.context['has_timer'])
        self.assertIn('class="ring-wrap" hidden', response.content.decode())

    def test_timeattack_still_shows_the_ring(self):
        self.client.get(reverse('game_restart'))
        response = self.client.get(reverse('game_play'))
        self.assertTrue(response.context['has_timer'])
        self.assertIn('class="ring-wrap">', response.content.decode())
        self.assertNotIn('class="ring-wrap" hidden', response.content.decode())


class GamePlayDualBgmTests(TestCase):
    """2026-09-05: layer the top page's existing BGM in under the game
    track (both existing audio files, no new asset) rather than replacing
    it. The two must play independently-addressable <audio> elements so
    the existing sound on/off toggle (which only knows the id #bgm-player)
    keeps working, while a local script mirrors its mute state onto the
    second track."""

    def setUp(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'bgm-word-{i}', englishname=f'Word {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'bgm-word-{i}', description='d', history='h')
        self.client.get(reverse('game_restart'))

    def test_second_track_rendered_with_own_id_when_top_bgm_configured(self):
        with patch('thank_japan_app.views.get_bgm_url', side_effect=lambda t: f'https://example.com/{t}.mp3'):
            response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn('id="bgm-player"', body)  # existing game track, untouched
        self.assertIn('src="https://example.com/game.mp3"', body)
        self.assertIn('id="bgm-player-top"', body)
        self.assertIn('src="https://example.com/top.mp3"', body)
        # both must autoplay muted (browser autoplay policy) and loop
        self.assertIn('<audio id="bgm-player-top" src="https://example.com/top.mp3" loop autoplay muted>', body)

    def test_mute_state_is_mirrored_from_main_track_to_top_track(self):
        with patch('thank_japan_app.views.get_bgm_url', side_effect=lambda t: f'https://example.com/{t}.mp3'):
            response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn("getElementById('bgm-player-top')", body)
        self.assertIn("mainTrack.addEventListener('volumechange'", body)
        self.assertIn('topTrack.muted = mainTrack.muted', body)

    def test_tracks_are_balanced_40_60(self):
        with patch('thank_japan_app.views.get_bgm_url', side_effect=lambda t: f'https://example.com/{t}.mp3'):
            response = self.client.get(reverse('game_play'))
        body = response.content.decode()
        self.assertIn('topTrack.volume = 0.4', body)
        self.assertIn('mainTrack.volume = 0.6', body)

    def test_second_track_omitted_when_no_top_bgm_configured(self):
        with patch('thank_japan_app.views.get_bgm_url', side_effect=lambda t: 'https://example.com/game.mp3' if t == 'game' else None):
            response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn('id="bgm-player"', body)
        self.assertNotIn('bgm-player-top', body)


class GameSoundToggleWiringTests(TestCase):
    """2026-09-06: #game-sound-toggle (the small round button next to the
    score chip) used to be unwired dead markup - harmless on phones, where
    the header's own "Sound" button (#global-sound-toggle) is also visible,
    but on tablet/PC that header is hidden (>=768px, see the tablet layout
    fix) leaving this as the *only* visible sound control there. Now wired
    to drive the same localStorage key and the same #bgm-player element the
    header's toggle uses, so both stay in sync and both tracks (main +
    top-page ambience) mute/unmute together regardless of which button (or
    viewport) was used. This wiring must be present unconditionally - i.e.
    even when the second BGM track isn't configured at all."""

    def setUp(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'sound-toggle-word-{i}', englishname=f'Word {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'sound-toggle-word-{i}', description='d', history='h')
        self.client.get(reverse('game_restart'))

    def test_toggle_wiring_present_regardless_of_top_bgm(self):
        with patch('thank_japan_app.views.get_bgm_url', side_effect=lambda t: 'https://example.com/game.mp3' if t == 'game' else None):
            response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertNotIn('bgm-player-top', body)  # confirms this case has no second track
        self.assertIn("getElementById('game-sound-toggle')", body)
        self.assertIn("getElementById('game-sound-icon')", body)
        self.assertIn("gameBtn.addEventListener('click'", body)

    def test_toggle_shares_the_header_localstorage_key(self):
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn("localStorage.getItem('thankjapan_sound')", body)
        self.assertIn("localStorage.setItem('thankjapan_sound', newState)", body)

    def test_toggle_drives_the_shared_bgm_player_element(self):
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn("const mainTrack = document.getElementById('bgm-player')", body)
        self.assertIn("mainTrack.muted = (newState === 'off')", body)

    def test_toggle_keeps_header_icon_in_sync(self):
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn("getElementById('global-sound-icon')", body)

    def test_icon_resyncs_on_any_mute_change_not_just_its_own_click(self):
        # Covers the header button (or bgm_player.html's first-click
        # unmute) also updating this icon, via the shared #bgm-player
        # volumechange event rather than only this button's own handler.
        response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn("mainTrack.addEventListener('volumechange', syncGameIcon)", body)

    def test_both_tracks_still_configured_when_top_bgm_present(self):
        # Sanity check that this feature and the dual-BGM feature coexist -
        # both scripts reference #bgm-player independently without conflict.
        with patch('thank_japan_app.views.get_bgm_url', side_effect=lambda t: f'https://example.com/{t}.mp3'):
            response = self.client.get(reverse('game_play'))
        body = response.content.decode()

        self.assertIn('id="bgm-player-top"', body)
        self.assertIn("getElementById('game-sound-toggle')", body)
        self.assertIn("mainTrack.addEventListener('volumechange', syncGameIcon)", body)
        self.assertIn("topTrack.muted = mainTrack.muted", body)


class GamePlayTabletElementScalingTests(TestCase):
    """2026-09-05: the tablet full-height fix stretched .main2 but left the
    image/tiles/timer at phone sizing, looking lost in the extra space.
    Scaling is scoped to (min-width: 768px) and (orientation: portrait) -
    tablets specifically, not wide/landscape PC windows or phones - so this
    just pins the media query and a few of its key rules are present in
    the rendered CSS (PC/phone sizing is asserted unchanged elsewhere by
    every other test in this file rendering the same template)."""

    def setUp(self):
        for i in range(6):
            ThankJapanModel.objects.create(
                name=f'tablet-word-{i}', englishname=f'Word {i}', jpname='いぬ',
                kanji_name='犬', katakana_name='イヌ', category='animal',
                slug=f'tablet-word-{i}', description='d', history='h')
        self.client.get(reverse('game_restart'))

    def test_tablet_media_query_scales_up_key_gameplay_elements(self):
        body = self.client.get(reverse('game_play')).content.decode()

        self.assertIn('@media (min-width: 768px) and (orientation: portrait)', body)
        # one representative rule per element family named in the request
        self.assertIn('.prompt-card img { max-width: 380px; height: 240px; }', body)
        self.assertIn('.tile, .slot { font-size: 1.875rem;', body)
        self.assertIn('.ring-wrap { width: 96px; height: 96px; }', body)
        self.assertIn('.ki-gauge-wrap { max-width: 380px; }', body)
        # tile spacing (both within a row and between the answer/bank rows)
        self.assertIn('.answer-row, .bank-row { max-width: 680px; gap: 20px; }', body)
        self.assertIn('.answer-row { margin-bottom: 28px; }', body)


class GameStartTabletElementScalingTests(TestCase):
    """2026-09-05: game_start's .container is a flex column with no
    justify-content, so it defaults to flex-start - invisible on a phone
    (content roughly fills the viewport anyway) but on a tablet-portrait
    viewport (much taller for a similar width) everything clumped near the
    top with a large empty band below. Same (min-width: 768px) and
    (orientation: portrait) scope as game_play's tablet fix, spreading
    .container's children out with justify-content: space-evenly and
    scaling up the PLAY button and related elements to match."""

    def test_tablet_media_query_distributes_and_scales_elements(self):
        body = self.client.get(reverse('game_start')).content.decode()

        self.assertIn('@media (min-width: 768px) and (orientation: portrait)', body)
        self.assertIn('justify-content: space-evenly', body)
        self.assertIn('.play-btn { font-size: 2.3rem;', body)
        self.assertIn('.rules-card { max-width: 560px;', body)

    def test_base_phone_pc_sizing_is_still_present_and_unscoped(self):
        # the original (non-tablet) rules must remain exactly as they were,
        # since PC/phone should see no change at all.
        body = self.client.get(reverse('game_start')).content.decode()

        self.assertIn('.play-btn {\n    font-family:', body)
        self.assertIn("font-size: 1.7rem;", body)


class PremiumRenewalRewardModelTests(TestCase):
    """The (user, period_start) unique constraint is the actual double-
    grant guard for the premium continuation benefit - not caller
    coordination between the webhook and the daily batch."""

    def setUp(self):
        self.user = User.objects.create_user(username='reward_model_user', password='pass12345')

    def test_duplicate_period_start_for_same_user_is_rejected(self):
        period_start = timezone.now()
        PremiumRenewalReward.objects.create(user=self.user, period_start=period_start, points=50)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                PremiumRenewalReward.objects.create(user=self.user, period_start=period_start, points=50)

    def test_same_period_start_allowed_for_different_users(self):
        other_user = User.objects.create_user(username='reward_model_user2', password='pass12345')
        period_start = timezone.now()
        PremiumRenewalReward.objects.create(user=self.user, period_start=period_start, points=50)
        PremiumRenewalReward.objects.create(user=other_user, period_start=period_start, points=50)
        self.assertEqual(PremiumRenewalReward.objects.count(), 2)


class GrantRenewalRewardsIfDueTests(TestCase):
    """2026-09-06: the premium continuation benefit's core logic, shared by
    the daily grant_premium_renewal_points batch and the PayPal ACTIVATED
    webhook's opportunistic fast path. No historical contract-start date
    exists for anyone, so the first sighting of a real (non-trial) premium
    profile just anchors "now" rather than granting retroactively."""

    def setUp(self):
        self.user = User.objects.create_user(username='grant_logic_user', password='pass12345')
        self.profile = self.user.profile
        self.profile.is_premium = True
        self.profile.is_trial = False
        self.profile.save()

    def test_first_call_sets_anchor_and_grants_nothing(self):
        count = grant_renewal_rewards_if_due(self.profile)

        self.assertEqual(count, 0)
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.premium_paid_started_at)
        self.assertEqual(self.profile.total_score, 0)
        self.assertEqual(PremiumRenewalReward.objects.filter(user=self.user).count(), 0)

    def test_grants_50pt_after_one_month_elapsed(self):
        self.profile.premium_paid_started_at = timezone.now() - relativedelta(months=1, days=1)
        self.profile.save()

        count = grant_renewal_rewards_if_due(self.profile)

        self.assertEqual(count, 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)
        self.assertEqual(PremiumRenewalReward.objects.filter(user=self.user).count(), 1)

    def test_no_grant_before_one_month_elapsed(self):
        self.profile.premium_paid_started_at = timezone.now() - relativedelta(days=10)
        self.profile.save()

        count = grant_renewal_rewards_if_due(self.profile)

        self.assertEqual(count, 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.total_score, 0)

    def test_calling_twice_in_the_same_period_does_not_double_grant(self):
        self.profile.premium_paid_started_at = timezone.now() - relativedelta(months=1, days=1)
        self.profile.save()

        grant_renewal_rewards_if_due(self.profile)
        self.profile.refresh_from_db()
        second_count = grant_renewal_rewards_if_due(self.profile)

        self.assertEqual(second_count, 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)
        self.assertEqual(PremiumRenewalReward.objects.filter(user=self.user).count(), 1)

    def test_catchup_is_bounded_by_max_catchup_constant(self):
        # anchor set far enough in the past that many periods have elapsed -
        # a missed-run scenario must not grant a runaway number of periods.
        self.profile.premium_paid_started_at = timezone.now() - relativedelta(months=10)
        self.profile.save()

        count = grant_renewal_rewards_if_due(self.profile)

        self.assertEqual(count, PREMIUM_RENEWAL_REWARD_MAX_CATCHUP)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS * PREMIUM_RENEWAL_REWARD_MAX_CATCHUP)

    def test_player_model_stays_in_sync_after_grant(self):
        Player.objects.create(username=self.user.username, total_score=0)
        self.profile.premium_paid_started_at = timezone.now() - relativedelta(months=1, days=1)
        self.profile.save()

        grant_renewal_rewards_if_due(self.profile)

        player = Player.objects.get(username=self.user.username)
        self.assertEqual(player.total_score, PREMIUM_RENEWAL_REWARD_POINTS)

    def test_points_persist_after_premium_is_cancelled(self):
        self.profile.premium_paid_started_at = timezone.now() - relativedelta(months=1, days=1)
        self.profile.save()
        grant_renewal_rewards_if_due(self.profile)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)

        # Cancellation logic elsewhere only ever touches these 3 fields.
        self.profile.is_premium = False
        self.profile.is_trial = False
        self.profile.premium_expires_at = None
        self.profile.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)
        self.assertEqual(PremiumRenewalReward.objects.filter(user=self.user).count(), 1)


class GrantPremiumRenewalPointsCommandTests(TestCase):
    """The daily batch (see project decision: runs once a day, is the
    reliable primary path for both PayPal and Google Play since Google
    Play has no webhook/RTDN infrastructure at all)."""

    def setUp(self):
        self.trial_user = User.objects.create_user(username='batch_trial_user', password='pass12345')
        self.trial_user.profile.is_premium = True
        self.trial_user.profile.is_trial = True
        self.trial_user.profile.save()

        self.new_paid_user = User.objects.create_user(username='batch_new_paid_user', password='pass12345')
        self.new_paid_user.profile.is_premium = True
        self.new_paid_user.profile.is_trial = False
        self.new_paid_user.profile.save()

        self.due_user = User.objects.create_user(username='batch_due_user', password='pass12345')
        self.due_user.profile.is_premium = True
        self.due_user.profile.is_trial = False
        self.due_user.profile.premium_paid_started_at = timezone.now() - relativedelta(months=1, days=1)
        self.due_user.profile.save()

        self.free_user = User.objects.create_user(username='batch_free_user', password='pass12345')

    def test_trial_users_are_never_touched(self):
        call_command('grant_premium_renewal_points')

        self.trial_user.profile.refresh_from_db()
        self.assertIsNone(self.trial_user.profile.premium_paid_started_at)
        self.assertEqual(self.trial_user.profile.total_score, 0)

    def test_free_non_premium_users_are_never_touched(self):
        call_command('grant_premium_renewal_points')

        self.free_user.profile.refresh_from_db()
        self.assertIsNone(self.free_user.profile.premium_paid_started_at)

    def test_new_paid_user_gets_anchored_not_granted(self):
        call_command('grant_premium_renewal_points')

        self.new_paid_user.profile.refresh_from_db()
        self.assertIsNotNone(self.new_paid_user.profile.premium_paid_started_at)
        self.assertEqual(self.new_paid_user.profile.total_score, 0)

    def test_due_user_gets_granted(self):
        call_command('grant_premium_renewal_points')

        self.due_user.profile.refresh_from_db()
        self.assertEqual(self.due_user.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)

    def test_running_command_twice_does_not_double_grant(self):
        call_command('grant_premium_renewal_points')
        call_command('grant_premium_renewal_points')

        self.due_user.profile.refresh_from_db()
        self.assertEqual(self.due_user.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)
        self.assertEqual(PremiumRenewalReward.objects.filter(user=self.due_user).count(), 1)


@override_settings(
    PAYPAL_CLIENT_ID='test-client-id',
    PAYPAL_CLIENT_SECRET='test-client-secret',
    PAYPAL_MODE='sandbox',
    PAYPAL_PLAN_ID='P-TESTPLAN',
    PAYPAL_WEBHOOK_ID='WH-TEST',
    SECURE_SSL_REDIRECT=False,
)
class PaypalWebhookRenewalRewardTests(TestCase):
    """The PayPal ACTIVATED webhook's opportunistic fast path for the
    continuation benefit - the daily batch is what actually guarantees the
    grant happens, this just lets it happen sooner when the webhook fires."""

    def setUp(self):
        self.user = User.objects.create_user(username='webhook_reward_user', password='pass12345')
        self.user.profile.is_premium = True
        self.user.profile.is_trial = False
        self.user.profile.paypal_subscription_id = 'I-REWARDSUB'
        self.user.profile.premium_paid_started_at = timezone.now() - relativedelta(months=1, days=1)
        self.user.profile.save()

        self.paypal_headers = {
            'HTTP_PAYPAL_AUTH_ALGO': 'SHA256withRSA',
            'HTTP_PAYPAL_CERT_URL': 'https://api.paypal.com/cert',
            'HTTP_PAYPAL_TRANSMISSION_ID': 'txn-reward',
            'HTTP_PAYPAL_TRANSMISSION_SIG': 'sig',
            'HTTP_PAYPAL_TRANSMISSION_TIME': timezone.now().isoformat(),
        }

    @staticmethod
    def _post_side_effect():
        def side_effect(url, **kwargs):
            if 'oauth2/token' in url:
                return _mock_response(200, {'access_token': 'tok'})
            if 'verify-webhook-signature' in url:
                return _mock_response(200, {'verification_status': 'SUCCESS'})
            raise AssertionError(f'unexpected POST to {url}')
        return side_effect

    @staticmethod
    def _regular_subscription_payload(next_billing_time):
        return {
            'status': 'ACTIVE',
            'plan_id': 'P-TESTPLAN',
            'billing_info': {
                'next_billing_time': next_billing_time,
                'cycle_executions': [
                    {'tenure_type': 'REGULAR', 'sequence': 1, 'cycles_completed': 3, 'total_cycles': 0},
                ],
            },
        }

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_activated_webhook_grants_reward_for_real_subscriber(self, mock_post, mock_get):
        mock_post.side_effect = self._post_side_effect()
        mock_get.return_value = _mock_response(
            200, self._regular_subscription_payload((timezone.now() + timedelta(days=30)).isoformat())
        )

        response = self.client.post(
            reverse('paypal_webhook'),
            data=json.dumps({'event_type': 'BILLING.SUBSCRIPTION.ACTIVATED', 'resource': {'id': 'I-REWARDSUB'}}),
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_activated_webhook_does_not_grant_during_trial(self, mock_post, mock_get):
        mock_post.side_effect = self._post_side_effect()
        mock_get.return_value = _mock_response(200, {
            'status': 'ACTIVE',
            'plan_id': 'P-TESTPLAN',
            'billing_info': {
                'next_billing_time': (timezone.now() + timedelta(days=7)).isoformat(),
                'cycle_executions': [
                    {'tenure_type': 'TRIAL', 'sequence': 1, 'cycles_completed': 0, 'total_cycles': 1},
                ],
            },
        })

        response = self.client.post(
            reverse('paypal_webhook'),
            data=json.dumps({'event_type': 'BILLING.SUBSCRIPTION.ACTIVATED', 'resource': {'id': 'I-REWARDSUB'}}),
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_trial)
        self.assertEqual(self.user.profile.total_score, 0)

    @patch('thank_japan_app.views.requests.get')
    @patch('thank_japan_app.views.requests.post')
    def test_points_persist_after_subscription_cancelled_via_webhook(self, mock_post, mock_get):
        mock_post.side_effect = self._post_side_effect()
        mock_get.return_value = _mock_response(
            200, self._regular_subscription_payload((timezone.now() + timedelta(days=30)).isoformat())
        )

        self.client.post(
            reverse('paypal_webhook'),
            data=json.dumps({'event_type': 'BILLING.SUBSCRIPTION.ACTIVATED', 'resource': {'id': 'I-REWARDSUB'}}),
            content_type='application/json',
            **self.paypal_headers,
        )
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)

        cancel_response = self.client.post(
            reverse('paypal_webhook'),
            data=json.dumps({'event_type': 'BILLING.SUBSCRIPTION.CANCELLED', 'resource': {'id': 'I-REWARDSUB'}}),
            content_type='application/json',
            **self.paypal_headers,
        )

        self.assertEqual(cancel_response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_premium)
        self.assertEqual(self.user.profile.total_score, PREMIUM_RENEWAL_REWARD_POINTS)


PREMIUM_REWARD_HEADING_BY_URL_NAME = {
    'account_settings': 'CONTINUATION REWARDS',
    'account_settingsde': 'TREUEBONUS',
    'account_settingsenIN': 'CONTINUATION REWARDS',
    'account_settingsesES': 'BONO DE FIDELIDAD',
    'account_settingsesMX': 'BONO DE FIDELIDAD',
    'account_settingsfr': 'BONUS DE FIDÉLITÉ',
    'account_settingsit': 'BONUS FEDELTÀ',
    'account_settingsja': '継続特典',
    'account_settingsko': '구독 유지 혜택',
    'account_settingspt': 'BÔNUS DE FIDELIDADE',
    'account_settingsptBR': 'BÔNUS DE FIDELIDADE',
    'account_settingsth': 'โบนัสสมาชิกต่อเนื่อง',
    'account_settingsvi': 'THƯỞNG DUY TRÌ',
    'account_settingszhHANT': '續約禮遇',
    'account_settingszhCN': '续约礼遇',
}


@override_settings(SECURE_SSL_REDIRECT=False)
class AccountSettingsRenewalRewardHistoryTests(TestCase):
    """2026-09-06: 'recent 5 rewards' history on the account settings page,
    in all 15 supported locales - the account_settings* views are 15
    separate near-duplicate view functions/templates (one per locale, a
    pre-existing pattern), so this is deliberately exercised per-locale
    rather than just once."""

    def setUp(self):
        self.user = User.objects.create_user(username='reward_history_user', password='pass12345')
        self.client.login(username='reward_history_user', password='pass12345')

        base_time = timezone.now()
        # 7 rewards granted - only the most recent 5 should be displayed.
        for i in range(7):
            PremiumRenewalReward.objects.create(
                user=self.user,
                period_start=base_time - relativedelta(months=7 - i),
                points=50,
                granted_at=base_time - relativedelta(months=7 - i),
            )

    def test_reward_heading_and_recent_entries_render_per_locale(self):
        for url_name, expected_heading in PREMIUM_REWARD_HEADING_BY_URL_NAME.items():
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                body = response.content.decode()

                self.assertEqual(response.status_code, 200)
                self.assertIn(expected_heading, body, msg=f"url_name={url_name}")
                self.assertEqual(len(response.context['recent_renewal_rewards']), 5)
                self.assertEqual(body.count('+50pt'), 5, msg=f"url_name={url_name}")

    def test_no_reward_section_shown_when_user_has_no_rewards(self):
        PremiumRenewalReward.objects.filter(user=self.user).delete()

        response = self.client.get(reverse('account_settings'))
        body = response.content.decode()

        self.assertNotIn('CONTINUATION REWARDS', body)


PREMIUM_INFO_URL_NAMES = [
    'premium_info', 'premium_infode', 'premium_infoenIN', 'premium_infoesES',
    'premium_infoesMX', 'premium_infofr', 'premium_infoit', 'premium_infoja',
    'premium_infoko', 'premium_infopt', 'premium_infoptBR', 'premium_infoth',
    'premium_infovi', 'premium_infozhHANT', 'premium_infozhCN',
]

# Ad-related substrings that must no longer appear on any premium_info
# page, keyed just for readability - checked across the whole page body
# regardless of which locale's template it came from.
AD_FREE_LEFTOVER_SUBSTRINGS = [
    'Ad-Free', 'ad-free', 'Werbefrei', 'publicité', 'Pubblicità', 'pubblicità',
    'Publicidad', 'Anúncios', 'anúncios', '広告', '广告', '廣告', '광고',
    'โฆษณา', 'quảng cáo',
]


class PremiumInfoBenefitsListTests(TestCase):
    """2026-09-06: ThankJapan never had ads to begin with, so 'Ad-Free
    Experience' never made sense as a premium perk - replaced with the
    real continuation benefit (+50pt every month, see
    grant_renewal_rewards_if_due) across all 15 premium_info pages.

    Each template has two plan-feature lists in its source (one for the
    web/PayPal card inside {% if not is_twa %}, one for the Google Play
    card inside {% else %}), but only one branch ever renders per request
    - so the two variants are checked here via two separate requests
    (default web, and TWA-simulated) rather than expecting both markers
    in a single response body."""

    def test_ad_free_wording_is_gone_and_50pt_benefit_present_per_locale(self):
        for url_name in PREMIUM_INFO_URL_NAMES:
            with self.subTest(url_name=url_name, branch='web'):
                response = self.client.get(reverse(url_name))
                body = response.content.decode()

                self.assertEqual(response.status_code, 200)
                for leftover in AD_FREE_LEFTOVER_SUBSTRINGS:
                    self.assertNotIn(leftover, body, msg=f"url_name={url_name} leftover={leftover!r}")
                self.assertEqual(body.count('+50'), 1, msg=f"url_name={url_name}")

            with self.subTest(url_name=url_name, branch='twa'):
                twa_client = Client()
                session = twa_client.session
                session['is_twa'] = True
                session.save()

                response = twa_client.get(reverse(url_name))
                body = response.content.decode()

                self.assertEqual(response.status_code, 200)
                for leftover in AD_FREE_LEFTOVER_SUBSTRINGS:
                    self.assertNotIn(leftover, body, msg=f"url_name={url_name} (twa) leftover={leftover!r}")
                self.assertEqual(body.count('+50'), 1, msg=f"url_name={url_name} (twa)")


class DailyQuestionTitleRemovedTests(TestCase):
    """2026-09-06: the page's own "DAILY QUESTION" title sat right under
    the fixed .app-header (logo + pt badge), close enough to visually
    overlap it - removed outright (per the user's own call: decluttering,
    not a padding fix) rather than pushing content down further."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='title-removed-word', englishname='Word', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='title-removed-word', description='d', history='h')
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def test_dq_title_element_is_gone_from_question_state(self):
        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'question')
        self.assertNotIn('dq-title', body)

    def test_dq_title_element_is_gone_from_already_answered_state(self):
        session = self.client.session
        session['daily_question_answered_date'] = self.today.isoformat()
        session.save()

        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'already_answered')
        self.assertNotIn('dq-title', body)


class DailyQuestionPromptSpacingTests(TestCase):
    """2026-09-06: removing the title (see DailyQuestionTitleRemovedTests)
    left .dq-prompt-card - the image/caption at the very top of the
    in-progress question screen - sitting right where the fixed
    .app-header (logo + pt badge) overlaps it. Given it top margin so the
    whole question screen (image, caption, timer, tiles - all children of
    or after .dq-prompt-card) clears the header. The result screen uses a
    completely different element (.dq-result-image) and must be
    untouched."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='prompt-spacing-word', englishname='Word', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='prompt-spacing-word', description='d', history='h')
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def test_prompt_card_has_top_margin_in_question_state(self):
        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'question')
        self.assertIn('margin-top: 35px;', body)

    def test_result_state_image_styling_is_unchanged(self):
        # The stylesheet (with .dq-prompt-card's rule) is always present in
        # the page source regardless of state, but .dq-prompt-card itself -
        # and thus the extra top margin - must not appear as markup here;
        # the result screen renders .dq-result-image instead, whose own
        # rule must be untouched.
        tiles = json.loads(self.client.get(reverse('daily_question')).context['tiles_json'])
        response = self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(tiles)})
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'result')
        self.assertNotIn('class="dq-prompt-card"', body)
        self.assertIn('class="dq-badge', body)  # result state's correct/incorrect badge always renders
        # .dq-result-image's own rule is untouched (no margin-top added)
        self.assertIn(
            '#daily-question-wrapper .dq-result-image {\n        display: block;\n        width: 100%;\n        max-width: 400px;\n        border-radius: 12px;\n        margin: 0 auto 15px;\n    }',
            body,
        )


class DailyQuestionResultSpacingTests(TestCase):
    """2026-09-06: the result screen (share buttons etc.) has the same
    "first thing in the scroll area now overlaps the fixed .app-header"
    problem the question screen had, since it also lost the page title.
    Its outer card gets a second class (dq-result-card) carrying the same
    margin-top - scoped so the visually-identical .dq-card used by the
    already_answered state (which the user did not ask to change) is left
    alone."""

    def setUp(self):
        self.today = timezone.localdate()
        self.word = ThankJapanModel.objects.create(
            name='result-spacing-word', englishname='Word', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='result-spacing-word', description='d', history='h')
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def _answer(self):
        tiles = json.loads(self.client.get(reverse('daily_question')).context['tiles_json'])
        return self.client.post(reverse('daily_question'), {'answer_tiles': json.dumps(tiles)})

    def test_result_card_has_top_margin(self):
        response = self._answer()
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'result')
        self.assertIn('class="dq-card dq-result-card"', body)
        self.assertIn('#daily-question-wrapper .dq-result-card { margin-top: 35px; }', body)

    def test_already_answered_card_does_not_get_the_extra_class_or_margin(self):
        # .dq-result-card's CSS rule is always present in the page's
        # <style> block regardless of state (same as any other rule in
        # that shared stylesheet), so the check that matters is the
        # *markup* - the class must not be applied to any actual element
        # here, and the plain unmodified .dq-card must still be used.
        session = self.client.session
        session['daily_question_answered_date'] = self.today.isoformat()
        session.save()

        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'already_answered')
        self.assertNotIn('class="dq-card dq-result-card"', body)
        self.assertIn('<div class="dq-card">', body)

    def test_question_state_prompt_card_margin_is_unaffected(self):
        # the earlier question-screen fix must still be exactly as it was
        response = self.client.get(reverse('daily_question'))
        body = response.content.decode()

        self.assertEqual(response.context['daily_state'], 'question')
        self.assertIn('#daily-question-wrapper .dq-prompt-card {', body)
        self.assertNotIn('class="dq-card dq-result-card"', body)

    def test_share_functionality_still_works_after_the_layout_change(self):
        response = self._answer()
        body = response.content.decode()

        self.assertIn('https://x.com/intent/tweet?text=', body)
        self.assertIn('handleShareClick(event, this.href,', body)
        self.assertIn('dq-share-btn', body)


class GameStartBackHomeButtonMobileSpacingTests(TestCase):
    """2026-09-06: on real phones, the 'Back to Home' link's fixed 60px
    bottom margin wasn't always enough clearance above the site's fixed
    75px bottom-nav bar, so the link could end up partly hidden underneath
    it. Fixed with a phone-only (<768px) media query; tablet/PC untouched."""

    def test_wrapper_has_dedicated_class_and_mobile_override_present(self):
        # 2026-09-06 follow-up: the exact buffer shrank from +20px to +10px
        # as part of the broader phone-compaction pass (see
        # GameStartPhoneCompactionTests) - only the calc()'s shape (still
        # clearing the 75px bottom-nav + safe-area-inset-bottom) matters
        # here, not the specific buffer constant.
        response = self.client.get(reverse('game_start'))
        body = response.content.decode()

        self.assertIn('class="back-home-wrap"', body)
        self.assertIn('@media (max-width: 767px)', body)
        self.assertIn('margin-bottom: calc(75px + env(safe-area-inset-bottom) + 16px) !important;', body)

    def test_tablet_still_zeroes_out_the_same_wrapper(self):
        # the tablet block's `div[style*="margin"]` selector must still
        # match now that the element also carries a class.
        response = self.client.get(reverse('game_start'))
        body = response.content.decode()

        self.assertIn('.container > div[style*="margin"] { margin: 0 !important; }', body)
