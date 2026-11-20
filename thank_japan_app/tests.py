import importlib
import json
import re
import time as time_module
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch
from urllib.parse import quote as urlquote

from dateutil.relativedelta import relativedelta

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
from django.db.utils import IntegrityError
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import context_processors, ranks, tile_split
from . import views as tj_views
from .achievements import ACHIEVEMENT_CATALOG, check_and_unlock_achievements, get_achievement_progress
from .models import DailyQuestion, Player, Profile, PremiumRenewalReward, ThankJapanModel, ThankJapanPremium, DailyActivitySummary, SiteVisitCounter, WordReviewLog, Achievement, PREMIUM_FREE_SAMPLE_SIZE, premium_free_sample_queryset
from .views import (
    combo_bonus_for_streak, build_combo_share_message, _sample_easy_ids, FIRST_TASTE_QUESTION_COUNT,
    grant_renewal_rewards_if_due, PREMIUM_RENEWAL_REWARD_POINTS, PREMIUM_RENEWAL_REWARD_MAX_CATCHUP,
    DAILY_QUESTION_SHARE_LOCALE_ALIAS, DAILY_QUESTION_SHARE_HEADING, DAILY_QUESTION_SHARE_INVITE,
    DAILY_QUESTION_SHARE_CTA, DAILY_QUESTION_SHARE_CHUNK_STEPS, DAILY_QUESTION_SHARE_ANSWER_LABEL,
    DAILY_QUESTION_HASHTAG_TERM, NAV_TUTORIAL_TARGET_SLUG, _nav_tutorial_eligible,
    _build_choices, _meaning_key, GAME_QUESTION_FORMAT, QUESTION_FORMAT_CHOICE,
)
from .pricing import (
    get_premium_price, get_top_page_seo_override,
    DEFAULT_TOP_PAGE_TITLE, DEFAULT_TOP_PAGE_DESCRIPTION,
)


# 2026-11-19: the tile-rearrangement game is dormant behind
# views.GAME_QUESTION_FORMAT since the switch to 3-choice questions. Tests of
# the tile mechanics themselves pin that flag back to 'tiles', so the dormant
# path stays covered and can be switched back on with confidence (see the
# ThreeChoice*Tests classes for the live 3-choice equivalents).
tiles_format = patch('thank_japan_app.views.GAME_QUESTION_FORMAT', 'tiles')


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

TIMED_CHALLENGE_BY_LOCALE = {  # game length = views.GAME_TIME_LIMIT_SECONDS (45s since 2026-11-19)
    'ja': '45秒チャレンジ', 'zh-hant': '45秒挑戰', 'zh-cn': '45秒挑战',
    'ko': '45초 챌린지', 'de': 'Challenge in 45 Sekunden', 'fr': 'Défi 45 secondes',
    'es-es': 'Desafío de 45 segundos', 'es-mx': 'Desafío de 45 segundos',
    'it': 'Sfida di 45 secondi', 'pt': 'Desafio de 45 segundos', 'pt-br': 'Desafio de 45 segundos',
    'vi': 'Thử thách 45 giây', 'th': 'ท้าทาย 45 วินาที',
    'en': '45s Challenge', 'en-in': '45s Challenge',
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


@tiles_format
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


@tiles_format
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
        # 2026-11-19 level system: a guest's normal game only draws from the
        # Lv1 categories (food/animal), so one such word must exist.
        ThankJapanModel.objects.create(
            name='single-return-lv1-word', englishname='Dog', jpname='いぬ',
            category='animal', slug='single-return-lv1-word', description='d', history='h',
        )
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
    """2026-09-05: the result screen always said '60s Challenge' (45s since
    2026-11-19) even for
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

    def test_timeattack_shows_45_second_challenge_label(self):
        response = self._play_one_question('timeattack')
        self.assertContains(response, '45s Challenge')
        self.assertNotContains(response, '60s Challenge')
        self.assertNotContains(response, 'No Time Limit')

    def test_single_practice_shows_no_time_limit_label(self):
        response = self._play_one_question('single')
        self.assertContains(response, 'No Time Limit')
        self.assertNotContains(response, '45s Challenge')

    def test_first_taste_shows_no_time_limit_label(self):
        response = self._play_one_question('first_taste')
        self.assertContains(response, 'No Time Limit')
        self.assertNotContains(response, '45s Challenge')


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

    # --- 2026-09-20: LCP investigation (GSC flagged 2,877 mobile URLs over
    # 2.5s) - the hero photo itself is fetchpriority="high" with a reserved
    # aspect-ratio box, and both variants of this page load through
    # css.html, which now front-loads preconnect hints for every render-
    # blocking cross-origin host instead of paying DNS+TCP+TLS serially. ---

    def test_free_word_detail_page_hero_image_has_lcp_hints(self):
        # Both hints only render inside the {% if object.image %} block -
        # setUp()'s fixture has no image, so give this one its own.
        self.word.image = 'thankjapan/images/dummy_test_asset'
        self.word.save()
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        self.assertIn('fetchpriority="high"', body)
        self.assertIn('width="900" height="500"', body)
        # 2026-09-20 regression: the explanatory note above this <img> was
        # originally written as a multi-line {# ... #} comment - Django's
        # docs are explicit that this tag is single-line only, so spanning
        # multiple lines makes it render as literal text instead of being
        # stripped (confirmed: {% comment %}...{% endcomment %} is the only
        # tag that supports multi-line content). Reported live on
        # production as visible junk text above the hero photo.
        self.assertNotIn('reserved-aspect-ratio', body)
        self.assertNotIn('{#', body)

    def test_premium_word_detail_page_hero_image_has_lcp_hints(self):
        self.premium_word.image = 'thankjapan/premium/dummy_test_asset'
        self.premium_word.save()
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        self.assertIn('fetchpriority="high"', body)
        self.assertNotIn('reserved-aspect-ratio', body)
        self.assertNotIn('{#', body)
        self.assertIn('width="900" height="500"', body)

    def test_free_word_detail_page_has_preconnect_hints_and_no_bootstrap_icons(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        for host in ['cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'res.cloudinary.com']:
            self.assertIn(f'rel="preconnect" href="https://{host}"', body)
        self.assertNotIn('bootstrap-icons', body)
        # Bootstrap CSS itself and Font Awesome are untouched this round.
        self.assertIn('bootstrap@5.3.0', body)
        self.assertIn('font-awesome', body)

    def test_premium_word_detail_page_has_preconnect_hints_and_no_bootstrap_icons(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        for host in ['cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'res.cloudinary.com']:
            self.assertIn(f'rel="preconnect" href="https://{host}"', body)
        self.assertNotIn('bootstrap-icons', body)

    # --- 2026-09-20: the bottom "Learn More Words" CTA was retitled to a
    # more eye-catching "What's your Japanese IQ?" and given its own
    # continuous bounce (layered on the shared pulse every game-invite-btn
    # already has).
    # 2026-09-23: per feedback that only the CTA buttons on this page should
    # move, the top practice-this-word button's one-shot JS "attention"
    # flourish was replaced with this exact same continuous bounce, so both
    # buttons now share one identical always-moving animation instead of two
    # different patterns. The (now-shared) class was renamed from
    # game-invite-btn-iq to game-invite-btn-bounce to reflect that. ---

    def test_free_word_detail_page_bottom_cta_is_the_iq_challenge(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        self.assertIn('game-invite-btn game-invite-btn-bounce', body)
        self.assertIn("What's Your Japanese IQ?", body)
        self.assertNotIn('Learn More Words', body)
        style_start = body.index('.game-invite-btn.game-invite-btn-bounce {')
        style_end = body.index('}', style_start)
        self.assertIn('game-invite-btn-bounce 1.4s', body[style_start:style_end])

    def test_premium_word_detail_page_bottom_cta_is_the_iq_challenge(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        self.assertIn('game-invite-btn game-invite-btn-bounce', body)
        self.assertIn("What's Your Japanese IQ?", body)
        self.assertNotIn('Learn More Words', body)

    def test_free_word_detail_page_top_practice_button_shares_the_same_bounce(self):
        # 2026-09-23: the top "practice this word" CTA used to get a
        # one-shot JS-triggered attention flourish instead of the bottom
        # button's continuous bounce - now both buttons carry the identical
        # game-invite-btn-bounce class/animation, and the old one-shot
        # flourish (class + keyframes + JS trigger) is gone entirely.
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        self.assertEqual(body.count('game-invite-btn game-invite-btn-bounce'), 2)
        self.assertNotIn('game-invite-btn-attention', body)

    def test_premium_word_detail_page_top_practice_button_shares_the_same_bounce(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        self.assertEqual(body.count('game-invite-btn game-invite-btn-bounce'), 2)
        self.assertNotIn('game-invite-btn-attention', body)

    def test_free_word_detail_page_badge_and_connector_are_static(self):
        # 2026-09-23: per feedback that only the CTA buttons should move,
        # the 🧠 badge's box-shadow pulse, its icon's bounce, and the
        # connector arrow's bounce were all removed - and the now-unused
        # keyframes deleted with them.
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        self.assertNotIn('practice-cta-headline-pulse', body)
        self.assertNotIn('practice-cta-headline-bounce', body)
        badge_block_start = body.index('.practice-cta-headline {')
        connector_block_start = body.index('.practice-cta-connector {')
        connector_block_end = body.index('}', connector_block_start)
        self.assertNotIn('animation:', body[badge_block_start:connector_block_end])

    def test_premium_word_detail_page_badge_and_connector_are_static(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        self.assertNotIn('practice-cta-headline-pulse', body)
        self.assertNotIn('practice-cta-headline-bounce', body)
        badge_block_start = body.index('.practice-cta-headline {')
        connector_block_start = body.index('.practice-cta-connector {')
        connector_block_end = body.index('}', connector_block_start)
        self.assertNotIn('animation:', body[badge_block_start:connector_block_end])

    def test_free_word_detail_page_has_practice_cta_headline_above_learn_more(self):
        # 2026-09-19: a standalone "test your Japanese?" eye-catcher, bigger
        # than any tap-icon hint on the page. Originally placed above the
        # top practice-this-word CTA box, then moved (per explicit feedback)
        # to sit above the *bottom* "Learn More Words" button instead - the
        # top box itself must stay untouched, so this checks the headline
        # lands strictly between the two "game-invite-btn" links, not
        # before the first one.
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        self.assertIn('practice-cta-headline', body)
        first_btn_idx = body.index('class="game-invite-btn')
        second_btn_idx = body.index('class="game-invite-btn', first_btn_idx + 1)
        headline_idx = body.index('practice-cta-headline"')
        self.assertGreater(headline_idx, first_btn_idx)
        self.assertLess(headline_idx, second_btn_idx)

    def test_premium_word_detail_page_has_practice_cta_headline_above_learn_more(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        self.assertIn('practice-cta-headline', body)
        first_btn_idx = body.index('class="game-invite-btn')
        second_btn_idx = body.index('class="game-invite-btn', first_btn_idx + 1)
        headline_idx = body.index('practice-cta-headline"')
        self.assertGreater(headline_idx, first_btn_idx)
        self.assertLess(headline_idx, second_btn_idx)

    # --- 2026-09-19: badge + button were both display:inline-flex, so they
    # sat side by side on any viewport wide enough to fit both (tablet and
    # up) - not an actual @media rule, just ordinary inline wrapping. Fixed
    # to display:flex (block-level) so the badge always forces its own
    # line regardless of width; a finger-icon connector links it visually
    # to the button beneath it. ---

    def test_free_word_detail_page_badge_is_block_level_and_has_a_connector(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        style_start = body.index('.practice-cta-headline {')
        style_end = body.index('.practice-cta-headline-icon {', style_start)
        badge_css = body[style_start:style_end]
        # display: flex (block-level), not inline-flex - the actual
        # property line, not just a substring check, since an explanatory
        # comment in this same block legitimately mentions 'inline-flex' by
        # name.
        self.assertIn('display: flex !important;', badge_css)
        self.assertNotIn('display: inline-flex', badge_css)
        self.assertIn('practice-cta-connector', body)
        headline_idx = body.index('practice-cta-headline"')
        connector_idx = body.index('practice-cta-connector"')
        second_btn_idx = body.index('class="game-invite-btn', body.index('class="game-invite-btn') + 1)
        self.assertGreater(connector_idx, headline_idx)
        self.assertLess(connector_idx, second_btn_idx)

    def test_premium_word_detail_page_badge_is_block_level_and_has_a_connector(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        style_start = body.index('.practice-cta-headline {')
        style_end = body.index('.practice-cta-headline-icon {', style_start)
        badge_css = body[style_start:style_end]
        # display: flex (block-level), not inline-flex - the actual
        # property line, not just a substring check, since an explanatory
        # comment in this same block legitimately mentions 'inline-flex' by
        # name.
        self.assertIn('display: flex !important;', badge_css)
        self.assertNotIn('display: inline-flex', badge_css)
        self.assertIn('practice-cta-connector', body)
        headline_idx = body.index('practice-cta-headline"')
        connector_idx = body.index('practice-cta-connector"')
        second_btn_idx = body.index('class="game-invite-btn', body.index('class="game-invite-btn') + 1)
        self.assertGreater(connector_idx, headline_idx)
        self.assertLess(connector_idx, second_btn_idx)

    # --- 2026-09-20: the connector was display:flex, a block that stretches
    # to its parent's full width by default - animating transform on that
    # whole wide box repainted a viewport-wide region every frame, which
    # read as "the whole screen shaking" rather than a small icon bouncing.
    # width:fit-content confines the animated box (and the icon itself
    # moved away from 👇, overused elsewhere on the site) to the emoji's own
    # small size. ---

    def test_free_word_detail_page_connector_animation_is_confined_to_the_icon(self):
        response = self.client.get(reverse('category_detail', kwargs={
            'category': 'animal', 'slug': 'animal-test-dog',
        }))
        body = response.content.decode()
        style_start = body.index('.practice-cta-connector {')
        style_end = body.index('}', style_start)
        connector_css = body[style_start:style_end]
        self.assertIn('width: fit-content !important;', connector_css)
        self.assertNotIn('display: flex !important;', connector_css)
        self.assertIn('<div class="practice-cta-connector" aria-hidden="true">🔽</div>', body)
        self.assertNotIn('\U0001f447', body)  # 👇 must not remain anywhere near the connector

    def test_premium_word_detail_page_connector_animation_is_confined_to_the_icon(self):
        response = self.client.get(reverse('detail_premium', kwargs={
            'category': 'TourismEtiquette', 'slug': 'premium-test-word',
        }))
        body = response.content.decode()
        style_start = body.index('.practice-cta-connector {')
        style_end = body.index('}', style_start)
        connector_css = body[style_start:style_end]
        self.assertIn('width: fit-content !important;', connector_css)
        self.assertNotIn('display: flex !important;', connector_css)
        self.assertIn('<div class="practice-cta-connector" aria-hidden="true">🔽</div>', body)
        self.assertNotIn('\U0001f447', body)

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

    def test_three_choice_description_present_per_locale(self):
        # 2026-11-19: updated from the tile-rearrangement wording to the
        # 3-choice game (the practice link itself is unchanged).
        expected_by_lang = {
            'ja': '3つの中から正しい意味を選ぼう', 'zh-hant': '從3個選項中選出正確的意思', 'zh-cn': '从3个选项中选出正确的意思',
            'ko': '3개 중에서 올바른 뜻을 골라보세요', 'de': 'Wähle aus 3 Antworten die richtige Bedeutung',
            'fr': 'Choisis la bonne signification parmi 3', 'es-es': 'Elige el significado correcto entre 3',
            'it': 'Scegli il significato giusto tra 3', 'pt': 'Escolha o significado certo entre 3',
            'vi': 'Hãy chọn nghĩa đúng trong 3 đáp án', 'th': 'เลือกความหมายที่ถูกต้องจาก 3 ตัวเลือก',
            'en': 'Pick the right meaning from 3 choices',
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


@tiles_format
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


# 2026-11-20: reworded from "premium words earn double points" - the 2pt
# words are served to every player from the level shown (ranks.py), so the
# rule no longer reads like a subscriber perk.
GAME_START_DOUBLE_POINTS_TEXT_BY_LOCALE = {
    'en': '💎 Advanced words (Lv8 and up) earn 2pt!', 'en-in': '💎 Advanced words (Lv8 and up) earn 2pt!',
    'ja': '💎 上級の単語(Lv8から)は2pt！', 'zh-hant': '💎 進階單字（Lv8起）可得2分！', 'zh-cn': '💎 进阶单词（Lv8起）可得2分！',
    'ko': '💎 상급 단어(Lv8부터)는 2점!', 'de': '💎 Fortgeschrittene Wörter (ab Lv8) bringen 2 Pkt.!',
    'fr': '💎 Les mots avancés (dès Lv8) rapportent 2 pts !', 'es-es': '💎 ¡Las palabras avanzadas (desde Lv8) dan 2 pts!',
    'es-mx': '💎 ¡Las palabras avanzadas (desde Lv8) dan 2 pts!', 'it': '💎 Le parole avanzate (dal Lv8) danno 2 pt!',
    'pt': '💎 Palavras avançadas (a partir do Lv8) valem 2 pts!', 'pt-br': '💎 Palavras avançadas (a partir do Lv8) valem 2 pts!',
    'vi': '💎 Từ nâng cao (từ Lv8) được 2 điểm!', 'th': '💎 คำศัพท์ระดับสูง (ตั้งแต่ Lv8) ได้ 2 คะแนน!',
}

# premium_info's plan-features <li> wraps the key phrase in <strong> with an
# &nbsp; before it (e.g. 'Premium Words Give&nbsp;<strong>Double
# Points</strong>') - assert on just the <strong>-wrapped phrase itself
# rather than the full sentence, since &nbsp;/tag markup between words
# means the full sentence is never one contiguous substring of the raw HTML.
PREMIUM_INFO_DOUBLE_POINTS_PHRASE_BY_LOCALE = {  # removed from the benefit list 2026-11-19
    'en': 'Double Points', 'ja': '2倍ポイント', 'de': 'doppelte Punkte',
    'en-in': 'Double Points', 'es-es': 'el doble de puntos', 'es-mx': 'el doble de puntos',
    'fr': 'points doublés', 'it': 'punti doppi', 'ko': '2배 포인트',
    'pt': 'pontos em dobro', 'pt-br': 'pontos em dobro',
    'th': 'คะแนน 2 เท่า', 'vi': 'gấp đôi điểm',
    'zh-hant': '雙倍點數', 'zh-cn': '双倍积分',
}
PREMIUM_INFO_DETAIL_PAGES_PHRASE_BY_LOCALE = {
    'en': 'Every Premium Word&nbsp;<strong>Detail Page</strong>', 'en-in': 'Every Premium Word&nbsp;<strong>Detail Page</strong>',
    'ja': 'プレミアム単語の&nbsp;<strong>詳細ページを全部見られる</strong>',
    'de': 'Alle Premiumwörter&nbsp;<strong>im Detail ansehen</strong>',
    'es-es': 'Todas las palabras premium&nbsp;<strong>en detalle</strong>', 'es-mx': 'Todas las palabras premium&nbsp;<strong>en detalle</strong>',
    'fr': 'Mots premium :&nbsp;<strong>toutes les fiches détaillées</strong>',
    'it': 'Parole premium:&nbsp;<strong>tutte le schede dettagliate</strong>',
    'ko': '프리미엄 단어&nbsp;<strong>상세 페이지 전체 열람</strong>',
    'pt': 'Todas as palavras premium&nbsp;<strong>em detalhes</strong>', 'pt-br': 'Todas as palavras premium&nbsp;<strong>em detalhes</strong>',
    'th': 'ดูหน้ารายละเอียดคำศัพท์พรีเมียม&nbsp;<strong>ได้ทั้งหมด</strong>',
    'vi': 'Xem&nbsp;<strong>toàn bộ trang chi tiết</strong>&nbsp;từ premium',
    'zh-hant': '進階單字&nbsp;<strong>詳細頁面全部開放</strong>', 'zh-cn': '进阶单词&nbsp;<strong>详情页全部开放</strong>',
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

    def test_premium_info_lists_detail_pages_not_double_points_per_locale_both_branches(self):
        # 2026-11-19: with the level system every player gets premium words
        # (2pt) from Lv8, so "double points" left the subscriber benefit list
        # in favour of "every premium word detail page".
        for url_name in PREMIUM_INFO_URL_NAMES:
            lang = 'en' if url_name == 'premium_info' else url_name.replace('premium_info', '')
            lang_key = {
                'de': 'de', 'enIN': 'en-in', 'esES': 'es-es', 'esMX': 'es-mx', 'fr': 'fr',
                'it': 'it', 'ja': 'ja', 'ko': 'ko', 'pt': 'pt', 'ptBR': 'pt-br', 'th': 'th',
                'vi': 'vi', 'zhHANT': 'zh-hant', 'zhCN': 'zh-cn',
            }.get(lang, 'en')
            expected = PREMIUM_INFO_DETAIL_PAGES_PHRASE_BY_LOCALE[lang_key]
            removed = PREMIUM_INFO_DOUBLE_POINTS_PHRASE_BY_LOCALE[lang_key]
            with self.subTest(url_name=url_name, branch='web'):
                response = self.client.get(reverse(url_name))
                self.assertContains(response, expected)
                self.assertNotContains(response, removed)
            with self.subTest(url_name=url_name, branch='twa'):
                session = self.client.session
                session['is_twa'] = True
                session.save()
                response = self.client.get(reverse(url_name))
                self.assertContains(response, expected)
                self.assertNotContains(response, removed)
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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


@tiles_format
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

    def test_game_result_45s_challenge_label_per_locale(self):
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
                self.assertIn(TIMED_CHALLENGE_BY_LOCALE[lang], response.content.decode(), msg=f"lang={lang}")

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


@tiles_format
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

    def test_direct_url_is_downgraded_to_a_normal_game_for_a_returning_guest(self):
        # 2026-09-17: the tutorial mechanics (forced らーめん Q1, wrong-tap
        # blocking, the result screen's auto-guided flow) must only ever
        # fire once - the top page stops linking here after the first
        # visit, but nothing previously stopped ?difficulty=first_taste
        # from being reached directly a second time (stale bookmark,
        # back-button, retyped URL). A guest who already has the
        # tj_has_played cookie must silently get a normal game instead.
        self.client.cookies['tj_has_played'] = '1'
        response = self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        session = self.client.session
        self.assertEqual(session['game_difficulty'], 'timeattack')

    def test_direct_url_is_downgraded_to_a_normal_game_for_a_logged_in_user(self):
        # Same guard, other half: the top page never even shows the "try 3
        # questions" CTA to a logged-in user (it's gated to
        # not user.is_authenticated), so the tutorial must not be
        # reachable for one via this URL either, tj_has_played aside.
        User.objects.create_user(username='first_taste_login_test', password='pass12345')
        self.client.login(username='first_taste_login_test', password='pass12345')
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        session = self.client.session
        self.assertEqual(session['game_difficulty'], 'timeattack')

    def test_first_question_is_always_ramen_when_the_word_exists(self):
        # 2026-09-17: Q1 of the first-taste tutorial is pinned to らーめん
        # specifically (a guaranteed-correct, fully-guided step - see
        # game_play-v2.html's tap-blocking) whenever that word exists,
        # regardless of the normal random EASY-pool selection/category
        # filter (らーめん's own category, 'cook', isn't even in
        # first_taste's filter list - it's picked directly, bypassing it).
        ramen = ThankJapanModel.objects.create(
            name='raamen', englishname='Ramen', jpname='らーめん',
            kanji_name='', katakana_name='ラーメン', category='cook',
            slug='raamen', description='d', history='h',
        )
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        session = self.client.session
        ids = [q['id'] for q in session['game_question_ids']]
        self.assertEqual(ids[0], ramen.id)
        self.assertEqual(len(ids), FIRST_TASTE_QUESTION_COUNT)
        self.assertEqual(len(set(ids)), FIRST_TASTE_QUESTION_COUNT)  # no duplicates

        self.client.get(reverse('game_play'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        reconstructed = ''.join(data['tiles'][i] for i in data['tutorial_order'])
        self.assertEqual(reconstructed, 'らーめん')

    def test_second_question_is_always_inu_with_a_wrong_guide_order(self):
        # 2026-09-19: Q2 of the first-taste tutorial is pinned to いぬ
        # specifically - a guaranteed-WRONG, fully-guided step (the finger
        # points through the reversed ぬ→い order instead of い→ぬ), so the
        # guest sees the "incorrect answer" tile treatment once before the
        # real, unguided game starts.
        #
        # setUp() above already seeds 6 words with jpname='いぬ' for other
        # tests in this class - game_restart's lookup would pick one of
        # those arbitrarily (whichever .first() returns), not necessarily
        # a fresh one created here. Clear them so this test controls
        # exactly which いぬ row exists, same as the ramen/inu pairing
        # game_restart expects in production (where each word is unique).
        ThankJapanModel.objects.filter(jpname='いぬ').delete()
        ramen = ThankJapanModel.objects.create(
            name='raamen', englishname='Ramen', jpname='らーめん',
            kanji_name='', katakana_name='ラーメン', category='cook',
            slug='raamen', description='d', history='h',
        )
        inu = ThankJapanModel.objects.create(
            name='inu-forced', englishname='Dog', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='inu-forced', description='d', history='h',
        )
        # Something else for Q3's normal random pick to land on, now that
        # the shared いぬ pool above was cleared.
        ThankJapanModel.objects.create(
            name='q3-pool-word', englishname='Cat', jpname='ねこ',
            kanji_name='猫', katakana_name='ネコ', category='animal',
            slug='q3-pool-word', description='d', history='h',
        )

        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        session = self.client.session
        ids = [q['id'] for q in session['game_question_ids']]
        self.assertEqual(ids[0], ramen.id)
        self.assertEqual(ids[1], inu.id)

        self.client.get(reverse('game_play'))
        # Q1 (らーめん) - answer it to advance to Q2.
        q1 = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNone(q1.get('tutorial_wrong_order'))
        q1_tiles = [q1['tiles'][i] for i in q1['tutorial_order']]
        self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': q1_tiles, 'seconds_left': 60}),
            content_type='application/json',
        )

        q2 = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNone(q2.get('tutorial_order'))
        wrong_order = q2['tutorial_wrong_order']
        self.assertIsNotNone(wrong_order)
        self.assertEqual(sorted(wrong_order), list(range(len(q2['tiles']))))
        reconstructed = ''.join(q2['tiles'][i] for i in wrong_order)
        self.assertEqual(reconstructed, 'ぬい')
        self.assertNotEqual(reconstructed, 'いぬ')

        # Submitting exactly that guided (wrong) order must actually score
        # as incorrect, and the game must still continue to Q3 afterwards.
        q2_tiles = [q2['tiles'][i] for i in wrong_order]
        result = json.loads(self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': q2_tiles, 'seconds_left': 60}),
            content_type='application/json',
        ).content)
        self.assertFalse(result['is_correct'])

        q3 = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertFalse(q3.get('game_over'))
        self.assertIsNone(q3.get('tutorial_order'))
        self.assertIsNone(q3.get('tutorial_wrong_order'))

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
        self.assertIsNone(data.get('tutorial_wrong_order'))


@tiles_format
class NavigationTutorialTests(TestCase):
    """2026-09-17: the second, returning-guest-only tutorial (Learn ->
    Category list -> Cook -> つけ麺's detail page -> practice it in single
    mode -> back to top), gated on tj_has_played=1 + no tj_nav_tutorial_seen
    cookie yet. Separate from FirstTasteTileGameTests above, which covers
    the unrelated first-visit tutorial."""

    def setUp(self):
        self.tsukemen = ThankJapanModel.objects.create(
            name='tsukemen-test', englishname='Tsukemen', jpname='つけめん',
            kanji_name='つけ麺', katakana_name='ツケメン', category='cook',
            slug=NAV_TUTORIAL_TARGET_SLUG, description='d', history='h',
        )

    # --- 1. shown only to a returning, not-yet-seen guest ---

    def test_hint_hidden_for_a_brand_new_guest(self):
        response = self.client.get(reverse('toppage'))
        self.assertFalse(response.context['nav_tutorial_learn_step_active'])

    def test_hint_shown_for_a_returning_guest(self):
        self.client.cookies['tj_has_played'] = '1'
        response = self.client.get(reverse('toppage'))
        self.assertTrue(response.context['nav_tutorial_learn_step_active'])

    def test_hint_hidden_once_already_seen(self):
        self.client.cookies['tj_has_played'] = '1'
        self.client.cookies['tj_nav_tutorial_seen'] = '1'
        response = self.client.get(reverse('toppage'))
        self.assertFalse(response.context['nav_tutorial_learn_step_active'])

    # --- 2026-09-20: must never start back-to-back with first_taste in the
    # same browser session - tj_has_played flips to '1' the moment
    # first_taste is *started* (game_restart), not completed, so without a
    # separate signal a guest landing back on the top page right after
    # finishing the ramen tutorial would immediately qualify for this one
    # too. ---

    def test_hint_hidden_right_after_first_taste_completes_same_session(self):
        self.client.cookies['tj_has_played'] = '1'
        self.client.cookies['tj_first_taste_just_completed'] = '1'
        response = self.client.get(reverse('toppage'))
        self.assertFalse(response.context['nav_tutorial_learn_step_active'])

    def test_hint_shown_again_once_the_deferral_cookie_is_gone(self):
        # Simulates the guest's *next* visit: tj_has_played (long-lived)
        # survives, but tj_first_taste_just_completed (a true browser-
        # session cookie, no max_age - see game_result) does not.
        self.client.cookies['tj_has_played'] = '1'
        response = self.client.get(reverse('toppage'))
        self.assertTrue(response.context['nav_tutorial_learn_step_active'])

    def test_playing_first_taste_end_to_end_defers_nav_tutorial_same_session(self):
        # Full integration: actually play first_taste to completion (not
        # just simulate the cookies) and confirm game_result both sets the
        # deferral cookie as a true session cookie (no Max-Age/Expires in
        # the Set-Cookie header) and that it actually takes effect on the
        # very next request in this same client/cookie-jar.
        ThankJapanModel.objects.filter(jpname='いぬ').delete()
        ThankJapanModel.objects.create(
            name='raamen-e2e', englishname='Ramen', jpname='らーめん',
            kanji_name='', katakana_name='ラーメン', category='cook',
            slug='raamen-e2e', description='d', history='h',
        )
        ThankJapanModel.objects.create(
            name='inu-e2e', englishname='Dog', jpname='いぬ',
            kanji_name='犬', katakana_name='イヌ', category='animal',
            slug='inu-e2e', description='d', history='h',
        )
        ThankJapanModel.objects.create(
            name='q3-pool-e2e', englishname='Cat', jpname='ねこ',
            kanji_name='猫', katakana_name='ネコ', category='animal',
            slug='q3-pool-e2e', description='d', history='h',
        )

        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.client.get(reverse('game_play'))
        for _ in range(3):
            data = json.loads(self.client.get(reverse('game_question_data')).content)
            if data.get('game_over'):
                break
            self.client.post(
                reverse('game_submit_answer'),
                data=json.dumps({'tiles': data['tiles'], 'seconds_left': 60}),
                content_type='application/json',
            )

        result_response = self.client.get(reverse('game_result'))
        self.assertEqual(result_response.cookies['tj_first_taste_just_completed'].value, '1')
        # No max_age/expires -> a true browser-session cookie (Django omits
        # both the Max-Age and expires attributes entirely in that case).
        self.assertEqual(result_response.cookies['tj_first_taste_just_completed']['max-age'], '')
        self.assertEqual(result_response.cookies['tj_first_taste_just_completed']['expires'], '')

        top_response = self.client.get(reverse('toppage'))
        self.assertFalse(top_response.context['nav_tutorial_learn_step_active'])

    def test_hint_hidden_for_a_logged_in_user_even_with_has_played(self):
        User.objects.create_user(username='nav_tutorial_login_test', password='pass12345')
        self.client.login(username='nav_tutorial_login_test', password='pass12345')
        self.client.cookies['tj_has_played'] = '1'
        response = self.client.get(reverse('toppage'))
        self.assertFalse(response.context['nav_tutorial_learn_step_active'])

    def test_hint_flag_also_reaches_category_list_and_cook_page(self):
        self.client.cookies['tj_has_played'] = '1'
        cat_response = self.client.get(reverse('category_list'))
        self.assertTrue(cat_response.context['nav_tutorial_cook_card_step_active'])
        cook_response = self.client.get(reverse('cookpage'))
        self.assertTrue(cook_response.context['show_nav_tutorial_hint'])
        self.assertEqual(cook_response.context['nav_tutorial_target_slug'], NAV_TUTORIAL_TARGET_SLUG)

    # --- 2026-09-19: a new "here's the detail screen, now practice it"
    # overlay fires the instant the つけ麺 detail modal opens, before the
    # existing finger-hint on the practice button - same overlay-then-
    # reveal sequencing as every other step in this tutorial. ---

    def test_modal_open_overlay_markup_present_and_precedes_the_hint_reveal(self):
        self.client.cookies['tj_has_played'] = '1'
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertIn('id="tutorial-overlay-cook-modal-open"', cook_html)
        # The JS must play this overlay (with a callback) rather than build
        # the hint bubble immediately - otherwise it isn't actually gating
        # anything and the two would appear together. 2026-09-22: migrated
        # to playTutorialOverlayAuto (dynamic, text-length-based hold time +
        # tap-to-dismiss) - see tutorial_overlay.js.html - so the fixed
        # "2200" literal is gone; the gating relationship this test actually
        # cares about (overlay call precedes the hint-bubble build) is
        # unchanged.
        self.assertIn("playTutorialOverlayAuto('tutorial-overlay-cook-modal-open', function () {", cook_html)
        overlay_call_index = cook_html.index("playTutorialOverlayAuto('tutorial-overlay-cook-modal-open'")
        hint_build_index = cook_html.index('navTutorialModalHintCaption()', overlay_call_index)
        self.assertGreater(hint_build_index, overlay_call_index)

    def test_modal_open_overlay_absent_for_a_normal_visitor(self):
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertNotIn('tutorial-overlay-cook-modal-open', cook_html)

    # --- regression: the category-list page must never point back at the
    # top page's own "Learn" nav button (2026-09-19 bug report) - the two
    # touchpoints share nothing but the visual pattern now, not a context
    # variable, so this is impossible to render by construction. ---

    def test_category_list_page_never_renders_the_learn_button_hint(self):
        self.client.cookies['tj_has_played'] = '1'
        cat_html = self.client.get(reverse('category_list')).content.decode()
        self.assertNotIn('nav-tutorial-learn-hint', cat_html)
        self.assertNotIn('nav-tutorial-category-link-hint', cat_html)
        self.assertNotIn('tutorial-overlay-nav-learn', cat_html)
        self.assertNotIn('tutorial-overlay-nav-category-link', cat_html)
        # ... while its own, correct hint is present.
        self.assertIn('id="nav-tutorial-cook-card-hint"', cat_html)
        self.assertIn('tutorial-overlay-nav-cook-card', cat_html)

    def test_cook_page_never_renders_the_learn_button_hint_either(self):
        self.client.cookies['tj_has_played'] = '1'
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertNotIn('nav-tutorial-learn-hint', cook_html)
        self.assertNotIn('nav-tutorial-category-link-hint', cook_html)

    # --- 2. all seven steps, in order ---

    def test_full_seven_step_flow_in_order(self):
        self.client.cookies['tj_has_played'] = '1'

        # Steps 1-2: top page carries the "Learn" nav-button hint and the
        # "Category list" link hint (inside the Learn modal), each rendered
        # exactly once and starting hidden - client-side JS (sessionStorage-
        # driven, see base_top.html) is what reveals them one at a time and
        # isn't exercised by this server-side test, but the markup itself
        # must be present and correctly scoped.
        top_html = self.client.get(reverse('toppage')).content.decode()
        self.assertIn('id="learn-modal-btn"', top_html)
        self.assertEqual(top_html.count('id="nav-tutorial-learn-hint"'), 1)
        self.assertEqual(top_html.count('id="nav-tutorial-category-link-hint"'), 1)
        self.assertIn('tutorial-overlay-nav-learn', top_html)
        self.assertIn('tutorial-overlay-nav-category-link', top_html)

        # Step 3: category list page guides to the 料理(cook) card - and
        # only that card, never the (nonexistent-here) Learn button.
        cat_html = self.client.get(reverse('category_list')).content.decode()
        self.assertIn('tutorial-hint-target', cat_html)
        self.assertEqual(cat_html.count('id="nav-tutorial-cook-card-hint"'), 1)
        self.assertNotIn('nav-tutorial-learn-hint', cat_html)

        # Step 4: cook page tags the つけめん card specifically (by slug,
        # not grid position) with both the finger-guide and the explanatory
        # overlay.
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertIn('id="nav-tutorial-target-guide"', cook_html)
        self.assertIn('tutorial-overlay-cook-target', cook_html)

        # Step 5 (clicking "practice this word") / step 6 setup: game_restart
        # recognizes the target slug and flags this single-mode session as
        # guided.
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG,
        })
        session = self.client.session
        self.assertTrue(session['nav_tutorial_single_active'])
        self.assertEqual(session['game_difficulty'], 'single')

        play_response = self.client.get(reverse('game_play'))
        self.assertTrue(play_response.context['is_nav_tutorial_single'])
        self.assertIn('tutorial-overlay-nav-single', play_response.content.decode())

        # Step 6: the tile order is guided to the correct つけめん spelling.
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNotNone(data['tutorial_order'])
        reconstructed = ''.join(data['tiles'][i] for i in data['tutorial_order'])
        self.assertEqual(reconstructed, 'つけめん')

        # Step 7: the guided (guaranteed-correct) submission is what sets
        # the one-time cookie.
        submit_response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({
                'tiles': [data['tiles'][i] for i in data['tutorial_order']],
                'seconds_left': 60,
            }),
            content_type='application/json',
        )
        submit_data = json.loads(submit_response.content)
        self.assertTrue(submit_data['is_correct'])
        self.assertEqual(submit_response.cookies['tj_nav_tutorial_seen'].value, '1')

    # --- 3. safe fallback if つけ麺 is ever removed ---

    def test_fallback_when_tsukemen_word_is_missing(self):
        self.tsukemen.delete()
        self.client.cookies['tj_has_played'] = '1'

        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertNotIn('id="nav-tutorial-target-guide"', cook_html)

        # mode=single for the now-nonexistent slug 404s normally, rather
        # than silently activating the guide for some other word.
        response = self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG,
        })
        self.assertEqual(response.status_code, 404)

    # --- 4. the generic "Tap!" guide's slot is swapped to the target card,
    # never shown alongside it (2026-09-19: simplified from the original
    # "suppress only when they coincide" design after the two showing on
    # different cards at once was reported as confusing) ---

    def test_no_double_tap_guide_when_target_word_is_also_the_first_card(self):
        # setUp() created only つけめん so far - it's necessarily
        # forloop.first in the cook queryset (ordered by -timestamp).
        self.client.cookies['tj_has_played'] = '1'
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertEqual(cook_html.count('id="first-card-guide"'), 0)
        self.assertEqual(cook_html.count('id="nav-tutorial-target-guide"'), 1)

    def test_generic_tap_guide_is_swapped_away_even_when_it_is_a_different_card(self):
        # A newer word becomes forloop.first instead - for an eligible
        # visitor the generic guide must NOT show on it either; only the
        # つけめん card gets a guide, no matter which card is actually first.
        ThankJapanModel.objects.create(
            name='newer-cook-word', englishname='Newer', jpname='てんぷら',
            kanji_name='天ぷら', katakana_name='テンプラ', category='cook',
            slug='cook-newer-word', description='d', history='h',
        )
        self.client.cookies['tj_has_played'] = '1'
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertEqual(cook_html.count('id="first-card-guide"'), 0)
        self.assertEqual(cook_html.count('id="nav-tutorial-target-guide"'), 1)

    def test_not_eligible_guest_gets_the_plain_generic_guide_only(self):
        # No tj_has_played cookie at all - not a returning guest, so none of
        # the nav-tutorial markup should appear, only the pre-existing
        # generic first-card guide.
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertEqual(cook_html.count('id="first-card-guide"'), 1)
        self.assertNotIn('id="nav-tutorial-target-guide"', cook_html)

    def test_eligible_guest_gets_no_generic_guide_at_all_if_target_word_missing(self):
        # Swapped-away, not suppressed-then-restored: even with つけ麺 gone
        # (see the fallback test above), an eligible visitor still gets no
        # generic guide on whatever card is now first - the slot stays
        # swapped, it doesn't fall back to the plain behavior.
        self.tsukemen.delete()
        self.client.cookies['tj_has_played'] = '1'
        cook_html = self.client.get(reverse('cookpage')).content.decode()
        self.assertEqual(cook_html.count('id="first-card-guide"'), 0)
        self.assertNotIn('id="nav-tutorial-target-guide"', cook_html)

    def test_culture_page_hides_its_own_generic_guide_for_an_eligible_visitor(self):
        # Culture isn't on the nav-tutorial's own guided path, but an
        # eligible visitor browsing there too shouldn't see its unrelated
        # "first card" tap-guide competing with the ongoing tutorial -
        # simple suppression, no retargeting (nothing on this page is being
        # taught).
        ThankJapanModel.objects.create(
            name='culture-word-1', englishname='Culture Word', jpname='しょっき',
            kanji_name='織機', katakana_name='ショッキ', category='culture',
            slug='culture-word-1', description='d', history='h',
        )
        self.client.cookies['tj_has_played'] = '1'
        culture_html = self.client.get(reverse('culturepage')).content.decode()
        self.assertNotIn('id="first-card-guide"', culture_html)

    def test_culture_page_still_shows_its_generic_guide_for_a_normal_visitor(self):
        ThankJapanModel.objects.create(
            name='culture-word-2', englishname='Culture Word', jpname='しょっき',
            kanji_name='織機', katakana_name='ショッキ', category='culture',
            slug='culture-word-2', description='d', history='h',
        )
        culture_html = self.client.get(reverse('culturepage')).content.decode()
        self.assertIn('id="first-card-guide"', culture_html)

    # --- 5. cookie set only on completion, making it one-time-only ---

    def test_cookie_not_set_until_the_guided_round_is_actually_completed(self):
        self.client.cookies['tj_has_played'] = '1'
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG,
        })
        # Merely starting the guided round must not itself set the cookie -
        # see the "set on completion, not on start" decision (unlike
        # tj_has_played), so a guest who drops off mid-flow still gets
        # offered the tutorial again on a later visit.
        restart_response = self.client.get(reverse('game_play'))
        self.assertNotIn('tj_nav_tutorial_seen', restart_response.cookies)

    def test_cookie_makes_it_one_time_only(self):
        self.client.cookies['tj_has_played'] = '1'
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG,
        })
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        submit_response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({
                'tiles': [data['tiles'][i] for i in data['tutorial_order']],
                'seconds_left': 60,
            }),
            content_type='application/json',
        )
        self.client.cookies['tj_nav_tutorial_seen'] = submit_response.cookies['tj_nav_tutorial_seen'].value

        top_response = self.client.get(reverse('toppage'))
        self.assertFalse(top_response.context['nav_tutorial_learn_step_active'])

    def test_cookie_not_set_for_ordinary_single_mode_practice_on_another_word(self):
        # The "practice this word" button is the same generic single-mode
        # link on every word's detail page - practicing anything other than
        # つけめん must never set the nav-tutorial cookie.
        other_word = ThankJapanModel.objects.create(
            name='other-word', englishname='Cat', jpname='ねこ',
            kanji_name='猫', katakana_name='ネコ', category='animal',
            slug='other-word-slug', description='d', history='h',
        )
        self.client.cookies['tj_has_played'] = '1'
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': other_word.slug,
        })
        session = self.client.session
        self.assertNotIn('nav_tutorial_single_active', session)

        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNone(data.get('tutorial_order'))
        submit_response = self.client.post(
            reverse('game_submit_answer'),
            data=json.dumps({'tiles': data['tiles'], 'seconds_left': 60}),
            content_type='application/json',
        )
        self.assertNotIn('tj_nav_tutorial_seen', submit_response.cookies)

    def test_not_activated_for_a_logged_in_user_even_with_matching_slug(self):
        User.objects.create_user(username='nav_tutorial_single_login_test', password='pass12345')
        self.client.login(username='nav_tutorial_single_login_test', password='pass12345')
        self.client.cookies['tj_has_played'] = '1'
        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG,
        })
        session = self.client.session
        self.assertNotIn('nav_tutorial_single_active', session)

    # --- 6. 15-language coverage: every gated page renders cleanly in all
    # of them, the same crash-catching pattern as
    # test_renders_without_error_in_all_15_locales elsewhere in this file ---

    def test_renders_without_error_in_all_15_locales(self):
        self.client.cookies['tj_has_played'] = '1'
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang, page='top'):
                response = self.client.get(reverse('toppage'), {'lang': lang})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'tutorial-overlay-nav-learn')
                self.assertContains(response, 'tutorial-overlay-nav-category-link')
            with self.subTest(lang=lang, page='category_list'):
                response = self.client.get(reverse('category_list'), {'lang': lang})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'tutorial-overlay-nav-cook-card')
                self.assertNotContains(response, 'nav-tutorial-learn-hint')
            with self.subTest(lang=lang, page='cook'):
                response = self.client.get(reverse('cookpage'), {'lang': lang})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="nav-tutorial-target-guide"')
                self.assertContains(response, 'id="tutorial-overlay-cook-modal-open"')

        self.client.get(reverse('game_restart'), {
            'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG,
        })
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang, page='game_play'):
                response = self.client.get(reverse('game_play'), {'lang': lang})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'tutorial-overlay-nav-single')


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
        # mode=single is untimed the same way first_taste is (see
        # skip_countdown/has_timer in game_play), and - unlike first_taste -
        # isn't restricted to logged-out first-time guests (2026-09-17: the
        # tutorial mechanics must only ever fire once for a genuinely new
        # guest, so game_restart now downgrades a logged-in user's
        # ?difficulty=first_taste request to a normal timed game instead;
        # this test's own self.user would no longer land in first_taste at
        # all, so mode=single exercises the same "untimed mode rejects
        # extend-time" behavior without relying on that now-closed path).
        self.user.profile.total_score = 100
        self.user.profile.save()
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': 'extend-ep-word-0'})
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


class SiteVisitCounterTests(TestCase):
    """The header's all-time visitor badge (2026-09-23). Deliberately the
    same 1-row-per-day running-total pattern as DailyActivitySummary (see
    DailyActivitySummaryTests above) - record_visit() must never grow the
    table by more than one row per calendar date no matter how many
    requests land that day."""

    def setUp(self):
        from django.core.cache import cache
        cache.delete('site_visit_cumulative_total')

    def test_record_visit_creates_one_row_for_the_day(self):
        SiteVisitCounter.record_visit()

        self.assertEqual(SiteVisitCounter.objects.count(), 1)
        row = SiteVisitCounter.objects.get()
        self.assertEqual(row.date, timezone.localdate())
        self.assertEqual(row.visit_count, 1)

    def test_repeated_visits_same_day_increment_one_row_not_append_rows(self):
        for _ in range(25):
            SiteVisitCounter.record_visit()

        self.assertEqual(SiteVisitCounter.objects.count(), 1)
        self.assertEqual(SiteVisitCounter.objects.get().visit_count, 25)

    def test_cumulative_total_sums_across_days(self):
        today = timezone.localdate()
        SiteVisitCounter.objects.create(date=today, visit_count=10)
        SiteVisitCounter.objects.create(date=today - timedelta(days=1), visit_count=7)
        SiteVisitCounter.objects.create(date=today - timedelta(days=400), visit_count=3)

        self.assertEqual(SiteVisitCounter.cumulative_total(), 20)

    def test_cumulative_total_is_zero_with_no_rows(self):
        self.assertEqual(SiteVisitCounter.cumulative_total(), 0)


class VisitCounterMiddlewareTests(TestCase):
    """Exercises VisitCounterMiddleware (thank_japan_app/middleware.py)
    through real requests rather than calling it directly, since its whole
    point is deciding which real responses qualify. secure=True is required
    on every call here - SECURE_SSL_REDIRECT=True (settings.py) 301s any
    plain-HTTP test-client request before it ever reaches a real view."""

    def test_real_page_view_increments_todays_row(self):
        self.client.get(reverse('toppage'), secure=True)

        self.assertEqual(SiteVisitCounter.objects.count(), 1)
        self.assertEqual(SiteVisitCounter.objects.get().visit_count, 1)

    def test_several_page_views_same_day_stay_one_row(self):
        for _ in range(5):
            self.client.get(reverse('toppage'), secure=True)
            self.client.get(reverse('game_start'), secure=True)

        self.assertEqual(SiteVisitCounter.objects.count(), 1)
        self.assertEqual(SiteVisitCounter.objects.get().visit_count, 10)

    def test_admin_login_path_is_excluded(self):
        self.client.get('/admin/login/', secure=True)

        self.assertEqual(SiteVisitCounter.objects.count(), 0)


class VisitorCountHeaderBadgeTests(TestCase):
    """The header badge (templates/includes/visitor_count_badge.html,
    included from base_top.html/basecategory_top.html) must render the
    cached cumulative total on an ordinary page, in all 15 locales."""

    def setUp(self):
        SiteVisitCounter.objects.create(date=timezone.localdate(), visit_count=12345)
        from django.core.cache import cache
        cache.delete('site_visit_cumulative_total')

    def test_badge_shows_formatted_cumulative_total_on_toppage(self):
        response = self.client.get(reverse('toppage'), secure=True)
        body = response.content.decode()

        self.assertIn('header-visitor-badge', body)
        self.assertIn('👥 12,345', body)

    def test_badge_present_across_all_15_locales(self):
        locale_full_text = {
            'ja': '人がThankJapanを訪れました', 'zh-hant': '人造訪了ThankJapan',
            'zh-cn': '人访问了ThankJapan', 'ko': '명이 ThankJapan을 방문했습니다',
            'de': 'Personen haben ThankJapan besucht', 'fr': 'personnes ont visité ThankJapan',
            'es-es': 'personas han visitado ThankJapan', 'es-mx': 'personas han visitado ThankJapan',
            'it': 'persone hanno visitato ThankJapan', 'pt': 'pessoas visitaram o ThankJapan',
            'pt-br': 'pessoas visitaram o ThankJapan', 'vi': 'người truy cập ThankJapan',
            'th': 'มีผู้เข้าชม ThankJapan แล้ว', 'en-in': 'people have visited ThankJapan',
            'en': 'people have visited ThankJapan',
        }
        for lang, expected_fragment in locale_full_text.items():
            with self.subTest(lang=lang):
                response = self.client.get(reverse('toppage'), {'lang': lang}, secure=True)
                body = response.content.decode()
                self.assertIn('👥 12,345', body)
                self.assertIn(expected_fragment, body)


class RelatedWordsCardEnhancementTests(TestCase):
    """The "related words" card grid on both word-detail pages (free and
    premium) - bigger image, a one-line "✨ Recommended" hook badge overlaid
    on the image, and a light hover/tap animation (2026-09-23). Related
    items need a real image for the hook badge/enlarged image to render at
    all (both live inside `{% if item.image %}`), so these tests exercise
    the same real Cloudinary-URL code path as the pre-existing LCP tests in
    WordDetailPageTests above."""

    def setUp(self):
        self.word = ThankJapanModel.objects.create(
            name='related-main-word', englishname='Main Word', jpname='メイン',
            category='RelatedTestCategory', slug='related-main-word',
            description='d', history='h',
        )
        self.related_word = ThankJapanModel.objects.create(
            name='related-sibling-word', englishname='Sibling Word', jpname='きょうだい',
            category='RelatedTestCategory', slug='related-sibling-word',
            description='d', history='h', image='thankjapan/images/sample',
        )
        self.premium_word = ThankJapanPremium.objects.create(
            name='related-main-premium', englishname='Main Premium', jpname='メインプレミアム',
            romaji='r', category='RelatedTestCategory', slug='related-main-premium',
            description='d', history='h',
        )
        self.related_premium_word = ThankJapanPremium.objects.create(
            name='related-sibling-premium', englishname='Sibling Premium', jpname='きょうだいプレミアム',
            romaji='r', category='RelatedTestCategory', slug='related-sibling-premium',
            description='d', history='h', image='thankjapan/premium/sample',
        )

    def _get_free(self, lang=None):
        params = {'lang': lang} if lang else {}
        return self.client.get(reverse('category_detail', kwargs={
            'category': 'RelatedTestCategory', 'slug': 'related-main-word',
        }), params)

    def _get_premium(self, lang=None):
        params = {'lang': lang} if lang else {}
        return self.client.get(reverse('detail_premium', kwargs={
            'category': 'RelatedTestCategory', 'slug': 'related-main-premium',
        }), params)

    def test_free_page_related_card_shows_enlarged_image_css(self):
        body = self._get_free().content.decode()

        self.assertIn('height: 100px !important', body)

    def test_free_page_related_card_shows_hook_badge(self):
        body = self._get_free().content.decode()

        self.assertIn('related-card-hook', body)
        self.assertIn('✨ Recommended', body)

    def test_free_page_related_card_has_hover_and_tap_animation(self):
        body = self._get_free().content.decode()

        self.assertIn('related-card-item:hover', body)
        self.assertIn('translateY(-3px) scale(1.03)', body)
        self.assertIn('related-card-item:active', body)
        self.assertIn('scale(0.96)', body)

    def test_premium_page_related_card_shows_enlarged_image_css(self):
        body = self._get_premium().content.decode()

        self.assertIn('height: 100px !important', body)

    def test_premium_page_related_card_shows_hook_badge(self):
        body = self._get_premium().content.decode()

        self.assertIn('related-card-hook', body)
        self.assertIn('✨ Recommended', body)

    def test_premium_page_related_card_has_hover_and_tap_animation(self):
        body = self._get_premium().content.decode()

        self.assertIn('related-card-item:hover', body)
        self.assertIn('translateY(-3px) scale(1.03)', body)
        self.assertIn('related-card-item:active', body)
        self.assertIn('scale(0.96)', body)

    def test_hook_badge_present_across_all_15_locales(self):
        locale_hook_text = {
            'ja': '✨ おすすめ', 'zh-hant': '✨ 推薦', 'zh-cn': '✨ 推荐', 'ko': '✨ 추천',
            'de': '✨ Empfehlung', 'fr': '✨ Recommandé', 'es-es': '✨ Recomendado',
            'es-mx': '✨ Recomendado', 'it': '✨ Consigliato', 'pt': '✨ Recomendado',
            'pt-br': '✨ Recomendado', 'vi': '✨ Đề xuất', 'th': '✨ แนะนำ',
            'en': '✨ Recommended', 'en-in': '✨ Recommended',
        }
        for lang, expected_text in locale_hook_text.items():
            with self.subTest(lang=lang, page='free'):
                body = self._get_free(lang).content.decode()
                self.assertIn(expected_text, body)
            with self.subTest(lang=lang, page='premium'):
                body = self._get_premium(lang).content.decode()
                self.assertIn(expected_text, body)


# ---------------------------------------------------------------------------
# 2026-11-19: 3-choice question format (views.GAME_QUESTION_FORMAT = 'choice')
# ---------------------------------------------------------------------------

# Expected 3-choice wording, transcribed per locale from the templates.
CHOICE_TUTORIAL_Q1_BY_LOCALE = {
    'ja': '正しい答えを<br>タップしてみよう', 'zh-hant': '正確答案吧', 'zh-cn': '正确答案吧',
    'ko': '정답을 탭해 봐요', 'de': 'Tippe auf die richtige', 'fr': 'Touchez la bonne réponse',
    'es-es': 'Toca la respuesta correcta', 'es-mx': 'Toca la respuesta correcta',
    'it': 'Tocca la risposta corretta', 'pt': 'Toque na resposta correta', 'pt-br': 'Toque na resposta correta',
    'vi': 'Hãy chạm vào đáp án đúng', 'th': 'แตะคำตอบที่ถูกต้อง',
    'en': 'Tap the correct answer', 'en-in': 'Tap the correct answer',
}
CHOICE_TUTORIAL_Q2_BY_LOCALE = {
    'ja': '違う答えを選んでみよう', 'zh-hant': '選一個錯誤的答案吧', 'zh-cn': '选一个错误的答案吧',
    'ko': '틀린 답을 선택해 봐요', 'de': 'absichtlich eine falsche Antwort', 'fr': 'volontairement une mauvaise réponse',
    'es-es': 'a propósito una respuesta incorrecta', 'es-mx': 'a propósito una respuesta incorrecta',
    'it': 'di proposito una risposta sbagliata', 'pt': 'de propósito uma resposta errada', 'pt-br': 'de propósito uma resposta errada',
    'vi': 'chọn một đáp án sai nhé', 'th': 'คำตอบที่ผิดดูสิ',
    'en': 'wrong answer on purpose', 'en-in': 'wrong answer on purpose',
}
OLD_TILE_TUTORIAL_FRAGMENTS = [
    '1文字ずつタップして', '一個字一個字地點擊', '一个字一个字地点击', '한 글자씩 탭해서', 'nacheinander an',
    'un par un', 'uno por uno', 'uno alla volta', 'um por um', 'theo thứ tự', 'ทีละตัว', 'one by one',
    'わざと<br>違う順番', 'タイルが緑', 'タイルが赤', 'the tile glows',
]
DAILY_CHOICE_INSTRUCTION_BY_LOCALE = {
    'ja': '正しい意味をタップしてください', 'zh-hant': '請點擊正確的意思', 'zh-cn': '请点击正确的意思',
    'ko': '올바른 뜻을 탭하세요', 'de': 'Tippe auf die richtige Bedeutung', 'fr': 'Touchez la bonne signification',
    'es-es': 'Toca el significado correcto', 'es-mx': 'Toca el significado correcto',
    'it': 'Tocca il significato corretto', 'pt': 'Toque no significado correto', 'pt-br': 'Toque no significado correto',
    'vi': 'Hãy chạm vào nghĩa đúng', 'th': 'แตะความหมายที่ถูกต้อง',
    'en': 'Tap the correct meaning', 'en-in': 'Tap the correct meaning',
}
GAME_START_RULE_BY_LOCALE = {
    'ja': '正しい意味を3つの中から選ぼう', 'zh-hant': '從3個選項中選出正確的意思', 'zh-cn': '从3个选项中选出正确的意思',
    'ko': '3개 중에서 올바른 뜻을 고르세요', 'de': 'wähle aus 3 Antworten die richtige Bedeutung',
    'fr': 'choisis la bonne signification parmi 3', 'es-es': 'elige el significado correcto entre 3 opciones',
    'es-mx': 'elige el significado correcto entre 3 opciones', 'it': 'scegli il significato giusto tra 3 opzioni',
    'pt': 'escolha o significado certo entre 3 opções', 'pt-br': 'escolha o significado certo entre 3 opções',
    'vi': 'chọn nghĩa đúng trong 3 đáp án', 'th': 'เลือกความหมายที่ถูกต้องจาก 3 ตัวเลือก',
    'en': 'pick the right meaning from 3 choices', 'en-in': 'pick the right meaning from 3 choices',
}
GAME_START_META_BY_LOCALE = {
    'ja': '3つから意味を選ぶだけ', 'zh-hant': '看圖三選一', 'zh-cn': '看图三选一', 'ko': '3개 중 뜻만 고르면 끝',
    'de': 'aus 3 Bedeutungen wählen', 'fr': 'choisis le bon sens parmi 3', 'es-es': 'elige el significado entre 3',
    'es-mx': 'elige el significado entre 3', 'it': 'scegli il significato tra 3', 'pt': 'escolhe o significado entre 3',
    'pt-br': 'escolha o significado entre 3', 'vi': 'chọn 1 trong 3 nghĩa', 'th': 'เลือกความหมายจาก 3 ตัวเลือก',
    'en': 'pick the right meaning from 3', 'en-in': 'pick the right meaning from 3',
}
OLD_GAME_START_TILE_FRAGMENTS = [
    '文字を並べる', 'バラバラのタイル', '看圖排字', '看图排字', '글자만 맞추면', 'remets les lettres', 'Buchstaben ordnen',
    'ordena las letras', 'ordina le lettere', 'organiza as letras', 'organize as letras', 'sắp chữ', 'เรียงตัวอักษร',
    'arrange the letters', 'scrambled tiles', 'durcheinandergewürfelten Kacheln', 'tuiles mélangées',
    'fichas mezcladas', 'tessere mescolate', 'peças embaralhadas', 'ô chữ xáo trộn', 'ตัวต่อที่สลับกัน', '흩어진 타일', '打亂的方塊', '打乱的方块',
]
YOUR_ANSWER_LABEL_BY_LOCALE = {
    'ja': 'あなたの回答:', 'zh-hant': '你的回答:', 'zh-cn': '你的回答:', 'ko': '나의 답:', 'de': 'Deine Antwort:',
    'fr': 'Votre réponse :', 'es-es': 'Tu respuesta:', 'es-mx': 'Tu respuesta:', 'it': 'La tua risposta:',
    'pt': 'Sua resposta:', 'pt-br': 'Sua resposta:', 'vi': 'Câu trả lời của bạn:', 'th': 'คำตอบของคุณ:',
    'en': 'Your answer:', 'en-in': 'Your answer:',
}
OLD_YOUR_INPUT_LABELS = ['あなたが入力した', '你輸入了', '你输入了', '입력한 내용', 'Du hast eingegeben', 'Vous avez tapé',
                         'Escribiste', 'Hai digitato', 'Você digitou', 'Bạn đã nhập', 'คุณพิมพ์', 'You typed']
DAILY_TUTORIAL_END_FRAGMENTS = ['「今日の一問」にも', '「今日一題」吧', '「今日一题」吧', '오늘의 한 문제에도', 'die Frage des Tages aus',
                                'la question du jour !', 'la pregunta de hoy', 'la domanda di oggi', 'a pergunta de hoje',
                                'câu hỏi hôm nay nhé', 'คำถามประจำวันนี้ด้วยนะ', "Daily Question too"]

MEANING_COLUMNS = {
    'en': 'englishname', 'en-in': 'englishname_en_in', 'ja': 'englishname_ja', 'zh-cn': 'englishname_zh_cn',
    'zh-hant': 'englishname_zh_hant', 'ko': 'englishname_ko', 'fr': 'englishname_fr', 'de': 'englishname_de',
    'it': 'englishname_it', 'es-es': 'englishname_es_es', 'es-mx': 'englishname_es_mx', 'pt': 'englishname_pt',
    'pt-br': 'englishname_pt_br', 'th': 'englishname_th', 'vi': 'englishname_vi',
}


def _make_word(slug, english, jpname, category='food', name=None, **extra):
    """A free word with a distinct, locale-tagged meaning in every column
    (e.g. 'Tomato [ko]'), so tests can tell which column a choice came from."""
    fields = {col: f'{english} [{lang}]' for lang, col in MEANING_COLUMNS.items() if col != 'englishname'}
    fields.update(extra)
    return ThankJapanModel.objects.create(
        name=name or slug, englishname=english, jpname=jpname, category=category,
        slug=slug, description='d', history='h', **fields,
    )


def _start_session_game(client, words, difficulty='timeattack'):
    session = client.session
    session['game_question_ids'] = [{'id': w.id, 'model': 'free'} for w in words]
    session['game_current_index'] = 0
    session['game_score'] = 0
    session['game_difficulty'] = difficulty
    session['game_history'] = []
    session.save()


def _submit_choice(client, choice_index):
    return client.post(reverse('game_submit_answer'), data=json.dumps({'choice_index': choice_index}),
                       content_type='application/json')


class ThreeChoiceFormatIsLiveTests(SimpleTestCase):
    def test_choice_is_the_active_format(self):
        self.assertEqual(GAME_QUESTION_FORMAT, QUESTION_FORMAT_CHOICE)


class ThreeChoiceBuildChoicesTests(TestCase):
    def setUp(self):
        self.tomato = _make_word('c-tomato', 'Tomato', 'とまと', name='tomato')
        self.food = [
            _make_word('c-apple', 'Apple', 'りんご'),
            _make_word('c-rice', 'Rice', 'ごはん'),
            _make_word('c-egg', 'Egg', 'たまご'),
            _make_word('c-fish', 'Fish', 'さかな'),
        ]
        self.other_cat = _make_word('c-dog', 'Dog', 'いぬ', category='animal')

    def test_three_distinct_choices_with_exactly_one_correct(self):
        built = _build_choices(self.tomato, 'en')
        self.assertEqual(len(built['choices']), 3)
        self.assertEqual(built['answer_text'], 'Tomato')
        self.assertEqual(built['choices'][built['answer_index']], 'Tomato')
        self.assertEqual(len({_meaning_key(c) for c in built['choices']}), 3)
        self.assertEqual(sum(1 for c in built['choices'] if c == 'Tomato'), 1)

    def test_dummies_come_from_the_same_category_when_available(self):
        same_category = {'Apple', 'Rice', 'Egg', 'Fish'}
        for _ in range(20):
            built = _build_choices(self.tomato, 'en')
            dummies = [c for c in built['choices'] if c != 'Tomato']
            self.assertTrue(set(dummies) <= same_category, dummies)

    def test_tops_up_from_other_categories_when_the_category_is_too_small(self):
        lonely = _make_word('c-shrine', 'Shrine', 'じんじゃ', category='building')
        built = _build_choices(lonely, 'en')
        self.assertEqual(len(built['choices']), 3)
        self.assertIn('Shrine', built['choices'])

    def test_same_meaning_as_the_answer_is_never_a_dummy(self):
        _make_word('c-tomato2', '  tomato ', 'とまと2')  # same meaning, different case/spacing
        for _ in range(20):
            built = _build_choices(self.tomato, 'en')
            self.assertEqual(sum(1 for c in built['choices'] if _meaning_key(c) == 'tomato'), 1)

    def test_choices_use_the_display_language_column(self):
        for lang, col in MEANING_COLUMNS.items():
            with self.subTest(lang=lang):
                built = _build_choices(self.tomato, lang)
                self.assertEqual(built['answer_text'], getattr(self.tomato, col))
                for choice in built['choices']:
                    if lang != 'en':
                        self.assertTrue(choice.endswith(f'[{lang}]'), choice)

    def test_blank_translation_falls_back_to_english_for_the_whole_question(self):
        blank = _make_word('c-blank', 'Bread', 'ぱん', englishname_fr='')
        built = _build_choices(blank, 'fr')
        self.assertEqual(built['answer_text'], 'Bread')
        for choice in built['choices']:
            self.assertNotIn('[fr]', choice)

    def test_correct_position_is_randomized(self):
        positions = {_build_choices(self.tomato, 'en')['answer_index'] for _ in range(60)}
        self.assertEqual(positions, {0, 1, 2})

    def test_correct_position_can_be_pinned(self):
        for _ in range(10):
            built = _build_choices(self.tomato, 'en', correct_position=0)
            self.assertEqual(built['answer_index'], 0)
            self.assertEqual(built['choices'][0], 'Tomato')


class ThreeChoiceGameFlowTests(TestCase):
    def setUp(self):
        self.words = [
            _make_word('g-tomato', 'Tomato', 'とまと', name='tomato'),
            _make_word('g-apple', 'Apple', 'りんご', name='ringo'),
            _make_word('g-rice', 'Rice', 'ごはん', name='gohan'),
            _make_word('g-egg', 'Egg', 'たまご', name='tamago'),
        ]

    def _question(self):
        return json.loads(self.client.get(reverse('game_question_data')).content)

    def test_question_data_serves_choices_and_reading_but_never_the_answer(self):
        _start_session_game(self.client, self.words)
        data = self._question()
        self.assertEqual(data['format'], 'choice')
        self.assertEqual(len(data['choices']), 3)
        self.assertIn('Tomato', data['choices'])
        self.assertEqual(data['reading'], 'とまと')
        self.assertEqual(data['romaji'], 'tomato')
        self.assertIsNone(data['tutorial_choice_index'])
        self.assertIsNone(data['tutorial_wrong_choice_index'])
        for key in ('caption', 'tiles', 'answer_index', 'tutorial_order'):
            self.assertNotIn(key, data)

    def test_correct_choice_scores_and_is_recorded(self):
        _start_session_game(self.client, self.words)
        data = self._question()
        res = json.loads(_submit_choice(self.client, data['choices'].index('Tomato')).content)
        self.assertTrue(res['is_correct'])
        self.assertEqual(res['points'], 1)
        self.assertEqual(self.client.session['game_current_index'], 1)
        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual((row.total_count, row.correct_count, row.guest_count), (1, 1, 1))

    def test_wrong_choice_reveals_which_choice_was_correct(self):
        _start_session_game(self.client, self.words)
        data = self._question()
        wrong = next(i for i, c in enumerate(data['choices']) if c != 'Tomato')
        res = json.loads(_submit_choice(self.client, wrong).content)
        self.assertFalse(res['is_correct'])
        self.assertEqual(res['correct_answer'], 'Tomato')
        self.assertEqual(data['choices'][res['correct_choice_index']], 'Tomato')
        self.assertEqual(self.client.session['game_history'][0]['user_input'], data['choices'][wrong])
        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual((row.total_count, row.correct_count), (1, 0))

    def test_invalid_choice_index_is_rejected_without_advancing(self):
        _start_session_game(self.client, self.words)
        self._question()
        for bad in (3, -1, '0', True, None, 1.5):
            with self.subTest(bad=bad):
                response = _submit_choice(self.client, bad)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session['game_current_index'], 0)
        self.assertFalse(DailyActivitySummary.objects.filter(total_count__gt=0).exists())

    def test_non_object_body_is_rejected(self):
        _start_session_game(self.client, self.words)
        self._question()
        response = self.client.post(reverse('game_submit_answer'), data='[1]', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_submit_without_the_question_being_served_is_a_miss_not_a_crash(self):
        _start_session_game(self.client, self.words)
        res = json.loads(_submit_choice(self.client, 0).content)
        self.assertFalse(res['is_correct'])
        self.assertEqual(res['correct_answer'], 'Tomato')

    def test_resubmitting_the_same_question_does_not_double_count(self):
        _start_session_game(self.client, self.words)
        data = self._question()
        idx = data['choices'].index('Tomato')
        _submit_choice(self.client, idx)
        session = self.client.session
        session['game_current_index'] = 0  # simulate a replayed request for the same index
        session.save()
        res = json.loads(_submit_choice(self.client, idx).content)
        self.assertTrue(res['is_correct'])
        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual(row.total_count, 1)

    def test_choices_follow_the_session_display_language(self):
        self.client.get(reverse('game_restart'), {'lang': 'ko'})
        _start_session_game(self.client, self.words)
        data = self._question()
        self.assertIn('Tomato [ko]', data['choices'])

    def test_game_play_renders_the_choice_board_not_the_tile_board(self):
        _start_session_game(self.client, self.words)
        body = self.client.get(reverse('game_play')).content.decode()
        self.assertIn('id="choice-list"', body)
        self.assertIn('id="prompt-reading"', body)
        self.assertIn('id="prompt-romaji"', body)
        self.assertNotIn('id="bank-row"', body)
        self.assertNotIn('id="prompt-caption"', body)

    def test_game_play_choice_buttons_are_large_and_stacked(self):
        _start_session_game(self.client, self.words)
        body = self.client.get(reverse('game_play')).content.decode()
        self.assertIn('flex-direction: column; gap: 14px', body)
        self.assertIn('min-height: 64px', body)
        self.assertIn('.prompt-reading {', body)
        self.assertIn('font-size: 1.9rem; font-weight: 900', body)

    def test_single_mode_correct_answer_records_learned_word_for_member(self):
        user = User.objects.create_user('choice-reviewer', password='pw-for-tests-only')
        self.client.force_login(user)
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': 'g-tomato'})
        data = self._question()
        res = json.loads(_submit_choice(self.client, data['choices'].index('Tomato')).content)
        self.assertTrue(res['is_correct'])
        self.assertTrue(WordReviewLog.objects.filter(user=user, word=self.words[0]).exists())
        # single mode is a review, not "playing the game": no main_game counters
        self.assertFalse(DailyActivitySummary.objects.exists())

    def test_single_mode_wrong_answer_records_nothing(self):
        user = User.objects.create_user('choice-reviewer2', password='pw-for-tests-only')
        self.client.force_login(user)
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': 'g-tomato'})
        data = self._question()
        wrong = next(i for i, c in enumerate(data['choices']) if c != 'Tomato')
        res = json.loads(_submit_choice(self.client, wrong).content)
        self.assertFalse(res['is_correct'])
        self.assertFalse(WordReviewLog.objects.exists())


class ThreeChoiceTutorialTests(TestCase):
    def setUp(self):
        self.ramen = _make_word('t-ramen', 'Ramen', 'らーめん', category='food', name='ramen')
        self.inu = _make_word('t-inu', 'Dog', 'いぬ', category='animal', name='inu')
        for i, (en, jp) in enumerate([('Cat', 'ねこ'), ('Bird', 'とり'), ('Bear', 'くま'), ('Fox', 'きつね')]):
            _make_word(f't-animal-{i}', en, jp, category='animal')
        for i, (en, jp) in enumerate([('Sushi', 'すし'), ('Tofu', 'とうふ')]):
            _make_word(f't-food-{i}', en, jp, category='food')
        self.tsukemen = _make_word(NAV_TUTORIAL_TARGET_SLUG, 'Tsukemen', 'つけめん', category='cook', name='tsukemen')
        _make_word('t-cook-1', 'Grill', 'やく', category='cook')
        _make_word('t-cook-2', 'Boil', 'ゆでる', category='cook')

    def _question(self, client):
        return json.loads(client.get(reverse('game_question_data')).content)

    def test_first_taste_q1_pins_the_correct_answer_first_and_guides_to_it(self):
        for _ in range(8):
            client = Client()
            client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
            data = self._question(client)
            self.assertEqual(data['reading'], 'らーめん')
            self.assertEqual(data['choices'][0], 'Ramen')
            self.assertEqual(data['tutorial_choice_index'], 0)
            self.assertIsNone(data['tutorial_wrong_choice_index'])

    def test_first_taste_q2_guides_to_a_wrong_choice_and_q3_is_unguided(self):
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        q1 = self._question(self.client)
        self.assertTrue(json.loads(_submit_choice(self.client, q1['tutorial_choice_index']).content)['is_correct'])

        q2 = self._question(self.client)
        self.assertEqual(q2['reading'], 'いぬ')
        self.assertIsNone(q2['tutorial_choice_index'])
        guided = q2['tutorial_wrong_choice_index']
        self.assertIsNotNone(guided)
        self.assertNotEqual(q2['choices'][guided], 'Dog')
        res = json.loads(_submit_choice(self.client, guided).content)
        self.assertFalse(res['is_correct'])
        self.assertEqual(q2['choices'][res['correct_choice_index']], 'Dog')

        q3 = self._question(self.client)
        self.assertIsNone(q3['tutorial_choice_index'])
        self.assertIsNone(q3['tutorial_wrong_choice_index'])

    def test_nav_tutorial_single_round_guides_to_the_correct_choice(self):
        self.client.cookies['tj_has_played'] = '1'
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG})
        data = self._question(self.client)
        self.assertEqual(data['reading'], 'つけめん')
        self.assertEqual(data['choices'][data['tutorial_choice_index']], 'Tsukemen')
        response = _submit_choice(self.client, data['tutorial_choice_index'])
        self.assertTrue(json.loads(response.content)['is_correct'])
        self.assertEqual(response.cookies['tj_nav_tutorial_seen'].value, '1')

    def test_ordinary_single_mode_has_no_guide(self):
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': 't-ramen'})
        data = self._question(self.client)
        self.assertIsNone(data['tutorial_choice_index'])

    def test_tutorial_overlays_use_the_3_choice_wording_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.get(reverse('game_restart'), {'difficulty': 'first_taste', 'lang': lang})
                body = client.get(reverse('game_play'), {'lang': lang}).content.decode()
                self.assertIn(CHOICE_TUTORIAL_Q1_BY_LOCALE[lang], body)
                self.assertIn(CHOICE_TUTORIAL_Q2_BY_LOCALE[lang], body)
                for old in OLD_TILE_TUTORIAL_FRAGMENTS:
                    self.assertNotIn(old, body)

    def test_nav_tutorial_overlay_uses_the_3_choice_wording_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.cookies['tj_has_played'] = '1'
                client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG, 'lang': lang})
                body = client.get(reverse('game_play'), {'lang': lang}).content.decode()
                self.assertIn('tutorial-overlay-nav-single', body)
                self.assertIn(CHOICE_TUTORIAL_Q1_BY_LOCALE[lang].replace('「らーめん」の<br>', ''), body)
                self.assertIn('つけめん', body)


class GameFunnelCountTests(TestCase):
    """games_started/games_completed: at most once per game, never on a
    reload or a revisit of the result screen."""

    def setUp(self):
        self.words = [_make_word(f'f-{i}', f'Word {i}', f'ことば{i}') for i in range(4)]

    def _row(self):
        return DailyActivitySummary.objects.filter(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME).first()

    def _start(self, difficulty='timeattack'):
        _start_session_game(self.client, self.words, difficulty=difficulty)
        session = self.client.session
        session.pop('game_start_counted', None)
        session.pop('game_completed_counted', None)
        session.save()

    def test_start_is_counted_once_even_when_the_question_is_reloaded(self):
        self._start()
        for _ in range(3):  # reloads re-fetch the same first question
            self.client.get(reverse('game_question_data'))
        self.assertEqual(self._row().games_started, 1)
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        _submit_choice(self.client, 0)
        self.client.get(reverse('game_question_data'))  # next question
        self.assertEqual(self._row().games_started, 1)
        self.assertEqual(self._row().games_completed, 0)

    def test_completion_is_counted_once_even_when_the_result_screen_is_reloaded(self):
        self._start()
        self.client.get(reverse('game_question_data'))
        _submit_choice(self.client, 0)
        for _ in range(3):
            self.client.get(reverse('game_result'))
        row = self._row()
        self.assertEqual((row.games_started, row.games_completed), (1, 1))
        self.assertEqual((row.first_taste_started, row.first_taste_completed), (0, 0))

    def test_result_screen_without_a_counted_start_is_not_a_completion(self):
        self._start()
        session = self.client.session
        session['game_history'] = [{'index': 0, 'is_correct': True, 'points': 1, 'question_id': self.words[0].id,
                                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                                    'user_input': 'x', 'correct_answer': 'x'}]
        session.save()
        self.client.get(reverse('game_result'))
        row = self._row()
        self.assertTrue(row is None or row.games_completed == 0)

    def test_first_taste_counts_in_both_the_total_and_its_own_columns(self):
        self._start(difficulty='first_taste')
        self.client.get(reverse('game_question_data'))
        self.client.get(reverse('game_result'))
        self.client.get(reverse('game_result'))
        row = self._row()
        self.assertEqual((row.games_started, row.games_completed), (1, 1))
        self.assertEqual((row.first_taste_started, row.first_taste_completed), (1, 1))

    def test_single_mode_review_is_not_counted(self):
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': 'f-0'})
        self.client.get(reverse('game_question_data'))
        self.client.get(reverse('game_result'))
        row = self._row()
        self.assertTrue(row is None or (row.games_started, row.games_completed) == (0, 0))

    def test_a_new_game_after_restart_counts_again(self):
        for _ in range(2):
            self.client.get(reverse('game_restart'))
            self.client.get(reverse('game_question_data'))
            self.client.get(reverse('game_result'))
        row = self._row()
        self.assertEqual((row.games_started, row.games_completed), (2, 2))

    def test_usage_report_prints_the_funnel(self):
        self._start()
        self.client.get(reverse('game_question_data'))
        out = StringIO()
        call_command('real_usage_report', stdout=out)
        self.assertIn('開始1 / 完了0', out.getvalue())


class ThreeChoiceDailyQuestionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.word = _make_word('d-umbrella', 'Umbrella', 'かさ', category='dailyactions', name='kasa', kanji_name='傘')
        for i, en in enumerate(['Clock', 'Chair', 'Pen']):
            _make_word(f'd-other-{i}', en, f'もの{i}', category='dailyactions')
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def _get(self, client, lang='en'):
        return client.get(reverse('daily_question'), {'lang': lang})

    def test_question_state_renders_three_choice_buttons_and_reading(self):
        response = self._get(self.client)
        self.assertEqual(response.context['question_format'], 'choice')
        choices = response.context['choices']
        self.assertEqual(len(choices), 3)
        self.assertIn('Umbrella', choices)
        body = response.content.decode()
        self.assertEqual(body.count('class="dq-choice"'), 3)
        self.assertIn('かさ', body)
        self.assertIn('kasa', body)
        self.assertIn('name="answer_choice"', body)

    def test_correct_choice_is_judged_correct_and_recorded(self):
        choices = self._get(self.client).context['choices']
        response = self.client.post(reverse('daily_question') + '?lang=en', {'answer_choice': choices.index('Umbrella')})
        self.assertTrue(response.context['is_correct'])
        self.assertEqual(response.context['correct_meaning'], 'Umbrella')
        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_DAILY_QUESTION)
        self.assertEqual((row.total_count, row.correct_count), (1, 1))

    def test_wrong_choice_and_timeout_are_misses(self):
        for posted in ('wrong', ''):
            with self.subTest(posted=posted or 'timeout'):
                client = Client()
                choices = self._get(client).context['choices']
                value = next(i for i, c in enumerate(choices) if c != 'Umbrella') if posted else ''
                response = client.post(reverse('daily_question') + '?lang=en', {'answer_choice': value})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['is_correct'])

    def test_post_without_seeing_the_question_is_a_miss(self):
        response = self.client.post(reverse('daily_question') + '?lang=en', {'answer_choice': '0'})
        self.assertFalse(response.context['is_correct'])

    def test_instruction_and_choices_are_localized_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = self._get(Client(), lang)
                self.assertIn(DAILY_CHOICE_INSTRUCTION_BY_LOCALE[lang], response.content.decode())
                expected = 'Umbrella' if lang == 'en' else f'Umbrella [{lang}]'
                self.assertIn(expected, response.context['choices'])

    def test_share_text_still_reveals_the_reading_after_a_choice_answer(self):
        choices = self._get(self.client).context['choices']
        response = self.client.post(reverse('daily_question') + '?lang=en', {'answer_choice': choices.index('Umbrella')})
        self.assertIn('か→さ→傘', response.context['share_text'])


class TopPageThreeChoiceCleanupTests(TestCase):
    def test_logged_in_top_page_has_only_the_centered_play_button(self):
        user = User.objects.create_user('top-page-user', password='pw-for-tests-only')
        self.client.force_login(user)
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self.client.get(reverse('toppage'), {'lang': lang}).content.decode()
                self.assertNotIn('class="explore-btn btn-red"', body)
                self.assertNotIn('攻略ガイド 📖', body)
                self.assertIn('hero-buttons hero-buttons-single', body)
                self.assertIn('explore-btn btn-green btn-play-main', body)

    def test_tutorial_end_message_no_longer_mentions_the_daily_question(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = Client().get(reverse('toppage'), {'lang': lang}).content.decode()
                self.assertIn('id="tutorial-overlay-end"', body)
                for fragment in DAILY_TUTORIAL_END_FRAGMENTS:
                    self.assertNotIn(fragment, body)

    def test_daily_question_banner_is_hidden(self):
        user = User.objects.create_user('banner-user', password='pw-for-tests-only')
        self.client.force_login(user)
        response = self.client.get(reverse('toppage'))
        self.assertFalse(response.context['show_daily_question_banner'])
        self.assertNotIn('id="dailyQuestionBanner"', response.content.decode())

    def test_banner_flag_restores_it(self):
        user = User.objects.create_user('banner-user2', password='pw-for-tests-only')
        self.client.force_login(user)
        with patch.object(context_processors, 'DAILY_QUESTION_BANNER_ENABLED', True):
            response = self.client.get(reverse('toppage'))
        self.assertTrue(response.context['show_daily_question_banner'])
        self.assertIn('id="dailyQuestionBanner"', response.content.decode())

    def test_daily_question_page_itself_still_works(self):
        word = _make_word('banner-word', 'Kite', 'たこ')
        DailyQuestion.objects.create(date=timezone.localdate(), word=word)
        response = self.client.get(reverse('daily_question'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['daily_state'], 'question')

    def test_returning_guest_still_gets_the_learn_nav_tutorial(self):
        self.client.cookies['tj_has_played'] = '1'
        body = self.client.get(reverse('toppage')).content.decode()
        self.assertIn('id="learn-modal-btn"', body)
        self.assertEqual(body.count('id="nav-tutorial-learn-hint"'), 1)
        self.assertEqual(body.count('id="nav-tutorial-category-link-hint"'), 1)


class ThreeChoiceTextPerLocaleTests(TestCase):
    def test_game_start_rules_and_meta_describe_the_3_choice_game(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self.client.get(reverse('game_start'), {'lang': lang}).content.decode()
                self.assertIn(GAME_START_RULE_BY_LOCALE[lang].lower(), body.lower())
                self.assertIn(GAME_START_META_BY_LOCALE[lang].lower(), body.lower())
                for old in OLD_GAME_START_TILE_FRAGMENTS:
                    self.assertNotIn(old, body)

    def test_game_start_keeps_the_bold_title(self):
        body = self.client.get(reverse('game_start'), {'lang': 'ja'}).content.decode()
        self.assertIn('日本人でも難しい?このゲーム、マジでハマる。', body)

    def test_result_screen_says_your_answer_per_locale(self):
        word = _make_word('r-word', 'Kite', 'たこ')
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                session = client.session
                session['game_history'] = [{
                    'index': 0, 'is_correct': False, 'points': 0, 'question_id': word.id,
                    'question_model': 'free', 'combo': 0, 'combo_bonus': 0,
                    'user_input': 'Octopus', 'correct_answer': 'Kite',
                }]
                session['game_difficulty'] = 'timeattack'
                session.save()
                body = client.get(reverse('game_result'), {'lang': lang}).content.decode()
                self.assertIn(YOUR_ANSWER_LABEL_BY_LOCALE[lang], body)
                for old in OLD_YOUR_INPUT_LABELS:
                    self.assertNotIn(old, body)


TOP_LOGIN_LINK_BY_LOCALE = {
    'ja': 'ログインはこちら', 'ko': '로그인은 여기', 'zh-cn': '点击这里登录', 'zh-hant': '點擊這裡登入',
    'th': 'เข้าสู่ระบบที่นี่', 'vi': 'Đăng nhập tại đây', 'fr': 'Se connecter ici', 'de': 'Hier anmelden',
    'it': 'Accedi qui', 'es-es': 'Inicia sesión aquí', 'es-mx': 'Inicia sesión aquí', 'pt': 'Faça login aqui',
    'pt-br': 'Faça login aqui', 'en': 'Log in here', 'en-in': 'Log in here',
}
OLD_TOP_AUTH_BUTTON_MARKERS = [
    'explore-btn btn-yellow', 'Start for Free', '無料で始めてみる', "Don't have an account? Register here",
    'Already have an account? Login here', 'アカウントをお持ちでない方はこちら', 'すでにアカウントをお持ちの方はこちら',
]


class TopPageGuestLoginLinkTests(TestCase):
    """2026-11-19: the logged-out top page keeps only the game button - the
    big login/register buttons are gone. A device that has logged in before
    (tj_has_account) gets a gold "log in here" text link instead; a first-
    time visitor gets nothing (the bottom nav still reaches login/sign-up)."""

    def _top(self, lang='en', **cookies):
        client = Client()
        for k, v in cookies.items():
            client.cookies[k] = v
        return client.get(reverse('toppage'), {'lang': lang}).content.decode()

    def test_first_time_visitor_sees_only_the_first_taste_button(self):
        body = self._top()
        self.assertIn('difficulty=first_taste', body)
        self.assertIn('startFirstTasteWithIntro', body)
        self.assertNotIn('class="top-login-text-link"', body)
        for marker in OLD_TOP_AUTH_BUTTON_MARKERS:
            self.assertNotIn(marker, body)

    def test_returning_guest_without_an_account_sees_only_the_game_button(self):
        body = self._top(tj_has_played='1')
        self.assertIn('explore-btn btn-green btn-play-main', body)
        self.assertIn('hero-buttons hero-buttons-single', body)
        self.assertNotIn('class="top-login-text-link"', body)
        for marker in OLD_TOP_AUTH_BUTTON_MARKERS:
            self.assertNotIn(marker, body)

    def test_guest_who_logged_in_before_gets_the_text_link_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self._top(lang, tj_has_played='1', tj_has_account='1')
                self.assertIn('class="top-login-text-link"', body)
                self.assertIn(f'href="{reverse("player_login")}?lang={lang}" class="top-login-text-link"', body)
                self.assertIn(TOP_LOGIN_LINK_BY_LOCALE[lang], body)
                for marker in OLD_TOP_AUTH_BUTTON_MARKERS:
                    self.assertNotIn(marker, body)

    def test_text_link_is_gold_underlined_and_tall_enough_to_tap(self):
        body = self._top(tj_has_played='1', tj_has_account='1')
        css = body[body.index('.top-login-text-link {'):]
        css = css[:css.index('}')]
        self.assertIn('min-height: 44px', css)
        self.assertIn('font-size: 1rem', css)
        self.assertIn('color: #d4af37', css)
        self.assertIn('text-decoration: underline', css)

    def test_logged_in_user_gets_no_text_link(self):
        user = User.objects.create_user('login-link-user', password='pw-for-tests-only')
        self.client.force_login(user)
        self.client.cookies['tj_has_account'] = '1'
        body = self.client.get(reverse('toppage')).content.decode()
        self.assertNotIn('class="top-login-text-link"', body)

    def test_nav_tutorial_hint_unaffected_for_returning_guest(self):
        body = self._top(tj_has_played='1')
        self.assertEqual(body.count('id="nav-tutorial-learn-hint"'), 1)
        self.assertEqual(body.count('id="nav-tutorial-category-link-hint"'), 1)


# ---------------------------------------------------------------------------
# 2026-11-19: level system (ranks.RANK_LEVELS) - rank thresholds, per-level
# question categories, never-decreasing max_level, level-up display.
# ---------------------------------------------------------------------------

NEW_RANK_TABLE = [
    (1, 0, 'Villager'), (2, 150, 'Traveler'), (3, 350, 'Ronin'), (4, 600, 'Ninja'), (5, 900, 'Samurai'),
    (6, 1300, 'Hatamoto'), (7, 1800, 'Daimyo'), (8, 2400, 'Shogun'), (9, 3100, 'General'), (10, 4000, 'Legend'),
]
LEVEL_CATEGORIES = {
    1: ['food', 'animal'], 2: ['flower', 'cook'], 3: ['nature', 'fashion'], 4: ['sports', 'householditems'],
    5: ['appliances', 'building'], 6: ['body', 'dailyactions', 'culture'], 7: ['live', 'work'],
    8: ['DailyConversation', 'TourismEtiquette', 'Entertainment', 'slang'],
    9: ['LivingInJapan', 'RomanceRelationships', 'BusinessJapanese'],
    10: ['MedicalEmergency', 'RealEstateRules', 'Prefectures'],
}
FREE_CATEGORIES = ['food', 'animal', 'flower', 'cook', 'nature', 'fashion', 'sports', 'householditems',
                   'appliances', 'building', 'body', 'dailyactions', 'culture', 'live', 'work']
PREMIUM_CATEGORIES = ['DailyConversation', 'TourismEtiquette', 'Entertainment', 'slang', 'LivingInJapan',
                      'RomanceRelationships', 'BusinessJapanese', 'MedicalEmergency', 'RealEstateRules', 'Prefectures']
ACCOUNT_SETTINGS_URL_NAMES = [
    'account_settings', 'account_settingsde', 'account_settingsenIN', 'account_settingsesES', 'account_settingsesMX',
    'account_settingsfr', 'account_settingsit', 'account_settingsja', 'account_settingsko', 'account_settingsptBR',
    'account_settingspt', 'account_settingsth', 'account_settingsvi', 'account_settingszhHANT', 'account_settingszhCN',
]
LEVEL_UP_TITLE_BY_LOCALE = {
    'ja': 'レベルアップ！新しいカテゴリーが解放されました', 'zh-hant': '等級提升！已解鎖新的類別', 'zh-cn': '等级提升！已解锁新的类别',
    'ko': '레벨 업! 새로운 카테고리가 열렸어요', 'de': 'Neues Level! Neue Kategorien freigeschaltet',
    'fr': 'Niveau supérieur ! Nouvelles catégories débloquées', 'es-es': '¡Subiste de nivel! Nuevas categorías desbloqueadas',
    'es-mx': '¡Subiste de nivel! Nuevas categorías desbloqueadas', 'it': 'Livello superiore! Nuove categorie sbloccate',
    'pt': 'Subiu de nível! Novas categorias desbloqueadas', 'pt-br': 'Subiu de nível! Novas categorias desbloqueadas',
    'vi': 'Lên cấp! Đã mở khóa danh mục mới', 'th': 'เลเวลอัป! ปลดล็อกหมวดหมู่ใหม่แล้ว',
    'en': 'Level up! New categories unlocked', 'en-in': 'Level up! New categories unlocked',
}
NEXT_LEVEL_LINE_BY_LOCALE = {  # with 50pt to go
    'ja': '次のレベルまで 50pt', 'zh-hant': '距離下一級還差 50 分', 'zh-cn': '距离下一级还差 50 分', 'ko': '다음 레벨까지 50pt',
    'de': 'noch 50 Pkt. bis zum nächsten Level', 'fr': 'encore 50 pts avant le niveau suivant',
    'es-es': 'faltan 50 pts para el siguiente nivel', 'es-mx': 'faltan 50 pts para el siguiente nivel',
    'it': 'mancano 50 pt al prossimo livello', 'pt': 'faltam 50 pts para o próximo nível', 'pt-br': 'faltam 50 pts para o próximo nível',
    'vi': 'còn 50 điểm nữa để lên cấp', 'th': 'อีก 50 คะแนนถึงเลเวลถัดไป', 'en': '50pt to the next level', 'en-in': '50pt to the next level',
}
GUEST_LEVEL_LINE_BY_LOCALE = {
    'ja': '会員登録するとレベルが貯まります', 'zh-hant': '註冊會員即可累積等級', 'zh-cn': '注册会员即可累积等级', 'ko': '회원가입하면 레벨이 쌓여요',
    'de': 'Registriere dich, um Level zu sammeln', 'fr': 'Créez un compte pour faire monter votre niveau',
    'es-es': 'Regístrate para ir subiendo de nivel', 'es-mx': 'Regístrate para ir subiendo de nivel',
    'it': 'Registrati per far salire il tuo livello', 'pt': 'Crie uma conta para subir de nível', 'pt-br': 'Crie uma conta para subir de nível',
    'vi': 'Đăng ký để tích lũy cấp độ', 'th': 'สมัครสมาชิกเพื่อสะสมเลเวล', 'en': 'Sign up to build up your level', 'en-in': 'Sign up to build up your level',
}
# Lv1 -> Lv2 unlocks flower + cook
LV2_UNLOCKED_LABELS_BY_LOCALE = {
    'ja': ('🌸 花', '🍳 料理'), 'zh-hant': ('🌸 花卉', '🍳 烹飪'), 'zh-cn': ('🌸 花卉', '🍳 烹饪'), 'ko': ('🌸 꽃', '🍳 요리'),
    'de': ('🌸 Blumen', '🍳 Kochen'), 'fr': ('🌸 Fleurs', '🍳 Cuisine'), 'es-es': ('🌸 Flores', '🍳 Cocina'),
    'es-mx': ('🌸 Flores', '🍳 Cocina'), 'it': ('🌸 Fiori', '🍳 Cucina'), 'pt': ('🌸 Flores', '🍳 Culinária'),
    'pt-br': ('🌸 Flores', '🍳 Culinária'), 'vi': ('🌸 Hoa', '🍳 Nấu ăn'), 'th': ('🌸 ดอกไม้', '🍳 การทำอาหาร'),
    'en': ('🌸 Flowers', '🍳 Cooking'), 'en-in': ('🌸 Flowers', '🍳 Cooking'),
}


def _user_with_score(username, score, max_level=None):
    user = User.objects.create_user(username, password='pw-for-tests-only')
    Profile.objects.filter(user=user).update(total_score=score, **({'max_level': max_level} if max_level else {}))
    return User.objects.get(pk=user.pk)


class RankDefinitionTests(SimpleTestCase):
    def test_thresholds_names_and_levels(self):
        self.assertEqual([(r['level'], r['min'], r['name']) for r in ranks.RANK_LEVELS], NEW_RANK_TABLE)

    def test_level_for_score_at_every_boundary(self):
        for level, minimum, _ in NEW_RANK_TABLE:
            with self.subTest(level=level):
                self.assertEqual(ranks.level_for_score(minimum), level)
                if minimum:
                    self.assertEqual(ranks.level_for_score(minimum - 1), level - 1)
        self.assertEqual(ranks.level_for_score(999999), 10)
        self.assertEqual(ranks.level_for_score(None), 1)

    def test_categories_are_cumulative_and_cover_all_25(self):
        expected = []
        for level in range(1, 11):
            expected += LEVEL_CATEGORIES[level]
            with self.subTest(level=level):
                self.assertEqual(ranks.categories_for_level(level), expected)
        self.assertEqual(len(ranks.categories_for_level(10)), 25)
        self.assertEqual(sorted(ranks.categories_for_level(10)), sorted(FREE_CATEGORIES + PREMIUM_CATEGORIES))
        self.assertEqual(ranks.categories_unlocked_between(1, 3), ['flower', 'cook', 'nature', 'fashion'])

    def test_progress_numbers(self):
        p = ranks.rank_progress(2, 250)
        self.assertEqual((p['level'], p['pts_to_next'], p['next_rank']['name']), (2, 100, 'Ronin'))
        self.assertAlmostEqual(p['progress_percent'], 50)
        top = ranks.rank_progress(10, 5000)
        self.assertTrue(top['is_max'])
        self.assertEqual(top['pts_to_next'], 0)
        # level kept above what the points give: bar reads 0%, never negative
        kept = ranks.rank_progress(5, 100)
        self.assertEqual(kept['progress_percent'], 0)
        self.assertEqual(kept['pts_to_next'], 1300 - 100)

    def test_every_category_has_a_short_label_in_every_language(self):
        for category in FREE_CATEGORIES + PREMIUM_CATEGORIES:
            for lang in LOCALE_QUERY_PARAMS:
                with self.subTest(category=category, lang=lang):
                    label = ranks.category_short_label(category, lang)
                    self.assertTrue(label and label != category, label)
            emoji, names = ranks.CATEGORY_SHORT_LABELS[category]
            self.assertEqual(set(names), {'ja', 'ko', 'zh-cn', 'zh-hant', 'th', 'vi', 'fr', 'de', 'it', 'es', 'pt', 'en'})

    def test_score_achievements_follow_the_rank_thresholds_others_unchanged(self):
        thresholds = {e['code']: e['threshold'] for e in ACHIEVEMENT_CATALOG}
        self.assertEqual(
            {k: thresholds[k] for k in ('score_50', 'score_300', 'score_1200', 'score_2000')},
            {'score_50': 150, 'score_300': 900, 'score_1200': 3100, 'score_2000': 4000})
        self.assertEqual(
            {k: thresholds[k] for k in ('streak_7', 'streak_30', 'streak_100', 'games_1', 'combo_7', 'combo_12')},
            {'streak_7': 7, 'streak_30': 30, 'streak_100': 100, 'games_1': 1, 'combo_7': 7, 'combo_12': 12})


class ProfileLevelTests(TestCase):
    def test_new_user_starts_at_villager(self):
        user = User.objects.create_user('lv-new', password='pw-for-tests-only')
        self.assertEqual((user.profile.max_level, user.profile.level), (1, 1))
        self.assertEqual(user.profile.rank_info['name'], 'Villager')

    def test_level_never_drops_when_points_are_spent(self):
        user = _user_with_score('lv-spender', 1300)
        profile = user.profile
        self.assertEqual(profile.sync_max_level(), (1, 6))
        Profile.objects.filter(pk=profile.pk).update(total_score=200)  # e.g. time extensions
        profile.refresh_from_db()
        self.assertEqual(profile.level, 6)
        self.assertEqual(profile.rank_info['name'], 'Hatamoto')
        self.assertEqual(profile.sync_max_level(), (6, 6))

    def test_level_follows_points_earned_outside_games(self):
        user = _user_with_score('lv-bonus', 160)
        self.assertEqual(user.profile.max_level, 1)
        self.assertEqual(user.profile.level, 2)  # visible right away, persisted at the next game result

    def test_existing_achievements_are_kept_after_the_threshold_change(self):
        user = _user_with_score('lv-badge', 60)
        Achievement.objects.create(user=user, code='score_50')  # earned under the old 50pt threshold
        check_and_unlock_achievements(user.profile)
        progress = {b['code']: b for b in get_achievement_progress(user.profile)}
        self.assertTrue(progress['score_50']['unlocked'])
        self.assertEqual(progress['score_50']['goal'], 150)
        self.assertTrue(Achievement.objects.filter(user=user, code='score_50').exists())

    def test_new_thresholds_gate_new_unlocks(self):
        user = _user_with_score('lv-badge2', 300)
        unlocked = {e['code'] for e in check_and_unlock_achievements(user.profile)}
        self.assertIn('score_50', unlocked)        # 150 reached
        self.assertNotIn('score_300', unlocked)    # now needs 900


class MaxLevelMigrationTests(TestCase):
    def test_migration_files_are_contiguous_and_0047_follows_0046(self):
        import os
        names = sorted(f for f in os.listdir(os.path.join(os.path.dirname(__file__), 'migrations')) if re.match(r'^\d{4}_.*\.py$', f))
        numbers = [int(n[:4]) for n in names]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        self.assertEqual(names[46], '0047_profile_max_level.py')
        module = importlib.import_module('thank_japan_app.migrations.0047_profile_max_level')
        self.assertEqual(module.Migration.dependencies, [('thank_japan_app', '0046_dailyactivitysummary_game_funnel')])

    def test_existing_users_keep_their_old_rank(self):
        module = importlib.import_module('thank_japan_app.migrations.0047_profile_max_level')
        # score -> expected level (old thresholds 0/50/100/200/300/450/650/900/1200/2000)
        cases = {0: 1, 49: 1, 50: 2, 120: 3, 300: 5, 650: 7, 1250: 9, 2000: 10, 4500: 10}
        users = {}
        for score in cases:
            users[score] = _user_with_score(f'mig-{score}', score)
            Profile.objects.filter(user=users[score]).update(max_level=1)
        module.init_max_level(django_apps, None)
        for score, expected in cases.items():
            with self.subTest(score=score):
                self.assertEqual(Profile.objects.get(user=users[score]).max_level, expected)
                self.assertGreaterEqual(expected, ranks.level_for_score(score))  # never below the new-threshold level


class LevelGamePoolTests(TestCase):
    """Only timeattack is level-gated; guests are always Lv1."""

    def setUp(self):
        for i, category in enumerate(FREE_CATEGORIES):
            for j in range(2):
                ThankJapanModel.objects.create(
                    name=f'pool-{category}-{j}', englishname=f'{category} {j}', jpname='いぬ' if j == 0 else f'ことばの{category}{j}',
                    category=category, slug=f'pool-{category}-{j}', description='d', history='h')
        for category in PREMIUM_CATEGORIES:
            ThankJapanPremium.objects.create(
                name=f'pool-p-{category}', englishname=f'{category} p', jpname='ふれーず', romaji='fureezu',
                category=category, slug=f'pool-p-{category}', description='d', history='h')

    def _categories_played(self, client):
        client.get(reverse('game_restart'))
        questions = client.session['game_question_ids']
        cats = set()
        for q in questions:
            model = ThankJapanPremium if q['model'] == 'premium' else ThankJapanModel
            cats.add(model.objects.get(id=q['id']).category)
        return questions, cats

    def test_guest_only_gets_lv1_categories(self):
        questions, cats = self._categories_played(self.client)
        self.assertEqual(cats, {'food', 'animal'})
        self.assertEqual(len(questions), 4)
        self.assertTrue(all(q['model'] == 'free' for q in questions))

    def test_logged_in_level_widens_the_pool(self):
        user = _user_with_score('pool-lv5', 950)
        self.client.force_login(user)
        _, cats = self._categories_played(self.client)
        self.assertEqual(cats, set(ranks.categories_for_level(5)))

    def test_level_kept_from_max_level_even_with_fewer_points(self):
        user = _user_with_score('pool-kept', 0, max_level=3)
        self.client.force_login(user)
        _, cats = self._categories_played(self.client)
        self.assertEqual(cats, set(ranks.categories_for_level(3)))

    def test_lv8_gets_premium_words_without_a_subscription(self):
        user = _user_with_score('pool-lv8', 2500)
        self.assertFalse(user.profile.has_premium_access)
        self.client.force_login(user)
        questions, cats = self._categories_played(self.client)
        self.assertTrue({'DailyConversation', 'TourismEtiquette', 'Entertainment', 'slang'} <= cats)
        self.assertFalse(cats & {'LivingInJapan', 'MedicalEmergency', 'Prefectures'})
        self.assertTrue(any(q['model'] == 'premium' for q in questions))

    def test_legend_gets_all_25_categories(self):
        user = _user_with_score('pool-lv10', 4000)
        self.client.force_login(user)
        _, cats = self._categories_played(self.client)
        self.assertEqual(cats, set(FREE_CATEGORIES + PREMIUM_CATEGORIES))

    def test_first_two_questions_are_short_free_words(self):
        user = _user_with_score('pool-open', 4000)
        self.client.force_login(user)
        questions, _ = self._categories_played(self.client)
        for q in questions[:2]:
            self.assertEqual(q['model'], 'free')
            self.assertEqual(ThankJapanModel.objects.get(id=q['id']).jpname, 'いぬ')

    def test_premium_question_from_the_level_pool_scores_two_points(self):
        user = _user_with_score('pool-points', 2500)
        self.client.force_login(user)
        word = ThankJapanPremium.objects.get(category='slang')
        session = self.client.session
        session['game_question_ids'] = [{'id': word.id, 'model': 'premium'}]
        session['game_current_index'] = 0
        session['game_score'] = 0
        session['game_difficulty'] = 'timeattack'
        session['game_history'] = []
        session.save()
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        res = json.loads(_submit_choice(self.client, data['choices'].index(word.englishname)).content)
        self.assertTrue(res['is_correct'])
        self.assertEqual(res['points'], 2)

    def test_first_taste_daily_and_single_ignore_the_level(self):
        ramen = ThankJapanModel.objects.create(name='pool-ramen', englishname='Ramen', jpname='らーめん',
                                               category='food', slug='pool-ramen', description='d', history='h')
        self.client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.assertEqual(self.client.session['game_question_ids'][0], {'id': ramen.id, 'model': 'free'})
        # single-mode review of a word far above the guest's Lv1
        self.client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': 'pool-work-1'})
        self.assertEqual(self.client.session['game_difficulty'], 'single')
        self.assertEqual(len(self.client.session['game_question_ids']), 1)
        # daily question for a word outside Lv1
        DailyQuestion.objects.create(date=timezone.localdate(), word=ThankJapanModel.objects.get(slug='pool-culture-1'))
        response = Client().get(reverse('daily_question'))
        self.assertEqual(response.context['daily_state'], 'question')

    def test_recording_still_works_in_a_level_game(self):
        user = _user_with_score('pool-record', 950)
        self.client.force_login(user)
        self.client.get(reverse('game_restart'))
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        _submit_choice(self.client, 0)
        self.client.get(reverse('game_result'))
        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual((row.total_count, row.games_started, row.games_completed), (1, 1, 1))


class LevelUpDisplayTests(TestCase):
    def setUp(self):
        self.word = ThankJapanModel.objects.create(name='lu-word', englishname='Dog', jpname='いぬ',
                                                   category='animal', slug='lu-word', description='d', history='h')

    def _finish_game(self, client, points, lang='en'):
        session = client.session
        session['game_history'] = [{'index': 0, 'is_correct': True, 'points': points, 'question_id': self.word.id,
                                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                                    'user_input': 'Dog', 'correct_answer': 'Dog'}]
        session['game_difficulty'] = 'timeattack'
        session.save()
        return client.get(reverse('game_result'), {'lang': lang})

    def test_level_up_shows_once_with_unlocked_categories_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                user = _user_with_score(f'lu-{lang}', 140)
                client = Client()
                client.force_login(user)
                response = self._finish_game(client, 15, lang)
                body = response.content.decode()
                self.assertEqual(response.context['level_up']['level'], 2)
                self.assertIn('id="level-up-banner"', body)
                self.assertIn(LEVEL_UP_TITLE_BY_LOCALE[lang], body)
                self.assertIn('Lv2 Traveler', body)
                for label in LV2_UNLOCKED_LABELS_BY_LOCALE[lang]:
                    self.assertIn(label, body)
                self.assertEqual(Profile.objects.get(user=user).max_level, 2)
                again = client.get(reverse('game_result'), {'lang': lang})
                self.assertIsNone(again.context['level_up'])
                self.assertNotIn('id="level-up-banner"', again.content.decode())

    def test_no_level_up_message_without_a_rise(self):
        user = _user_with_score('lu-none', 10)
        self.client.force_login(user)
        response = self._finish_game(self.client, 5)
        self.assertIsNone(response.context['level_up'])
        self.assertNotIn('id="level-up-banner"', response.content.decode())

    def test_multi_level_jump_lists_every_unlocked_category(self):
        user = _user_with_score('lu-jump', 140)
        self.client.force_login(user)
        response = self._finish_game(self.client, 300)  # 440 -> Lv3
        self.assertEqual(response.context['level_up']['level'], 3)
        self.assertEqual(len(response.context['level_up']['categories']), 4)

    def test_level_line_on_start_and_result_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                user = _user_with_score(f'line-{lang}', 300)  # Lv2, 50pt to Lv3
                client = Client()
                client.force_login(user)
                start = client.get(reverse('game_start'), {'lang': lang}).content.decode()
                self.assertIn('Lv2 Traveler', start)
                self.assertIn(NEXT_LEVEL_LINE_BY_LOCALE[lang], start)
                result = self._finish_game(client, 0, lang).content.decode()
                self.assertIn('id="level-progress-line"', result)

    def test_guest_sees_the_sign_up_nudge_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = Client().get(reverse('game_start'), {'lang': lang}).content.decode()
                self.assertIn(GUEST_LEVEL_LINE_BY_LOCALE[lang], body)
                self.assertIn(f'{reverse("player_register")}?lang={lang}', body)

    def test_max_level_line(self):
        user = _user_with_score('line-max', 4200)
        self.client.force_login(user)
        body = self.client.get(reverse('game_start')).content.decode()
        self.assertIn('Lv10 Legend', body)
        self.assertIn('Max level reached!', body)

    def test_start_screen_play_button_has_no_rank(self):
        user = _user_with_score('line-play', 2500)
        self.client.force_login(user)
        body = self.client.get(reverse('game_start')).content.decode()
        play = body[body.index('class="play-btn"'):]
        play = play[:play.index('</a>')]
        self.assertNotIn('Shogun', play)


class RankDisplayTests(TestCase):
    def test_account_settings_rank_card_follows_the_level_in_every_language(self):
        user = _user_with_score('acct-rank', 1300)
        self.client.force_login(user)
        for url_name in ACCOUNT_SETTINGS_URL_NAMES:
            with self.subTest(url=url_name):
                response = self.client.get(reverse(url_name))
                body = response.content.decode()
                self.assertIn('🚩 旗本 (Hatamoto)', body)
                self.assertIn('card-platinum', body)
                self.assertEqual(response.context['pts_to_next'], 500)

    def test_card_colour_by_level(self):
        expected = {1: 'card-wood', 2: 'card-wood', 3: 'card-silver', 4: 'card-gold', 5: 'card-gold',
                    6: 'card-platinum', 7: 'card-platinum', 8: 'card-shogun', 9: 'card-shogun', 10: 'card-shogun'}
        self.assertEqual({r['level']: r['card'] for r in ranks.RANK_LEVELS}, expected)

    def test_account_settings_keeps_a_level_above_the_points(self):
        user = _user_with_score('acct-kept', 100, max_level=5)
        self.client.force_login(user)
        body = self.client.get(reverse('account_settings')).content.decode()
        self.assertIn('⚔️ 侍 (Samurai)', body)

    def test_header_badge_rank_comes_from_the_level(self):
        user = _user_with_score('header-rank', 2400)
        self.assertEqual(user.profile.rank_info['emoji'], '🐎')
        self.assertEqual(user.profile.rank_info['name'], 'Shogun')


class TopPlayButtonRankTests(TestCase):
    def test_logged_in_button_shows_the_english_rank_per_locale(self):
        user = _user_with_score('top-rank', 1300)
        self.client.force_login(user)
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self.client.get(reverse('toppage'), {'lang': lang}).content.decode()
                self.assertIn('btn-play-main play-with-rank', body)
                self.assertIn('<span class="play-rank">(Hatamoto)</span> 🎮', body)
                self.assertIn('<p class="play-rank-below">(Hatamoto)</p>', body)

    def test_returning_guest_button_shows_villager(self):
        client = Client()
        client.cookies['tj_has_played'] = '1'
        body = client.get(reverse('toppage')).content.decode()
        self.assertIn('<span class="play-rank">(Villager)</span> 🎮', body)

    def test_first_time_guest_button_has_no_rank(self):
        body = Client().get(reverse('toppage')).content.decode()
        self.assertNotIn('class="play-rank"', body)
        self.assertNotIn('<p class="play-rank-below">', body)
        self.assertIn('difficulty=first_taste', body)

    def test_overflow_fallback_is_wired(self):
        body = Client().get(reverse('toppage')).content.decode()
        self.assertIn('function fitPlayRank()', body)
        self.assertIn('.play-with-rank.rank-below + .play-rank-below { display: block; }', body)


# ---------------------------------------------------------------------------
# 2026-11-19: 45-second time attack (views.GAME_TIME_LIMIT_SECONDS) and the
# login page's How to Play (rules + rank table from ranks.RANK_LEVELS).
# ---------------------------------------------------------------------------


OLD_60_SECOND_PATTERN = re.compile(
    r'60 ?秒|60s\b|60-?[Ss]econd|60 sec|60초|60 giây|60 วินาที|60[ -]Sekunden|60 secondes|60 segundos|60 secondi')
GAME_START_RULE_TIME_BY_LOCALE = {
    'ja': '⏱ 45秒タイムアタック', 'zh-hant': '⏱ 45秒限時挑戰', 'zh-cn': '⏱ 45秒限时挑战', 'ko': '⏱ 45초 타임어택',
    'de': '⏱ 45 Sekunden auf Zeit', 'fr': '⏱ Défi 45 secondes', 'es-es': '⏱ Contrarreloj de 45s',
    'es-mx': '⏱ Contrarreloj de 45s', 'it': '⏱ Time Attack di 45s', 'pt': '⏱ Desafio de 45s', 'pt-br': '⏱ Desafio de 45s',
    'vi': '⏱ Thử thách thời gian 45 giây', 'th': '⏱ ท้าทายเวลา 45 วินาที', 'en': '⏱ 45s Time Attack', 'en-in': '⏱ 45s Time Attack',
}
GAME_START_META_TIME_BY_LOCALE = {
    'zh-hant': '45秒學會', 'zh-cn': '45秒学会', 'ko': '45초 만에', 'fr': 'en 45 secondes', 'es-es': 'en 45 segundos',
    'es-mx': 'en 45 segundos', 'pt': 'em 45 segundos', 'pt-br': 'em 45 segundos', 'de': 'in 45 Sekunden',
    'it': 'in 45 secondi', 'vi': '45 giây', 'th': '45 วินาที', 'en': 'in 45 seconds', 'en-in': 'in 45 seconds',
    'ja': '45秒チャレンジ',
}
HOW_TO_PLAY_RULES_BY_LOCALE = {  # (time line, points line, levels line)
    'ja': ('⏱ 45秒タイムアタック', '⭕ 正解で+1pt、💎 上級の単語(Lv8から)は2pt、⚡ 3連続正解以降は1問ごとに+1pt', '🔓 ランクが上がると、出題されるカテゴリーが増えます'),
    'zh-hant': ('⏱ 45秒限時挑戰', '⭕ 答對+1分、💎 進階單字（Lv8起）2分', '🔓 等級提升後，出題類別會增加'),
    'zh-cn': ('⏱ 45秒限时挑战', '⭕ 答对+1分、💎 进阶单词（Lv8起）2分', '🔓 等级提升后，出题类别会增加'),
    'ko': ('⏱ 45초 타임어택', '⭕ 정답 +1점, 💎 상급 단어(Lv8부터) 2점', '🔓 랭크가 오르면 출제되는 카테고리가 늘어나요'),
    'de': ('⏱ 45 Sekunden auf Zeit', '⭕ Richtig +1 Pkt., 💎 fortgeschrittene Wörter (ab Lv8) 2 Pkt.', '🔓 Mit jedem Rang kommen neue Fragekategorien hinzu'),
    'fr': ('⏱ Défi 45 secondes', '⭕ Bonne réponse +1 pt, 💎 mots avancés (dès Lv8) 2 pts', '🔓 Plus votre rang monte'),
    'es-es': ('⏱ Contrarreloj de 45 s', '⭕ Acierto +1 pt, 💎 palabras avanzadas (desde Lv8) 2 pts', '🔓 Al subir de rango'),
    'es-mx': ('⏱ Contrarreloj de 45 s', '⭕ Acierto +1 pt, 💎 palabras avanzadas (desde Lv8) 2 pts', '🔓 Al subir de rango'),
    'it': ('⏱ Time Attack di 45 s', '⭕ Risposta giusta +1 pt, 💎 parole avanzate (dal Lv8) 2 pt', '🔓 Salendo di rango'),
    'pt': ('⏱ Desafio de 45 s', '⭕ Acerto +1 pt, 💎 palavras avançadas (a partir do Lv8) 2 pts', '🔓 Ao subir de ranking'),
    'pt-br': ('⏱ Desafio de 45 s', '⭕ Acerto +1 pt, 💎 palavras avançadas (a partir do Lv8) 2 pts', '🔓 Ao subir de ranking'),
    'vi': ('⏱ Thử thách 45 giây', '⭕ Trả lời đúng +1 điểm, 💎 từ nâng cao (từ Lv8) 2 điểm', '🔓 Thăng hạng để mở thêm danh mục câu hỏi'),
    'th': ('⏱ ท้าทายเวลา 45 วินาที', '⭕ ตอบถูก +1 คะแนน, 💎 คำศัพท์ระดับสูง (ตั้งแต่ Lv8) 2 คะแนน', '🔓 เมื่ออันดับสูงขึ้น'),
    'en': ('⏱ Time attack, 45 seconds', '⭕ Correct +1pt, 💎 advanced words (Lv8 and up) 2pt, ⚡ from 3 in a row, +1pt per question', '🔓 Rank up to unlock more question categories'),
    'en-in': ('⏱ Time attack, 45 seconds', '⭕ Correct +1pt, 💎 advanced words (Lv8 and up) 2pt', '🔓 Rank up to unlock more question categories'),
}
HOW_TO_PLAY_CLOSE_BY_LOCALE = {
    'ja': '閉じる ❌', 'zh-hant': '關閉 ❌', 'zh-cn': '关闭 ❌', 'de': 'Schließen ❌', 'fr': 'Fermer ❌',
    'es-es': 'Cerrar ❌', 'es-mx': 'Cerrar ❌', 'it': 'Chiudi ❌', 'ko': '닫기 ❌', 'pt': 'Fechar ❌', 'pt-br': 'Fechar ❌',
    'vi': 'Đóng ❌', 'th': 'ปิด ❌', 'en': 'Close Guide ❌', 'en-in': 'Close Guide ❌',
}


def _create_social_apps():
    """player_login-v2.html renders allauth provider_login_url for these
    providers, which raises without a SocialApp row - production has real
    ones, the test DB needs dummies (no real credentials)."""
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    site = Site.objects.get_current()
    for provider in ('google', 'facebook', 'twitter_oauth2'):
        app = SocialApp.objects.create(provider=provider, name=f'test-{provider}', client_id='test-only', secret='test-only')
        app.sites.add(site)


class GameTimeLimitTests(TestCase):
    def setUp(self):
        _create_social_apps()
        self.word = ThankJapanModel.objects.create(name='tl-word', englishname='Dog', jpname='いぬ',
                                                   category='animal', slug='tl-word', description='d', history='h')

    def _start(self, client, difficulty='timeattack'):
        _start_session_game(client, [self.word], difficulty=difficulty)

    def test_constant_is_45_and_extension_is_unchanged(self):
        self.assertEqual(tj_views.GAME_TIME_LIMIT_SECONDS, 45)
        self.assertEqual((tj_views.TIME_EXTEND_COST, tj_views.TIME_EXTEND_SECONDS), (10, 10))
        self.assertEqual(tj_views.DAILY_QUESTION_TIME_LIMIT, 30)

    def test_server_clock_starts_at_the_limit(self):
        self._start(self.client)
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIn(data['seconds_left'], (44, 45))
        end = self.client.session['game_end_time']
        self.assertAlmostEqual(end - time_module.time(), 45, delta=2)

    def test_untimed_modes_still_have_no_clock(self):
        self._start(self.client, difficulty='first_taste')
        data = json.loads(self.client.get(reverse('game_question_data')).content)
        self.assertIsNone(data['seconds_left'])

    def test_game_screen_timer_reads_the_constant(self):
        self._start(self.client)
        body = self.client.get(reverse('game_play')).content.decode()
        self.assertIn('const TOTAL_TIME = 45;', body)
        self.assertIn('<div class="ring-num" id="ring-num">45</div>', body)

    def test_everything_follows_the_constant(self):
        with patch.object(tj_views, 'GAME_TIME_LIMIT_SECONDS', 30):
            self._start(self.client)
            self.assertIn('const TOTAL_TIME = 30;', self.client.get(reverse('game_play')).content.decode())
            data = json.loads(self.client.get(reverse('game_question_data')).content)
            self.assertIn(data['seconds_left'], (29, 30))
            self.assertIn('⏱ 30s Time Attack', self.client.get(reverse('game_start')).content.decode())
            self.assertIn('30 Second Challenge', build_combo_share_message('en', 3, 10))
            self.assertIn('⏱ Time attack, 30 seconds', self.client.get(reverse('player_login')).content.decode())

    def test_no_60_second_wording_left_on_live_screens_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                start = client.get(reverse('game_start'), {'lang': lang}).content.decode()
                self.assertIn(GAME_START_RULE_TIME_BY_LOCALE[lang], start)
                self.assertIn(GAME_START_META_TIME_BY_LOCALE[lang], start)
                self.assertIsNone(OLD_60_SECOND_PATTERN.search(start), OLD_60_SECOND_PATTERN.search(start))
                self._start(client)
                play = client.get(reverse('game_play'), {'lang': lang}).content.decode()
                self.assertIsNone(OLD_60_SECOND_PATTERN.search(play))
                session = client.session
                session['game_history'] = [{'index': 0, 'is_correct': True, 'points': 1, 'question_id': self.word.id,
                                            'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                                            'user_input': 'Dog', 'correct_answer': 'Dog'}]
                session.save()
                result = client.get(reverse('game_result'), {'lang': lang}).content.decode()
                self.assertIn(TIMED_CHALLENGE_BY_LOCALE[lang], result)
                self.assertIsNone(OLD_60_SECOND_PATTERN.search(result))
                login = client.get(reverse('player_login'), {'lang': lang}).content.decode()
                self.assertIsNone(OLD_60_SECOND_PATTERN.search(login))

    def test_share_texts_say_45_seconds_in_every_language(self):
        for lang in LOCALE_QUERY_PARAMS:
            for max_combo in (1, 3, 7):
                with self.subTest(lang=lang, combo=max_combo):
                    message = build_combo_share_message(lang, max_combo, 12)
                    self.assertIn('45', message)
                    self.assertIsNone(OLD_60_SECOND_PATTERN.search(message))
                    self.assertIn('12', message)


class HowToPlayModalTests(TestCase):
    def setUp(self):
        _create_social_apps()

    def _login_page(self, lang):
        return Client().get(reverse('player_login'), {'lang': lang}).content.decode()

    def test_rank_table_comes_from_ranks_py(self):
        body = self._login_page('en')
        table = body[body.index('id="how-to-play-ranks"'):]
        table = table[:table.index('</div>\n        </div>') if '</div>\n        </div>' in table else len(table)]
        positions = []
        for rank in ranks.RANK_LEVELS:
            row = f"<span>{rank['emoji']} {rank['ja']} {rank['name']}</span><span style=\"color:#d46a00; font-weight:bold;\">{rank['min']}~</span>"
            self.assertIn(row, body)
            positions.append(body.index(row))
        self.assertEqual(positions, sorted(positions, reverse=True))  # Legend first, Villager last
        for old in ('>2000~<', '>1200~<', '>650~<', '>450~<', '>300~<', '>200~<', '>100~<', '>50~<'):
            self.assertNotIn(old, body)

    def test_rules_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self._login_page(lang)
                self.assertIn('id="how-to-play-rules"', body)
                for fragment in HOW_TO_PLAY_RULES_BY_LOCALE[lang]:
                    self.assertIn(fragment, body)

    def test_toggle_keeps_both_labels_localized(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self._login_page(lang)
                self.assertIn('id="toggle-instruction-open"', body)
                close = body[body.index('id="toggle-instruction-close"'):]
                close = close[:close.index('</span>')]
                self.assertIn(HOW_TO_PLAY_CLOSE_BY_LOCALE[lang], close)
                self.assertNotIn('toggleBtn.textContent', body)


# ---------------------------------------------------------------------------
# 2026-11-19: one definition of "premium words a non-subscriber can open"
# (models.premium_free_sample_queryset) shared by the category list, word
# detail page, sitemap and single-word review.
# ---------------------------------------------------------------------------


PREMIUM_INFO_URL_NAME_BY_LOCALE = {
    'en': 'premium_info', 'en-in': 'premium_infoenIN', 'ja': 'premium_infoja', 'de': 'premium_infode',
    'es-es': 'premium_infoesES', 'es-mx': 'premium_infoesMX', 'fr': 'premium_infofr', 'it': 'premium_infoit',
    'ko': 'premium_infoko', 'pt': 'premium_infopt', 'pt-br': 'premium_infoptBR', 'th': 'premium_infoth',
    'vi': 'premium_infovi', 'zh-hant': 'premium_infozhHANT', 'zh-cn': 'premium_infozhCN',
}


class PremiumFreeSampleConsistencyTests(TestCase):
    CATEGORY = 'BusinessJapanese'

    def setUp(self):
        base = timezone.now() - timedelta(days=30)
        self.words = []
        for i in range(15):
            word = ThankJapanPremium.objects.create(
                name=f'biz-{i}', englishname=f'Business {i}', jpname='びじねす', romaji='bijinesu',
                category=self.CATEGORY, slug=f'biz-word-{i}', description='d', history='h')
            self.words.append(word)
        # oldest first; #11 and #12 share a timestamp, so the id decides
        for i, word in enumerate(self.words):
            stamp = base + timedelta(hours=min(i, 11))
            ThankJapanPremium.objects.filter(pk=word.pk).update(timestamp=stamp)
        self.open_ids = [w.id for w in self.words[:12]]
        self.locked = self.words[12:]
        self.free_preview = [ThankJapanPremium.objects.create(
            name=f'slang-{i}', englishname=f'Slang {i}', jpname='すらんぐ', romaji='surangu',
            category='slang', slug=f'slang-word-{i}', description='d', history='h') for i in range(3)]

    def _premium_user(self):
        user = User.objects.create_user('sample-premium', password='pw-for-tests-only')
        Profile.objects.filter(user=user).update(is_premium=True)
        return User.objects.get(pk=user.pk)

    def test_definition_is_the_12_oldest_with_id_as_tie_breaker(self):
        self.assertEqual(PREMIUM_FREE_SAMPLE_SIZE, 12)
        self.assertEqual(list(premium_free_sample_queryset(self.CATEGORY).values_list('id', flat=True)), self.open_ids)

    def test_list_page_shows_exactly_the_sample_to_non_subscribers(self):
        for client in (Client(), self._logged_in_free_client()):
            body = client.get(reverse('businessjapanese')).content.decode()
            for word in self.words[:12]:
                self.assertIn(f'/{word.slug}/', body)
            for word in self.locked:
                self.assertNotIn(f'/{word.slug}/', body)

    def _logged_in_free_client(self):
        client = Client()
        client.force_login(User.objects.create_user(f'sample-free-{User.objects.count()}', password='pw-for-tests-only'))
        return client

    def _detail(self, client, word):
        return client.get(reverse('detail_premium', kwargs={'category': word.category, 'slug': word.slug}))

    def test_detail_page_opens_exactly_the_sample(self):
        client = Client()
        for word in self.words[:12]:
            self.assertEqual(self._detail(client, word).status_code, 200, word.slug)
        for word in self.locked:
            response = self._detail(client, word)
            self.assertEqual(response.status_code, 302, word.slug)
            self.assertIn(reverse('premium_info'), response['Location'])

    def test_sitemap_lists_exactly_the_sample(self):
        body = Client().get(reverse('sitemap')).content.decode()
        for word in self.words[:12]:
            self.assertIn(f'/premium-content/businessjapanese/{word.slug}/', body)
        for word in self.locked:
            self.assertNotIn(f'/{word.slug}/', body)
        for word in self.free_preview:
            self.assertIn(f'/premium-content/slang/{word.slug}/', body)

    def _review(self, client, word, lang=None):
        params = {'mode': 'single', 'model_type': 'premium', 'slug': word.slug}
        if lang:
            params['lang'] = lang
        return client.get(reverse('game_restart'), params)

    def test_single_review_opens_exactly_the_sample(self):
        # Before 2026-11-19 review allowed the 5 *newest* - so the oldest
        # sample words were blocked and the newest (locked elsewhere) open.
        for word in self.words[:12]:
            response = self._review(Client(), word)
            self.assertTrue(response['Location'].startswith(reverse('game_play')), word.slug)
        for word in self.locked:
            response = self._review(Client(), word)
            self.assertFalse(response['Location'].startswith(reverse('game_play')), word.slug)

    def test_all_four_paths_agree(self):
        client = Client()
        list_body = client.get(reverse('businessjapanese')).content.decode()
        sitemap = client.get(reverse('sitemap')).content.decode()
        for word in self.words:
            with self.subTest(slug=word.slug):
                on_list = f'/{word.slug}/' in list_body
                detail_open = self._detail(Client(), word).status_code == 200
                in_sitemap = f'/{word.slug}/' in sitemap
                review_open = self._review(Client(), word)['Location'].startswith(reverse('game_play'))
                self.assertEqual({on_list, detail_open, in_sitemap, review_open}, {word.id in self.open_ids})

    def test_subscriber_opens_everything(self):
        client = Client()
        client.force_login(self._premium_user())
        body = client.get(reverse('businessjapanese')).content.decode()
        for word in self.words:
            self.assertIn(f'/{word.slug}/', body)
            self.assertEqual(self._detail(client, word).status_code, 200)
            self.assertTrue(self._review(client, word)['Location'].startswith(reverse('game_play')))

    def test_free_preview_categories_stay_fully_open(self):
        for word in self.free_preview:
            self.assertEqual(self._detail(Client(), word).status_code, 200)
            self.assertTrue(self._review(Client(), word)['Location'].startswith(reverse('game_play')))

    def test_locked_review_redirects_to_the_premium_page_in_every_language(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = self._review(Client(), self.locked[0], lang=lang)
                self.assertEqual(response['Location'], reverse(PREMIUM_INFO_URL_NAME_BY_LOCALE[lang]))
                open_response = self._review(Client(), self.words[0], lang=lang)
                self.assertTrue(open_response['Location'].startswith(reverse('game_play')))

    def test_sample_detail_page_renders_in_every_language(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                response = Client().get(
                    reverse('detail_premium', kwargs={'category': self.CATEGORY, 'slug': self.words[11].slug}), {'lang': lang})
                self.assertEqual(response.status_code, 200)

    def test_first_taste_daily_question_and_nav_tutorial_unaffected(self):
        ramen = ThankJapanModel.objects.create(name='sample-ramen', englishname='Ramen', jpname='らーめん',
                                               category='food', slug='sample-ramen', description='d', history='h')
        client = Client()
        client.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.assertEqual(client.session['game_question_ids'][0], {'id': ramen.id, 'model': 'free'})
        DailyQuestion.objects.create(date=timezone.localdate(), premium_word=self.free_preview[0])
        self.assertEqual(Client().get(reverse('daily_question')).context['daily_state'], 'question')
        ThankJapanModel.objects.create(name='sample-tsukemen', englishname='Tsukemen', jpname='つけめん',
                                       category='cook', slug=NAV_TUTORIAL_TARGET_SLUG, description='d', history='h')
        nav = Client()
        nav.cookies['tj_has_played'] = '1'
        nav.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': NAV_TUTORIAL_TARGET_SLUG})
        self.assertTrue(nav.session['nav_tutorial_single_active'])


# ---------------------------------------------------------------------------
# 2026-11-19: no bottom nav while playing (it hid the last answer choice),
# tighter bottom spacing, and a "×" way out of the play screen.
# ---------------------------------------------------------------------------

HIDE_NAV_CSS = 'nav.bottom-nav { display: none !important; }'
EXIT_CONFIRM_BY_LOCALE = {
    'ja': 'ゲームをやめますか？このゲームのポイントは保存されません。', 'zh-hant': '要結束遊戲嗎？本局的分數不會被保存。',
    'zh-cn': '要结束游戏吗？本局的分数不会被保存。', 'ko': '게임을 그만두시겠어요? 이번 게임의 포인트는 저장되지 않아요.',
    'de': 'Spiel beenden? Die Punkte dieser Runde werden nicht gespeichert.',
    'fr': 'Quitter la partie ? Les points de cette partie ne seront pas enregistrés.',
    'es-es': '¿Salir del juego? Los puntos de esta partida no se guardarán.',
    'es-mx': '¿Salir del juego? Los puntos de esta partida no se guardarán.',
    'it': 'Vuoi uscire dal gioco? I punti di questa partita non verranno salvati.',
    'pt': 'Sair do jogo? Os pontos desta partida não serão salvos.', 'pt-br': 'Sair do jogo? Os pontos desta partida não serão salvos.',
    'vi': 'Thoát trò chơi? Điểm của lượt này sẽ không được lưu.', 'th': 'ออกจากเกมไหม? คะแนนของรอบนี้จะไม่ถูกบันทึก',
    'en': "Quit the game? Points from this game won't be saved.", 'en-in': "Quit the game? Points from this game won't be saved.",
}
EXIT_REVIEW_CONFIRM_BY_LOCALE = {
    'ja': '復習をやめて、単語ページに戻りますか？', 'zh-hant': '要結束複習並返回單字頁面嗎？', 'zh-cn': '要结束复习并返回单词页面吗？',
    'ko': '복습을 그만두고 단어 페이지로 돌아갈까요?', 'de': 'Wiederholung beenden und zur Wortseite zurückkehren?',
    'fr': 'Arrêter la révision et revenir à la page du mot ?', 'es-es': '¿Dejar el repaso y volver a la página de la palabra?',
    'es-mx': '¿Dejar el repaso y volver a la página de la palabra?', 'it': 'Interrompere il ripasso e tornare alla pagina della parola?',
    'pt': 'Parar a revisão e voltar à página da palavra?', 'pt-br': 'Parar a revisão e voltar à página da palavra?',
    'vi': 'Dừng ôn tập và quay lại trang từ vựng?', 'th': 'หยุดทบทวนและกลับไปที่หน้าคำศัพท์ไหม?',
    'en': 'Stop reviewing and go back to the word page?', 'en-in': 'Stop reviewing and go back to the word page?',
}
EXIT_ARIA_BY_LOCALE = {
    'ja': 'ゲームをやめる', 'zh-hant': '結束遊戲', 'zh-cn': '结束游戏', 'ko': '게임 그만두기', 'de': 'Spiel beenden',
    'fr': 'Quitter la partie', 'es-es': 'Salir del juego', 'es-mx': 'Salir del juego', 'it': 'Esci dal gioco',
    'pt': 'Sair do jogo', 'pt-br': 'Sair do jogo', 'vi': 'Thoát trò chơi', 'th': 'ออกจากเกม', 'en': 'Quit game', 'en-in': 'Quit game',
}


class PlayScreenNavAndExitTests(TestCase):
    def setUp(self):
        self.word = ThankJapanModel.objects.create(name='exit-word', englishname='Dog', jpname='いぬ',
                                                   category='animal', slug='exit-word', description='d', history='h')

    def _play(self, client, difficulty='timeattack', lang=None):
        _start_session_game(client, [self.word], difficulty=difficulty)
        return client.get(reverse('game_play'), {'lang': lang} if lang else {}).content.decode()

    def _exit_button(self, body):
        start = body.index('id="game-exit-btn"')
        return body[body.rindex('<button', 0, start):body.index('</button>', start)]

    def test_play_screen_hides_the_bottom_nav(self):
        body = self._play(self.client)
        self.assertIn(HIDE_NAV_CSS, body)
        self.assertIn('<nav class="bottom-nav">', body)  # still in the DOM for the shared scripts

    def test_start_and_result_screens_keep_the_bottom_nav(self):
        start = self.client.get(reverse('game_start')).content.decode()
        self.assertIn('<nav class="bottom-nav">', start)
        self.assertNotIn(HIDE_NAV_CSS, start)
        _start_session_game(self.client, [self.word])
        session = self.client.session
        session['game_history'] = [{'index': 0, 'is_correct': True, 'points': 1, 'question_id': self.word.id,
                                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                                    'user_input': 'Dog', 'correct_answer': 'Dog'}]
        session.save()
        result = self.client.get(reverse('game_result')).content.decode()
        self.assertIn('<nav class="bottom-nav">', result)
        self.assertNotIn(HIDE_NAV_CSS, result)

    def test_bottom_spacing_and_dynamic_viewport_height(self):
        body = self._play(self.client)
        self.assertIn('height: 100vh; height: 100dvh;', body)
        self.assertIn('padding-bottom: calc(24px + env(safe-area-inset-bottom));', body)
        self.assertNotIn('padding-bottom: calc(100px + env(safe-area-inset-bottom));', body)

    def test_exit_button_visible_in_a_normal_game_and_goes_to_game_start(self):
        body = self._play(self.client)
        button = self._exit_button(body)
        self.assertNotIn(' hidden', button)
        self.assertIn('>×', button)
        self.assertIn(f'const GAME_START_URL = "{reverse("game_start")}?lang=en";', body)
        self.assertIn('return GAME_START_URL;', body)

    def test_exit_button_hidden_server_side_for_first_taste_and_nav_tutorial(self):
        self.assertIn(' hidden', self._exit_button(self._play(Client(), difficulty='first_taste')))
        tsukemen = ThankJapanModel.objects.create(name='exit-tsukemen', englishname='Tsukemen', jpname='つけめん',
                                                  category='cook', slug=NAV_TUTORIAL_TARGET_SLUG, description='d', history='h')
        nav = Client()
        nav.cookies['tj_has_played'] = '1'
        nav.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': tsukemen.slug})
        self.assertIn(' hidden', self._exit_button(nav.get(reverse('game_play')).content.decode()))

    def test_exit_visibility_follows_the_tutorial_steps(self):
        body = self._play(self.client)
        # hidden while a guided step (first_taste Q1/Q2) or the nav tutorial is active, shown otherwise
        self.assertIn("btn.hidden = IS_NAV_TUTORIAL_SINGLE || currentQuestionTutorialMode !== null;", body)
        self.assertEqual(body.count('updateExitButton();'), 2)  # both the 3-choice and tile renderers

    def test_word_review_exit_returns_to_the_word_page(self):
        client = Client()
        client.get(reverse('game_restart'), {'mode': 'single', 'model_type': 'free', 'slug': self.word.slug})
        body = client.get(reverse('game_play')).content.decode()
        self.assertNotIn(' hidden', self._exit_button(body))
        detail = reverse('category_detail', kwargs={'category': 'animal', 'slug': self.word.slug})
        from django.utils.html import escapejs
        self.assertIn(f'const SINGLE_RETURN_URL = "{escapejs(detail)}";', body)
        self.assertIn('if (SINGLE_RETURN_URL) return SINGLE_RETURN_URL;', body)

    def test_first_taste_exit_goes_to_the_top_page(self):
        body = self._play(Client(), difficulty='first_taste')
        self.assertIn("if (GAME_DIFFICULTY === 'first_taste') {", body)
        self.assertIn("const GAME_DIFFICULTY = 'first_taste';", body)

    def test_abandoning_a_game_is_not_counted_as_completed(self):
        _start_session_game(self.client, [self.word])
        self.client.get(reverse('game_question_data'))
        _submit_choice(self.client, 0)
        # "×" just navigates away (to game_start) - the result screen is never reached
        self.client.get(reverse('game_start'))
        row = DailyActivitySummary.objects.get(activity_type=DailyActivitySummary.ACTIVITY_MAIN_GAME)
        self.assertEqual((row.games_started, row.games_completed), (1, 0))

    def test_exit_texts_in_every_language(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self._play(Client(), lang=lang)
                self.assertIn(EXIT_CONFIRM_BY_LOCALE[lang], body)
                self.assertIn(EXIT_REVIEW_CONFIRM_BY_LOCALE[lang], body)
                self.assertIn(f'aria-label="{EXIT_ARIA_BY_LOCALE[lang]}"', self._exit_button(body).replace('&#x27;', "'"))
                self.assertIn(HIDE_NAV_CSS, body)


# ---------------------------------------------------------------------------
# 2026-11-20: game-screen fixed text in 15 languages (combo gauge label,
# "COMBO MAX", the "N COMBO!" popup, screen-reader labels), and guards that a
# 3-choice question never offers an empty answer.
# ---------------------------------------------------------------------------

from django.core.exceptions import ValidationError

COMBO_LABEL_BY_LOCALE = {
    'ja': '気(コンボ)', 'zh-hant': '連擊', 'zh-cn': '连击', 'ko': '콤보', 'de': 'SERIE', 'fr': 'SÉRIE',
    'es-es': 'RACHA', 'es-mx': 'RACHA', 'it': 'SERIE', 'pt': 'SEQUÊNCIA', 'pt-br': 'SEQUÊNCIA',
    'vi': 'CHUỖI', 'th': 'คอมโบ', 'en': 'COMBO', 'en-in': 'COMBO',
}
COMBO_POPUP_BY_LOCALE = {
    'ja': '__N__コンボ！', 'zh-hant': '__N__ 連擊！', 'zh-cn': '__N__ 连击！', 'ko': '__N__ 콤보!', 'de': '__N__ in Folge!',
    'fr': 'Série de __N__ !', 'es-es': '¡Racha de __N__!', 'es-mx': '¡Racha de __N__!', 'it': 'Serie di __N__!',
    'pt': 'Sequência de __N__!', 'pt-br': 'Sequência de __N__!', 'vi': 'Chuỗi __N__!', 'th': 'คอมโบ __N__!',
    'en': '__N__ COMBO!', 'en-in': '__N__ COMBO!',
}
COMBO_MAX_BY_LOCALE = {  # with max_combo = 4
    'ja': '気(コンボ) MAX 4', 'zh-hant': '最高連擊 4', 'zh-cn': '最高连击 4', 'ko': '최대 콤보 4', 'de': 'BESTE SERIE 4',
    'fr': 'MEILLEURE SÉRIE 4', 'es-es': 'MEJOR RACHA 4', 'es-mx': 'MEJOR RACHA 4', 'it': 'MIGLIOR SERIE 4',
    'pt': 'MELHOR SEQUÊNCIA 4', 'pt-br': 'MELHOR SEQUÊNCIA 4', 'vi': 'CHUỖI CAO NHẤT 4', 'th': 'คอมโบสูงสุด 4',
    'en': 'COMBO MAX 4', 'en-in': 'COMBO MAX 4',
}
SOUND_ARIA_BY_LOCALE = {
    'ja': 'サウンドの切り替え', 'zh-hant': '切換音效', 'zh-cn': '切换音效', 'ko': '사운드 켜기/끄기', 'de': 'Ton ein/aus',
    'fr': 'Activer/désactiver le son', 'es-es': 'Activar/desactivar el sonido', 'es-mx': 'Activar/desactivar el sonido',
    'it': "Attiva/disattiva l'audio", 'pt': 'Ativar/desativar o som', 'pt-br': 'Ativar/desativar o som',
    'vi': 'Bật/tắt âm thanh', 'th': 'เปิด/ปิดเสียง', 'en': 'Toggle Sound', 'en-in': 'Toggle Sound',
}
SHARE_ARIA_BY_LOCALE = {  # platform placeholder {p}
    'ja': '{p}でシェア', 'zh-hant': '分享到 {p}', 'zh-cn': '分享到 {p}', 'ko': '{p}에 공유', 'de': 'Auf {p} teilen',
    'fr': 'Partager sur {p}', 'es-es': 'Compartir en {p}', 'es-mx': 'Compartir en {p}', 'it': 'Condividi su {p}',
    'pt': 'Compartilhar no {p}', 'pt-br': 'Compartilhar no {p}', 'vi': 'Chia sẻ lên {p}', 'th': 'แชร์ไปที่ {p}',
    'en': 'Share on {p}', 'en-in': 'Share on {p}',
}


class GameScreenFixedTextLocaleTests(TestCase):
    def setUp(self):
        self.word = ThankJapanModel.objects.create(name='combo-word', englishname='Dog', jpname='いぬ',
                                                   category='animal', slug='combo-word', description='d', history='h')

    def _result(self, client, lang):
        _start_session_game(client, [self.word])
        session = client.session
        session['game_history'] = [{'index': i, 'is_correct': True, 'points': 1, 'question_id': self.word.id,
                                    'question_model': 'free', 'combo': i + 1, 'combo_bonus': 0,
                                    'user_input': 'Dog', 'correct_answer': 'Dog'} for i in range(4)]
        session.save()
        return client.get(reverse('game_result'), {'lang': lang}).content.decode()

    def test_play_screen_combo_texts_and_sound_label_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                _start_session_game(client, [self.word])
                body = client.get(reverse('game_play'), {'lang': lang}).content.decode()
                self.assertIn(f'<span>{COMBO_LABEL_BY_LOCALE[lang]}</span>', body)
                self.assertIn(COMBO_POPUP_BY_LOCALE[lang], body)
                self.assertIn(f'aria-label="{SOUND_ARIA_BY_LOCALE[lang]}"', body)
                if lang not in ('en', 'en-in'):
                    self.assertNotIn('<span>COMBO</span>', body)
                    self.assertNotIn('aria-label="Toggle Sound"><span id="game-sound-icon">', body)

    def test_combo_popup_is_built_from_the_translated_text(self):
        client = Client()
        _start_session_game(client, [self.word])
        body = client.get(reverse('game_play')).content.decode()
        self.assertIn("el.textContent = i18n('i18n-combo-popup').replace('__N__', combo);", body)
        self.assertNotIn("combo + ' COMBO!'", body)
        self.assertIn('font-size: min(2.4rem, 8.5vw);', body)  # long localized popups stay on 320px screens

    def test_result_screen_combo_max_and_share_labels_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                body = self._result(Client(), lang)
                self.assertIn(COMBO_MAX_BY_LOCALE[lang], body)
                for platform in ('X', 'LINE', 'WhatsApp'):
                    self.assertIn(f'aria-label="{SHARE_ARIA_BY_LOCALE[lang].format(p=platform)}"', body)
                if lang not in ('en', 'en-in'):
                    self.assertNotIn('COMBO MAX 4', body)
                    self.assertNotIn('aria-label="Share on', body)


class ChoicesNeverEmptyTests(TestCase):
    """Regression guards (2026-11-20 audit): an empty answer button would be
    unanswerable - the empty submission is always judged wrong."""

    def setUp(self):
        # a word with no translations at all (English only), dummies with
        # some blank columns, one with English blank but others filled
        self.target = ThankJapanModel.objects.create(name='safe-a', englishname='Pajamas', jpname='ぱじゃま',
                                                     category='fashion', slug='safe-a', description='d', history='h')
        ThankJapanModel.objects.create(name='safe-b', englishname='Hat', englishname_fr='Chapeau', jpname='ぼうし',
                                       category='fashion', slug='safe-b', description='d', history='h')
        ThankJapanModel.objects.create(name='safe-c', englishname='Shoes', jpname='くつ', englishname_ko='',
                                       category='fashion', slug='safe-c', description='d', history='h')
        ThankJapanModel.objects.create(name='safe-d', englishname='Scarf', jpname='まふらー',
                                       category='fashion', slug='safe-d', description='d', history='h')

    def test_choices_never_contain_an_empty_answer_in_any_language(self):
        for lang in LOCALE_QUERY_PARAMS:
            for word in ThankJapanModel.objects.filter(category='fashion'):
                for _ in range(10):
                    with self.subTest(lang=lang, word=word.slug):
                        built = _build_choices(word, lang)
                        self.assertTrue(all(c.strip() for c in built['choices']), built)
                        self.assertTrue(built['answer_text'].strip())
                        self.assertEqual(built['choices'][built['answer_index']], built['answer_text'])

    def test_english_meaning_is_mandatory_so_an_all_blank_word_cannot_be_saved(self):
        for model in (ThankJapanModel, ThankJapanPremium):
            with self.subTest(model=model.__name__):
                self.assertFalse(model._meta.get_field('englishname').blank)
        word = ThankJapanModel(name='safe-blank', englishname='', jpname='から', category='fashion',
                               slug='safe-blank', description='d', history='h')
        with self.assertRaises(ValidationError) as ctx:
            word.full_clean()
        self.assertIn('englishname', ctx.exception.message_dict)


# ---------------------------------------------------------------------------
# 2026-11-20: no stylistic hyphens/dashes in the game wording, and one term
# for "combo" per Western language (the game screen's word) everywhere.
# ---------------------------------------------------------------------------

STREAK_RULE_BY_LOCALE = {
    'de': '⚡ Serienbonus +1 Pkt. ab 3 Richtigen in Folge!', 'fr': "⚡ Bonus de série +1 pt dès 3 bonnes réponses d'affilée !",
    'es-es': '⚡ ¡Bono de racha +1 pt a partir de 3 aciertos seguidos!', 'es-mx': '⚡ ¡Bono de racha +1 pt a partir de 3 aciertos seguidos!',
    'it': '⚡ Bonus serie +1 pt da 3 corrette di fila!', 'pt': '⚡ Bônus de sequência +1 pt a partir de 3 acertos seguidos!',
    'pt-br': '⚡ Bônus de sequência +1 pt a partir de 3 acertos seguidos!', 'vi': '⚡ Thưởng chuỗi +1đ khi trả lời đúng 3 câu liên tiếp trở lên!',
}
STREAK_RESULT_LINE_BY_LOCALE = {  # max_combo = 3
    'de': 'Beste Serie: 3! Serienbonus', 'fr': 'Meilleure série : 3 ! Bonus de série', 'es-es': '¡Mejor racha: 3! Bono de racha',
    'es-mx': '¡Mejor racha: 3! Bono de racha', 'it': 'Miglior serie: 3! Bonus serie', 'pt': 'Melhor sequência: 3! Bônus de sequência',
    'pt-br': 'Melhor sequência: 3! Bônus de sequência', 'vi': 'Chuỗi cao nhất: 3! Thưởng chuỗi',
}
STREAK_SHARE_BY_LOCALE = {  # mid tier, max_combo = 3
    'de': '⚡Beste Serie: 3!', 'fr': '⚡Meilleure série : 3 !', 'es-es': '⚡¡Mejor racha: 3!', 'es-mx': '⚡¡Mejor racha: 3!',
    'it': '⚡Miglior serie: 3!', 'pt': '⚡Melhor sequência: 3!', 'pt-br': '⚡Melhor sequência: 3!', 'vi': '⚡Chuỗi cao nhất 3!',
}
STREAK_BADGES_BY_LOCALE = {  # (combo_7, combo_12)
    'de': ('Serieneinsteiger', 'Serienmeister'), 'fr': ('Débutant de Série', 'Maître des Séries'),
    'es-es': ('Racha Inicial', 'Maestro de la Racha'), 'es-mx': ('Racha Inicial', 'Maestro de la Racha'),
    'it': ('Serie Iniziale', 'Maestro della Serie'), 'pt': ('Sequência Iniciante', 'Mestre da Sequência'),
    'pt-br': ('Sequência Iniciante', 'Mestre da Sequência'), 'vi': ('Khởi Đầu Chuỗi', 'Bậc Thầy Chuỗi'),
}
COMBO_KEPT_BY_LOCALE = {'ja': 'コンボ', 'ko': '콤보', 'zh-cn': '连击', 'zh-hant': '連擊', 'th': 'คอมโบ'}
STREAK_LANGS = list(STREAK_RULE_BY_LOCALE)


class WordingConsistencyTests(TestCase):
    def setUp(self):
        _create_social_apps()
        self.word = ThankJapanModel.objects.create(name='wording-word', englishname='Dog', jpname='いぬ',
                                                   category='animal', slug='wording-word', description='d', history='h')

    def _result(self, client, lang):
        _start_session_game(client, [self.word])
        session = client.session
        session['game_history'] = [{'index': i, 'is_correct': True, 'points': 1, 'question_id': self.word.id,
                                    'question_model': 'free', 'combo': i + 1, 'combo_bonus': 0,
                                    'user_input': 'Dog', 'correct_answer': 'Dog'} for i in range(3)]
        session.save()
        return client.get(reverse('game_result'), {'lang': lang}).content.decode()

    def test_one_streak_term_on_start_result_share_and_badges(self):
        user = User.objects.create_user('wording-user', password='pw-for-tests-only')
        for code in ('combo_7', 'combo_12'):
            Achievement.objects.create(user=user, code=code)
        for lang in STREAK_LANGS:
            with self.subTest(lang=lang):
                start = Client().get(reverse('game_start'), {'lang': lang}).content.decode()
                self.assertIn(STREAK_RULE_BY_LOCALE[lang], start)
                result = self._result(Client(), lang)
                self.assertIn(STREAK_RESULT_LINE_BY_LOCALE[lang], ' '.join(result.split()))
                self.assertTrue(build_combo_share_message(lang, 3, 10).startswith(STREAK_SHARE_BY_LOCALE[lang]))
                for screen in (start, result):
                    # visible wording only (CSS class names like .combo-bonus are code, not text)
                    self.assertNotRegex(screen, r'Bonus combo|Bono (de )?combo|Combo-Bonus|Bônus de combo|Thưởng combo')
        account_urls = {'de': 'account_settingsde', 'fr': 'account_settingsfr', 'es-es': 'account_settingsesES',
                        'es-mx': 'account_settingsesMX', 'it': 'account_settingsit', 'pt': 'account_settingspt',
                        'pt-br': 'account_settingsptBR', 'vi': 'account_settingsvi'}
        client = Client()
        client.force_login(user)
        for lang, url_name in account_urls.items():
            with self.subTest(lang=lang, page='account_settings'):
                body = client.get(reverse(url_name)).content.decode()
                for name in STREAK_BADGES_BY_LOCALE[lang]:
                    self.assertIn(name, body)
        # the badge *codes* (earned records) are unchanged
        self.assertEqual(set(Achievement.objects.filter(user=user).values_list('code', flat=True)), {'combo_7', 'combo_12'})

    def test_badge_names_on_the_result_toast(self):
        user = User.objects.create_user('wording-toast', password='pw-for-tests-only')
        Profile.objects.filter(user=user).update(best_combo=0)
        client = Client()
        client.force_login(user)
        for lang in STREAK_LANGS:
            with self.subTest(lang=lang):
                Achievement.objects.filter(user=user).delete()
                _start_session_game(client, [self.word])
                session = client.session
                session['game_history'] = [{'index': i, 'is_correct': True, 'points': 1, 'question_id': self.word.id,
                                            'question_model': 'free', 'combo': i + 1, 'combo_bonus': 0,
                                            'user_input': 'Dog', 'correct_answer': 'Dog'} for i in range(12)]
                session.pop('achievements_checked', None)
                session.save()
                body = client.get(reverse('game_result'), {'lang': lang}).content.decode()
                toast = body[body.index('id="achievement-toast"'):body.index('</div>\n<script>')]
                for name in STREAK_BADGES_BY_LOCALE[lang]:
                    self.assertIn(name, toast)

    def test_asian_languages_keep_their_combo_word(self):
        for lang, word in COMBO_KEPT_BY_LOCALE.items():
            with self.subTest(lang=lang):
                self.assertIn(word, self._result(Client(), lang))

    def test_no_stylistic_hyphens_in_the_reworded_texts(self):
        removed = ['45-second', '45-Sekunden', '-Sekunden-Challenge', '-Second Challenge', 'er-Serie', 'Level-Up!',
                   'Reise-Etikette', 'Business-Japanisch', 'Inscrivez-vous pour faire monter',
                   'Cadastre-se para subir de nível', 'Maximal-Combo', 'Combo-Einsteiger', 'Combo-Meister']
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                pages = [Client().get(reverse(name), {'lang': lang}).content.decode()
                         for name in ('game_start', 'player_login', 'premium_infode')]
                pages.append(self._result(Client(), lang))
                for tier_combo in (1, 3, 7):
                    pages.append(build_combo_share_message(lang, tier_combo, 10))
                for text in removed:
                    for page in pages:
                        self.assertNotIn(text, page)
        # "Premium-Wörter": reworded on How to Play and the premium page; the
        # older start-screen rule line keeps it (not part of this change)
        for name in ('player_login', 'premium_infode'):
            self.assertNotIn('Premium-Wörter', Client().get(reverse(name), {'lang': 'de'}).content.decode())
        self.assertEqual(ranks.category_short_label('TourismEtiquette', 'de'), '🗺️ Reiseknigge')
        self.assertEqual(ranks.category_short_label('BusinessJapanese', 'de'), '👔 Japanisch für den Beruf')

    def test_grammatical_hyphens_are_kept(self):
        _start_session_game(self.client, [self.word])
        body = self.client.get(reverse('game_play'), {'lang': 'fr'}).content.decode()
        self.assertIn('Inscrivez-vous', body)       # existing French imperative, untouched
        pt = self.client.get(reverse('game_play'), {'lang': 'pt'}).content.decode()
        self.assertIn('Cadastre-se', pt)              # existing Portuguese clitic, untouched


class AdvancedWordsRuleTests(TestCase):
    """2026-11-20: the 2pt rule is described as "advanced words (LvN and up)",
    never as a "premium" perk, with N taken from ranks.py."""

    HTP_BY_LOCALE = {
        'ja': '💎 上級の単語(Lv8から)は2pt、', 'zh-hant': '💎 進階單字（Lv8起）2分、', 'zh-cn': '💎 进阶单词（Lv8起）2分、',
        'ko': '💎 상급 단어(Lv8부터) 2점, ', 'de': '💎 fortgeschrittene Wörter (ab Lv8) 2 Pkt., ', 'fr': '💎 mots avancés (dès Lv8) 2 pts, ',
        'es-es': '💎 palabras avanzadas (desde Lv8) 2 pts, ', 'es-mx': '💎 palabras avanzadas (desde Lv8) 2 pts, ',
        'it': '💎 parole avanzate (dal Lv8) 2 pt, ', 'pt': '💎 palavras avançadas (a partir do Lv8) 2 pts, ',
        'pt-br': '💎 palavras avançadas (a partir do Lv8) 2 pts, ', 'vi': '💎 từ nâng cao (từ Lv8) 2 điểm, ',
        'th': '💎 คำศัพท์ระดับสูง (ตั้งแต่ Lv8) 2 คะแนน, ', 'en': '💎 advanced words (Lv8 and up) 2pt, ',
        'en-in': '💎 advanced words (Lv8 and up) 2pt, ',
    }
    PREMIUM_WORDING = re.compile(r'💎[^<]*(?:[Pp]remium|プレミアム|프리미엄|พรีเมียม|2倍|2배|2 เท่า|雙倍|双倍|doppelte|doble|doppio|dobro|gấp đôi|double)')

    def setUp(self):
        _create_social_apps()

    def test_level_comes_from_ranks_py(self):
        self.assertEqual(ranks.ADVANCED_WORDS_LEVEL, 8)
        first = next(r['level'] for r in ranks.RANK_LEVELS if set(r['categories']) & ranks.PREMIUM_TABLE_CATEGORIES)
        self.assertEqual(ranks.ADVANCED_WORDS_LEVEL, first)
        self.assertEqual(ranks.PREMIUM_TABLE_CATEGORIES, set(PREMIUM_CATEGORIES))
        with patch.object(tj_views, 'ADVANCED_WORDS_LEVEL', 6):
            self.assertIn('(Lv6 and up)', self.client.get(reverse('game_start')).content.decode())
            self.assertIn('(Lv6 and up)', self.client.get(reverse('player_login')).content.decode())

    def test_start_screen_and_how_to_play_per_locale(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                start = Client().get(reverse('game_start'), {'lang': lang}).content.decode()
                self.assertIn(GAME_START_DOUBLE_POINTS_TEXT_BY_LOCALE[lang], start)
                login = Client().get(reverse('player_login'), {'lang': lang}).content.decode()
                self.assertIn(self.HTP_BY_LOCALE[lang], login)
                for page in (start, login):
                    self.assertIsNone(self.PREMIUM_WORDING.search(page), self.PREMIUM_WORDING.search(page))
                    rule = [line for line in page.split('\n') if '💎' in line]
                    self.assertTrue(rule)
                    for line in rule:
                        self.assertNotRegex(re.sub(r'<[^>]+>', '', line), '[\u2013\u2014]|\w-\w')


# ---------------------------------------------------------------------------
# 2026-11-20: brackets in the game display (game_display.py). A bracket is
# dropped only when it repeats the word's reading; type C brackets (part of
# the meaning), Hangul/Thai values and multi-bracket values stay as stored.
# Readings lose every bracket. Detail/list pages keep the stored value.
# ---------------------------------------------------------------------------

from .game_display import game_meaning, strip_brackets, word_game_meaning  # noqa: E402


class GameMeaningRuleTests(SimpleTestCase):
    """Real production values from the 2026-11-20 audit."""

    def test_reading_bracket_is_dropped(self):
        cases = [
            ('Colador (Zaru)', 'zaru', 'ざる', 'Colador'),
            ('漏勺 (Zaru)', 'zaru', 'ざる', '漏勺'),
            ('入学考试 (Juken)', 'juken', 'じゅけん', '入学考试'),
            ('Đồn cảnh sát nhỏ (Koban)', 'kouban', 'こうばん', 'Đồn cảnh sát nhỏ'),
            ('蓬 (よもぎ)', 'yomogi', 'よもぎ', '蓬'),
            ('蓬（よもぎ）', 'yomogi', 'よもぎ', '蓬'),
            ('Teeschale (Chawan)', 'chawan', 'ちゃわん', 'Teeschale'),
        ]
        for value, romaji, jpname, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(game_meaning(value, romaji, jpname), expected)

    def test_romaji_outside_shows_only_the_bracketed_meaning(self):
        cases = [
            ('Kyūsu (Théière japonaise)', 'kyuusu', 'きゅうす', 'Théière japonaise'),
            ('Motsunabe (Offal Hot Pot)', 'motsunabe', 'もつなべ', 'Offal Hot Pot'),
            ('Katsuobushi (Bonitoflocken)', 'katsuobushi', 'かつおぶし', 'Bonitoflocken'),
            ('Kyōka Suigetsu (Blume im Spiegel, Mond im Wasser)', 'kyoukasuigetsu', 'きょうかすいげつ',
             'Blume im Spiegel, Mond im Wasser'),
        ]
        for value, romaji, jpname, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(game_meaning(value, romaji, jpname), expected)

    def test_meaning_brackets_stay(self):
        cases = [
            ('To take (a photo)', 'toru', 'とる'),
            ('Prendre (une photo)', 'toru', 'とる'),
            ('Lavare (i denti)', 'migaku', 'みがく'),
            ('撮る（写真を）', 'toru', 'とる'),
            ('Hoa cúc họa mi (Marguerite)', 'maagaretto', 'まーがれっと'),
            ('唱片骑师 (DJ)', 'diijee', 'でぃーじぇー'),
        ]
        for value, romaji, jpname in cases:
            with self.subTest(value=value):
                self.assertEqual(game_meaning(value, romaji, jpname), value)

    def test_hangul_and_thai_values_are_never_changed(self):
        cases = [
            ('미소 (일본식 된장)', 'miso', 'みそ'),
            ('찻잔 (Chawan)', 'chawan', 'ちゃわん'),  # the bracket matches, but Hangul -> keep
            ('Chawan (찻잔)', 'chawan', 'ちゃわん'),
            ('กาน้ำชา (Kyusu)', 'kyuusu', 'きゅうす'),
        ]
        for value, romaji, jpname in cases:
            with self.subTest(value=value):
                self.assertEqual(game_meaning(value, romaji, jpname), value)

    def test_multiple_or_unbalanced_brackets_stay(self):
        for value in ('Petit(e) ami(e)', "Recherche active d'un(e) futur(e) époux", '김 (노리', 'Nori (Zaru', 'Zaru) x (y'):
            with self.subTest(value=value):
                self.assertEqual(game_meaning(value, 'zaru', 'ざる'), value)

    def test_never_empty_and_whitespace_is_collapsed(self):
        self.assertEqual(game_meaning('(Zaru)', 'zaru', 'ざる'), 'Zaru')
        self.assertEqual(game_meaning('()', 'zaru', 'ざる'), '()')
        self.assertEqual(game_meaning('  Sieb   (Zaru) ', 'zaru', 'ざる'), 'Sieb')
        self.assertEqual(game_meaning('', 'zaru', 'ざる'), '')
        self.assertEqual(game_meaning(None, 'zaru', 'ざる'), '')

    def test_reading_brackets_are_all_removed(self):
        cases = [
            ('kaki(kudamono)', 'kaki'), ('botan(fashion)', 'botan'), ('Madori (1K, 1LDK, etc.)', 'Madori'),
            ('Kitchin(kicchin) ga hiroi desu', 'Kitchin ga hiroi desu'), ('kaki（kaisen）', 'kaki'),
            ('a (b (c)) d', 'a d'), ('(only)', 'only'), ('nori (food', 'nori (food'), ('ramen', 'ramen'), ('', ''),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(strip_brackets(value), expected)


class GameBracketDisplayTests(TestCase):
    def setUp(self):
        self.zaru = _make_word('b-zaru', 'Colander (Zaru)', 'ざる', category='householditems', name='zaru(kitchen)',
                               englishname_fr='Passoire (Zaru)', englishname_ko='소쿠리 (Zaru)')
        self.kyusu = _make_word('b-kyusu', 'Teapot', 'きゅうす', category='householditems', name='kyuusu',
                                englishname_fr='Kyūsu (Théière japonaise)')
        self.take = _make_word('b-take', 'To take (a photo)', 'とる', category='householditems', name='toru')
        self.shower = _make_word('b-shower', 'To take (a shower)', 'あびる', category='householditems', name='abiru')
        _make_word('b-pot', 'Pot', 'なべ', category='householditems', name='nabe')

    def test_choices_show_the_game_meaning_in_every_language(self):
        for lang, col in MEANING_COLUMNS.items():
            for word in (self.zaru, self.kyusu, self.take, self.shower):
                with self.subTest(lang=lang, word=word.slug):
                    built = _build_choices(word, lang)
                    expected = word_game_meaning(word, col) or word_game_meaning(word, 'englishname')
                    self.assertEqual(built['answer_text'], expected)
                    self.assertEqual(built['choices'][built['answer_index']], expected)
                    shown = ' '.join(c for c in built['choices'] if '소쿠리' not in c)
                    self.assertNotIn('(Zaru)', shown)
                    self.assertNotIn('Kyūsu', shown)
        self.assertEqual(_build_choices(self.zaru, 'en')['answer_text'], 'Colander')
        self.assertEqual(_build_choices(self.zaru, 'ko')['answer_text'], '소쿠리 (Zaru)')
        self.assertEqual(_build_choices(self.kyusu, 'fr')['answer_text'], 'Théière japonaise')
        self.assertEqual(_build_choices(self.take, 'en')['answer_text'], 'To take (a photo)')

    def test_words_that_look_the_same_after_stripping_are_never_shown_together(self):
        belt = _make_word('b-belt', 'Belt', 'べると', category='fashion', name='beruto', englishname_zh_cn='腰带')
        obi = _make_word('b-obi', 'Obi', 'おび', category='fashion', name='obi', englishname_zh_cn='腰带 (Obi)')
        for i in range(3):
            _make_word(f'b-hat{i}', f'Hat{i}', f'ぼうし{i}', category='fashion', englishname_zh_cn=f'帽子{i}')
        for word in (belt, obi):
            for _ in range(30):
                built = _build_choices(word, 'zh-cn')
                self.assertEqual(built['choices'].count('腰带'), 1, built)
                self.assertEqual(len({_meaning_key(c) for c in built['choices']}), 3)

    def test_question_reading_has_no_brackets_and_the_stripped_answer_is_judged_correct(self):
        _start_session_game(self.client, [self.zaru])
        data = json.loads(self.client.get(reverse('game_question_data') + '?lang=fr').content)
        self.assertEqual(data['romaji'], 'zaru')
        self.assertIn('Passoire', data['choices'])
        self.assertFalse(any('Zaru' in c for c in data['choices']), data['choices'])
        result = json.loads(_submit_choice(self.client, data['choices'].index('Passoire')).content)
        self.assertTrue(result['is_correct'], result)

    def test_premium_romaji_reading_is_stripped(self):
        word = ThankJapanPremium.objects.create(
            name='b-madori', englishname='Room layout', jpname='まどり', romaji='Madori (1K, 1LDK, etc.)',
            category='RealEstateRules', slug='b-madori', description='d', history='h')
        self.assertEqual(tj_views._reading_for(word), ('まどり', 'Madori'))

    def test_result_review_card_shows_the_reading_without_brackets(self):
        session = self.client.session
        session['game_history'] = [{'index': 0, 'is_correct': True, 'points': 1, 'question_id': self.zaru.id,
                                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0, 'user_input': 'Colander',
                                    'correct_answer': 'Colander'}]
        session['game_difficulty'] = 'timeattack'
        session.save()
        body = self.client.get(reverse('game_result')).content.decode()
        self.assertIn('(Zaru)', re.sub(r'\s+', '', body))
        self.assertNotIn('Zaru(Kitchen)', body)
        self.assertNotIn('zaru(kitchen)', body)

    def test_detail_page_keeps_the_stored_value(self):
        response = self.client.get(reverse('category_detail', kwargs={'category': 'householditems', 'slug': 'b-zaru'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Colander (Zaru)')


class DailyQuestionBracketTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.word = _make_word('dq-zaru', 'Colander (Zaru)', 'ざる', category='householditems', name='zaru(kitchen)',
                               englishname_fr='Passoire (Zaru)')
        for i, en in enumerate(['Clock', 'Chair', 'Pen']):
            _make_word(f'dq-other-{i}', en, f'もの{i}', category='householditems')
        DailyQuestion.objects.create(date=self.today, word=self.word)

    def test_daily_choices_reading_and_judging_use_the_game_display(self):
        for lang, expected in (('en', 'Colander'), ('fr', 'Passoire')):
            with self.subTest(lang=lang):
                client = Client()
                response = client.get(reverse('daily_question'), {'lang': lang})
                choices = response.context['choices']
                self.assertIn(expected, choices)
                self.assertEqual(response.context['romaji'], 'zaru')
                self.assertNotIn('zaru(kitchen)', response.content.decode())
                result = client.post(reverse('daily_question') + f'?lang={lang}', {'answer_choice': choices.index(expected)})
                self.assertTrue(result.context['is_correct'])
                self.assertEqual(result.context['correct_meaning'], expected)
                body = result.content.decode()
                self.assertIn('zaru / Colander', body)
                self.assertNotIn('zaru(kitchen)', body)


# ---------------------------------------------------------------------------
# 2026-11-21: clues by level (ranks.CLUE_TIERS). Lv1-3 as before, Lv4-6 no
# romaji until answered, Lv7-10 also a smaller picture. Phrase categories
# get the next easier tier. Logged-in timeattack only, level fixed at start.
# ---------------------------------------------------------------------------

LEVEL_SCORES = {1: 0, 3: 350, 4: 600, 6: 1300, 7: 1800, 8: 2400, 10: 4000}
CLUE_ROMAJI_NOTICE_BY_LOCALE = {
    'en': 'From Lv4, romaji is shown after you answer.', 'en-in': 'From Lv4, romaji is shown after you answer.',
    'ja': 'Lv4から、ローマ字は答えたあとに表示されます。', 'zh-hant': '從Lv4起，羅馬字會在作答後顯示。',
    'zh-cn': '从Lv4起，罗马字会在作答后显示。', 'ko': 'Lv4부터 로마자는 답한 뒤에 표시돼요.',
    'de': 'Ab Lv4 erscheint Romaji erst nach deiner Antwort.', 'fr': "À partir du Lv4, le romaji s'affiche après ta réponse.",
    'es-es': 'Desde el Lv4, el romaji aparece después de responder.', 'es-mx': 'Desde el Lv4, el romaji aparece después de responder.',
    'it': 'Dal Lv4, il romaji appare dopo la risposta.', 'pt': 'A partir do Lv4, o romaji aparece depois da resposta.',
    'pt-br': 'A partir do Lv4, o romaji aparece depois da resposta.', 'vi': 'Từ Lv4, romaji sẽ hiện sau khi bạn trả lời.',
    'th': 'ตั้งแต่ Lv4 โรมาจิจะแสดงหลังจากตอบแล้ว',
}
CLUE_IMAGE_NOTICE_BY_LOCALE = {
    'en': 'From Lv7, pictures are smaller.', 'en-in': 'From Lv7, pictures are smaller.', 'ja': 'Lv7から、画像が小さくなります。',
    'zh-hant': '從Lv7起，圖片會變小。', 'zh-cn': '从Lv7起，图片会变小。', 'ko': 'Lv7부터 그림이 작아져요.',
    'de': 'Ab Lv7 werden die Bilder kleiner.', 'fr': 'À partir du Lv7, les images sont plus petites.',
    'es-es': 'Desde el Lv7, las imágenes son más pequeñas.', 'es-mx': 'Desde el Lv7, las imágenes son más pequeñas.',
    'it': 'Dal Lv7, le immagini sono più piccole.', 'pt': 'A partir do Lv7, as imagens ficam menores.',
    'pt-br': 'A partir do Lv7, as imagens ficam menores.', 'vi': 'Từ Lv7, hình ảnh sẽ nhỏ hơn.', 'th': 'ตั้งแต่ Lv7 รูปภาพจะเล็กลง',
}


class ClueRuleDefinitionTests(SimpleTestCase):
    def test_tiers_by_level_for_word_categories(self):
        expected = {1: (True, 'full'), 2: (True, 'full'), 3: (True, 'full'), 4: (False, 'full'), 5: (False, 'full'),
                    6: (False, 'full'), 7: (False, 'small'), 8: (False, 'small'), 9: (False, 'small'), 10: (False, 'small')}
        for level, (romaji, image) in expected.items():
            with self.subTest(level=level):
                self.assertEqual(ranks.clue_rules_for(level, 'food'), {'romaji': romaji, 'image': image})

    def test_phrase_categories_get_the_next_easier_tier(self):
        for category in ranks.PHRASE_CATEGORIES:
            for level in (8, 9, 10):
                with self.subTest(category=category, level=level):
                    self.assertEqual(ranks.clue_rules_for(level, category), {'romaji': False, 'image': 'full'})
        self.assertEqual(ranks.clue_rules_for(10, 'Entertainment'), {'romaji': False, 'image': 'small'})
        self.assertEqual(ranks.clue_rules_for(10, 'Prefectures'), {'romaji': False, 'image': 'small'})
        self.assertTrue(ranks.PHRASE_CATEGORIES <= set(ranks.categories_for_level(ranks.MAX_LEVEL)))

    def test_levels_and_changes_crossed(self):
        self.assertEqual((ranks.ROMAJI_HIDDEN_LEVEL, ranks.SMALL_IMAGE_LEVEL), (4, 7))
        self.assertEqual(ranks.clue_changes_between(3, 4), ['romaji'])
        self.assertEqual(ranks.clue_changes_between(6, 7), ['image'])
        self.assertEqual(ranks.clue_changes_between(2, 8), ['romaji', 'image'])
        self.assertEqual(ranks.clue_changes_between(4, 6), [])
        self.assertEqual(ranks.clue_changes_between(1, 2), [])


class ClueGameplayTests(TestCase):
    def setUp(self):
        self.word = _make_word('clue-sushi', 'Sushi', 'すし', category='food', name='sushi(food)')
        for i, en in enumerate(['Apple', 'Rice', 'Egg']):
            _make_word(f'clue-other-{i}', en, f'もの{i}', category='food')
        self.phrase = ThankJapanPremium.objects.create(
            name='clue-phrase', englishname='Good morning', jpname='おはようございます', romaji='Ohayou gozaimasu',
            category='DailyConversation', slug='clue-phrase', description='d', history='h')

    def _start(self, client, level, words=None, model='free'):
        user = _user_with_score(f'clue-user-{level}-{model}-{User.objects.count()}', LEVEL_SCORES[level])
        client.force_login(user)
        client.get(reverse('game_restart'))
        session = client.session
        session['game_question_ids'] = [{'id': w.id, 'model': model} for w in (words or [self.word])]
        session.save()
        return user

    def _question(self, client):
        return json.loads(client.get(reverse('game_question_data')).content)

    def test_question_clues_follow_the_level(self):
        expected = {1: ('sushi', 'full'), 3: ('sushi', 'full'), 4: ('', 'full'), 6: ('', 'full'),
                    7: ('', 'small'), 10: ('', 'small')}
        for level, (romaji, image) in expected.items():
            with self.subTest(level=level):
                client = Client()
                self._start(client, level)
                data = self._question(client)
                self.assertEqual(data['romaji'], romaji)
                self.assertEqual(data['clue'], {'romaji': bool(romaji), 'image': image})
                self.assertEqual(data['reading'], 'すし')

    def test_hidden_romaji_is_returned_with_the_answer(self):
        self._start(self.client, 4)
        data = self._question(self.client)
        self.assertEqual(data['romaji'], '')
        self.assertNotIn('sushi', json.dumps(data))
        result = json.loads(_submit_choice(self.client, 0).content)
        self.assertEqual(result['romaji'], 'sushi')

    def test_phrase_category_is_one_tier_easier(self):
        self._start(self.client, 10, words=[self.phrase], model='premium')
        data = self._question(self.client)
        self.assertEqual(data['clue'], {'romaji': False, 'image': 'full'})
        self.assertEqual(data['romaji'], '')

    def test_level_is_fixed_when_the_game_starts(self):
        user = self._start(self.client, 3, words=[self.word, self.word])
        self.assertEqual(self._question(self.client)['clue']['romaji'], True)
        Profile.objects.filter(user=user).update(total_score=LEVEL_SCORES[7], max_level=7)
        _submit_choice(self.client, 0)
        self.assertEqual(self._question(self.client)['clue'], {'romaji': True, 'image': 'full'})

    def test_excluded_modes_always_get_full_clues(self):
        # guest timed game
        guest = Client()
        guest.get(reverse('game_restart'))
        session = guest.session
        session['game_question_ids'] = [{'id': self.word.id, 'model': 'free'}]
        session.save()
        data = self._question(guest)
        self.assertEqual((data['romaji'], data['clue']), ('sushi', {'romaji': True, 'image': 'full'}))
        # 3-question guest trial
        trial = Client()
        trial.get(reverse('game_restart'), {'difficulty': 'first_taste'})
        self.assertEqual(trial.session.get('game_difficulty'), 'first_taste')
        self.assertIsNone(trial.session.get('game_clue_level'))
        self.assertEqual(self._question(trial)['clue'], {'romaji': True, 'image': 'full'})
        # single-word review by a Lv10 player
        single = Client()
        single.force_login(_user_with_score('clue-single', LEVEL_SCORES[10]))
        single.get(reverse('game_restart'), {'mode': 'single', 'slug': self.word.slug})
        data = self._question(single)
        self.assertEqual((data['romaji'], data['clue']), ('sushi', {'romaji': True, 'image': 'full'}))
        # Daily Question for a Lv10 player
        DailyQuestion.objects.create(date=timezone.localdate(), word=self.word)
        daily = Client()
        daily.force_login(_user_with_score('clue-daily', LEVEL_SCORES[10]))
        self.assertEqual(daily.get(reverse('daily_question'), {'lang': 'en'}).context['romaji'], 'sushi')

    def test_a_timed_game_after_single_review_gets_the_level_again(self):
        client = Client()
        client.force_login(_user_with_score('clue-switch', LEVEL_SCORES[7]))
        client.get(reverse('game_restart'), {'mode': 'single', 'slug': self.word.slug})
        self.assertIsNone(client.session.get('game_clue_level'))
        client.get(reverse('game_restart'))
        self.assertEqual(client.session.get('game_clue_level'), 7)

    def test_play_screen_has_the_clue_hooks(self):
        self._start(self.client, 7)
        body = self.client.get(reverse('game_play')).content.decode()
        for hook in ('id="prompt-card"', '.prompt-card.clue-image-small img', '.prompt-romaji.romaji-pending',
                     'function applyClues', 'revealRomaji(res.romaji)'):
            self.assertIn(hook, body)


class ClueLevelUpNoticeTests(TestCase):
    def setUp(self):
        self.word = ThankJapanModel.objects.create(name='cn-word', englishname='Dog', jpname='いぬ',
                                                   category='animal', slug='cn-word', description='d', history='h')

    def _finish(self, client, points, lang):
        session = client.session
        session['game_history'] = [{'index': 0, 'is_correct': True, 'points': points, 'question_id': self.word.id,
                                    'question_model': 'free', 'combo': 1, 'combo_bonus': 0,
                                    'user_input': 'Dog', 'correct_answer': 'Dog'}]
        session['game_difficulty'] = 'timeattack'
        session.save()
        return client.get(reverse('game_result'), {'lang': lang}).content.decode()

    def _notices(self, body):
        return re.findall(r'<p class="lu-clue" data-clue="(\w+)">(.*?)</p>', body, re.S)

    def test_notice_lines_per_locale_without_hyphens_or_dashes(self):
        for lang in LOCALE_QUERY_PARAMS:
            with self.subTest(lang=lang):
                client = Client()
                client.force_login(_user_with_score(f'cn-r-{lang}', 590))  # Lv3 -> Lv4
                notices = self._notices(self._finish(client, 15, lang))
                self.assertEqual([n[0] for n in notices], ['romaji'])
                self.assertIn(CLUE_ROMAJI_NOTICE_BY_LOCALE[lang], notices[0][1])

                client = Client()
                client.force_login(_user_with_score(f'cn-i-{lang}', 1790, max_level=6))  # Lv6 -> Lv7
                notices = self._notices(self._finish(client, 15, lang))
                self.assertEqual([n[0] for n in notices], ['image'])
                self.assertIn(CLUE_IMAGE_NOTICE_BY_LOCALE[lang], notices[0][1])
        for text in list(CLUE_ROMAJI_NOTICE_BY_LOCALE.values()) + list(CLUE_IMAGE_NOTICE_BY_LOCALE.values()):
            self.assertIsNone(re.search('[-‐-―−－]', text), text)

    def test_jump_shows_both_and_other_rises_show_none(self):
        client = Client()
        client.force_login(_user_with_score('cn-both', 340))  # Lv2 -> Lv8
        self.assertEqual([n[0] for n in self._notices(self._finish(client, 2100, 'en'))], ['romaji', 'image'])
        client = Client()
        client.force_login(_user_with_score('cn-none', 140))  # Lv1 -> Lv2
        body = self._finish(client, 15, 'en')
        self.assertIn('id="level-up-banner"', body)
        self.assertEqual(self._notices(body), [])


class RomajiRevealDelayTests(TestCase):
    """2026-11-21: a correct answer whose romaji was hidden waits
    ROMAJI_REVEAL_CORRECT_DELAY_MS (not 550ms) before the next question.
    Only questions that hid the romaji qualify, and only logged-in timed
    games from Lv4 hide it (see ClueGameplayTests)."""

    def setUp(self):
        self.word = _make_word('delay-sushi', 'Sushi', 'すし', category='food', name='sushi')

    def _play_page(self, client):
        return client.get(reverse('game_play')).content.decode()

    def test_one_constant_drives_the_delay(self):
        self.assertEqual(tj_views.ROMAJI_REVEAL_CORRECT_DELAY_MS, 1200)
        user = _user_with_score('delay-user', 600)
        self.client.force_login(user)
        self.client.get(reverse('game_restart'))
        body = self._play_page(self.client)
        self.assertIn('const ROMAJI_REVEAL_CORRECT_DELAY_MS = 1200;', body)
        self.assertIn('const romajiRevealed = revealRomaji(res.romaji);', body)
        self.assertIn('setTimeout(fetchQuestion, res.is_correct ? (romajiRevealed ? ROMAJI_REVEAL_CORRECT_DELAY_MS : 550) : wrongDelayMs);', body)
        # a miss keeps its own pause (1500ms for 3 choices)
        self.assertIn('}, 1500);', body)

    def test_only_hidden_romaji_questions_qualify(self):
        # revealRomaji reports a reveal only when the romaji was pending
        self.client.get(reverse('game_restart'))
        body = self._play_page(self.client)
        self.assertIn("if (!romajiEl || !romajiEl.classList.contains('romaji-pending') || !romaji) return false;", body)
        for level, hidden in ((3, False), (4, True)):
            with self.subTest(level=level):
                client = Client()
                client.force_login(_user_with_score(f'delay-lv{level}', LEVEL_SCORES[level]))
                client.get(reverse('game_restart'))
                session = client.session
                session['game_question_ids'] = [{'id': self.word.id, 'model': 'free'}]
                session.save()
                data = json.loads(client.get(reverse('game_question_data')).content)
                self.assertEqual(data['clue']['romaji'], not hidden)
