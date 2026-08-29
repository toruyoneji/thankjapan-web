import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Profile


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


@override_settings(SECURE_SSL_REDIRECT=False)
class PremiumInfoTrialPlanTemplateTests(TestCase):
    """The new trial-enabled PayPal plan puts the free TRIAL cycle at
    sequence 1 and the paid REGULAR cycle at sequence 2. The regional-price
    override in premium_info-v2.html (and its language variants) must target
    sequence 2 - overriding sequence 1 would silently replace the $0 trial
    price with the regional price, destroying the free trial."""

    # A real Cloudflare edge IP, so pricing.get_premium_price() trusts the
    # CF-IPCountry header instead of falling back to the USD default.
    CLOUDFLARE_IP = '173.245.48.1'

    def test_default_region_overrides_sequence_2_at_base_price(self):
        response = self.client.get(reverse('premium_info'))
        content = response.content.decode()

        self.assertIn("'sequence': 2,", content)
        self.assertNotIn("'sequence': 1,", content)
        self.assertIn("'value': '5.00'", content)

    def test_discounted_region_overrides_sequence_2_at_tier_price(self):
        response = self.client.get(
            reverse('premium_info'),
            REMOTE_ADDR=self.CLOUDFLARE_IP,
            HTTP_X_FORWARDED_FOR=self.CLOUDFLARE_IP,
            HTTP_CF_IPCOUNTRY='ID',  # tier A -> $1.75
        )
        content = response.content.decode()

        self.assertIn("'sequence': 2,", content)
        self.assertNotIn("'sequence': 1,", content)
        self.assertIn("'value': '1.75'", content)

    def test_language_variants_also_override_sequence_2(self):
        for url_name in ('premium_infoja', 'premium_infode', 'premium_infozhCN'):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                content = response.content.decode()
                self.assertIn("'sequence': 2,", content)
                self.assertNotIn("'sequence': 1,", content)
