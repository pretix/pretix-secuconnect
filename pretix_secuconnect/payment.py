import hashlib
import json
import logging
import requests
import uuid
from collections import OrderedDict
from django import forms
from django.conf import settings
from django.core import signing
from django.http import HttpRequest
from django.template.loader import get_template
from django.utils.translation import gettext_lazy as _, pgettext

from pretix.base.cache import ObjectRelatedCache
from pretix.base.decimal import round_decimal
from pretix.base.forms.questions import guess_country
from pretix.base.models import Event, InvoiceAddress, OrderPayment, OrderRefund
from pretix.base.payment import BasePaymentProvider, PaymentException
from pretix.base.settings import SettingsSandbox
from pretix.multidomain.urlreverse import build_absolute_uri
from requests import HTTPError, RequestException

logger = logging.getLogger(__name__)


class SecuconnectSettingsHolder(BasePaymentProvider):
    identifier = "secuconnect"
    verbose_name = _("SecuConnect")
    is_enabled = False
    is_meta = True

    def __init__(self, event: Event):
        super().__init__(event)
        self.settings = SettingsSandbox("payment", "secuconnect", event)

    @property
    def settings_form_fields(self):
        fields = [
            (
                "environment",
                forms.ChoiceField(
                    label=_("Environment"),
                    initial="production",
                    choices=(
                        ("production", pgettext("secuconnect", "Production")),
                        ("testing", pgettext("secuconnect", "Testing")),
                        ("showcase", pgettext("secuconnect", "Showcase")),
                    ),
                ),
            ),
            (
                "client_id",
                forms.CharField(
                    label=_("OAuth Client ID"),
                ),
            ),
            (
                "client_secret",
                forms.CharField(
                    label=_("OAuth Client Secret"),
                ),
            ),
            (
                "contract_id",
                forms.CharField(
                    label=_("Merchant Contract ID"),
                ),
            ),
        ]
        d = OrderedDict(
            fields
            + [
                (
                    "method_creditcard",
                    forms.BooleanField(
                        label=_("Credit Card"),
                        required=False,
                    ),
                ),
                (
                    "method_debit",
                    forms.BooleanField(
                        label=_("SEPA Direct Debit"),
                        required=False,
                    ),
                ),
                (
                    "method_invoice",
                    forms.BooleanField(
                        label=_("Invoice Payment"),
                        required=False,
                    ),
                ),
                (
                    "method_prepayment",
                    forms.BooleanField(
                        label=_("Prepayment"),
                        required=False,
                    ),
                ),
                (
                    "method_paypal",
                    forms.BooleanField(
                        label=_("PayPal"),
                        required=False,
                    ),
                ),
                (
                    "method_sofort",
                    forms.BooleanField(
                        label=_("Sofort"),
                        required=False,
                    ),
                ),
                (
                    "method_giropay",
                    forms.BooleanField(
                        label=_("giropay"),
                        required=False,
                    ),
                ),
                (
                    "method_eps",
                    forms.BooleanField(
                        label=_("eps"),
                        required=False,
                    ),
                ),
            ]
            + list(super().settings_form_fields.items())
        )
        d.move_to_end("_enabled", last=False)
        return d


class SecuconnectMethod(BasePaymentProvider):
    method = ""
    abort_pending_allowed = False
    refunds_allowed = True
    cancel_flow = True
    payment_methods = []

    def __init__(self, event: Event):
        super().__init__(event)
        self.settings = SettingsSandbox("payment", "secuconnect", event)
        self.cache = ObjectRelatedCache(event)

    @property
    def settings_form_fields(self):
        return {}

    @property
    def identifier(self):
        return "secuconnect_{}".format(self.method)

    @property
    def is_enabled(self) -> bool:
        return self.settings.get("_enabled", as_type=bool) and self.settings.get(
            "method_{}".format(self.method), as_type=bool
        )

    def payment_refund_supported(self, payment: OrderPayment) -> bool:
        return self.refunds_allowed

    def payment_partial_refund_supported(self, payment: OrderPayment) -> bool:
        return self.refunds_allowed

    def payment_prepare(self, request, payment):
        return self.checkout_prepare(request, None)

    def payment_is_valid_session(self, request: HttpRequest):
        return True

    def payment_form_render(self, request) -> str:
        template = get_template("pretix_secuconnect/checkout_payment_form.html")
        ctx = {"request": request, "event": self.event, "settings": self.settings}
        return template.render(ctx)

    def checkout_confirm_render(self, request) -> str:
        template = get_template("pretix_secuconnect/checkout_payment_confirm.html")
        ctx = {
            "request": request,
            "event": self.event,
            "settings": self.settings,
            "provider": self,
        }
        return template.render(ctx)

    def payment_can_retry(self, payment):
        return self._is_still_available(order=payment.order)

    def payment_pending_render(self, request, payment) -> str:
        if payment.info:
            payment_info = json.loads(payment.info)
        else:
            payment_info = None
        template = get_template("pretix_secuconnect/pending.html")
        ctx = {
            "request": request,
            "event": self.event,
            "settings": self.settings,
            "provider": self,
            "order": payment.order,
            "payment": payment,
            "payment_info": payment_info,
        }
        return template.render(ctx)

    def payment_control_render(self, request, payment) -> str:
        if payment.info:
            payment_info = json.loads(payment.info)
            if "amount" in payment_info:
                payment_info["amount"] /= 10 ** settings.CURRENCY_PLACES.get(
                    self.event.currency, 2
                )
        else:
            payment_info = None
        template = get_template("pretix_secuconnect/control.html")
        ctx = {
            "request": request,
            "event": self.event,
            "settings": self.settings,
            "payment_info": payment_info,
            "payment": payment,
            "method": self.method,
            "provider": self,
        }
        return template.render(ctx)

    def api_payment_details(self, payment: OrderPayment):
        return {
            "id": payment.info_data.get("Id"),
            "status": payment.info_data.get("Status"),
            "reference": payment.info_data.get("SixTransactionReference"),
            "payment_method": payment.info_data.get("PaymentMeans", {})
            .get("Brand", {})
            .get("Name"),
            "payment_source": payment.info_data.get("PaymentMeans", {}).get(
                "DisplayText"
            ),
        }

    def execute_refund(self, refund: OrderRefund):
        # TODO
        raise NotImplemented

    @property
    def test_mode_message(self):
        if self.is_test_mode:
            return _(
                "The SecuConnect plugin is operating in test mode. No money will actually be transferred."
            )
        return None

    @property
    def is_test_mode(self):
        return (
            self.settings.environment == "testing"
            or self.settings.environment == "showcase"
        )

    @property
    def _api_base_url(self):
        return {
            "production": "https://connect.secucard.com",
            "testing": "https://connect-testing.secupay-ag.de",
            "showcase": "https://connect-showcase.secupay-ag.de",
        }[self.settings.environment]

    def _get_auth_token(self):
        token = self.cache.get("payment_secuconnect_auth_token")
        print("Token from cache?", token)
        if not token:
            print("Requesting access token")
            r = requests.post(
                "{base}/oauth/token".format(base=self._api_base_url),
                timeout=20,
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.get("client_id"),
                    "client_secret": self.settings.get("client_secret"),
                },
            )
            response = r.json()
            print("Response", response)
            token = response["access_token"]
            self.cache.set(
                "payment_secuconnect_auth_token", token, response["expires_in"]
            )
        return token

    def _post(self, endpoint, *args, **kwargs):
        r = requests.post(
            "{base}/api/{ep}".format(base=self._api_base_url, ep=endpoint),
            headers={"Authorization": "Bearer " + self._get_auth_token()},
            timeout=20,
            *args,
            **kwargs
        )
        return r

    def _get(self, endpoint, *args, **kwargs):
        r = requests.get(
            "{base}/api/v2/{ep}".format(base=self._api_base_url, ep=endpoint),
            headers={"Authorization": "Bearer " + self._get_auth_token()},
            timeout=20,
            *args,
            **kwargs
        )
        return r

    def _amount_to_decimal(self, cents):
        places = settings.CURRENCY_PLACES.get(self.event.currency, 2)
        return round_decimal(float(cents) / (10**places), self.event.currency)

    def _decimal_to_int(self, amount):
        places = settings.CURRENCY_PLACES.get(self.event.currency, 2)
        return int(amount * 10**places)

    def _return_url(self, payment, status):
        return build_absolute_uri(
            self.event,
            "plugins:pretix_secuconnect:return",
            kwargs={
                "order": payment.order.code,
                "payment": payment.pk,
                "hash": hashlib.sha1(payment.order.secret.lower().encode()).hexdigest(),
                "action": status,
            },
        )

    def _get_smart_transaction_init_body(self, payment):
        b = {
            "is_demo": True, # self.is_test_mode
            "contract": {
                "id": self.settings.contract_id
            },
            "intent": "sale",
            "basket": {
                "products": [
                    {
                        "id": 1,
                        "desc": "Pretix Order",
                        "priceOne": self._decimal_to_int(payment.amount),
                        "quantity": 1,
                        "tax": 0,
                    }
                ]
            },
            "basket_info": {
                "sum": self._decimal_to_int(payment.amount),
                "currency": self.event.currency,
            },
            "application_context": {
                # template ID for checkout (not subscription)
                "checkout_template": "COT_WD0DE66HN2XWJHW8JM88003YG0NEA2",
                "return_urls": {
                    "url_success": self._return_url(payment, "success"),
                    "url_error": self._return_url(payment, "fail"),
                    "url_abort": self._return_url(payment, "abort"),
                },
            },
            "payment_context": {
                "auto_capture": True,
            },
        }
        return b

    def execute_payment(self, request: HttpRequest, payment: OrderPayment):
        try:
            req = self._post(
                "v2/Smart/Transactions",
                json=self._get_smart_transaction_init_body(payment),
            )
            req.raise_for_status()
        except HTTPError:
            logger.exception("SecuConnect error: %s" % req.text)
            try:
                payment.info_data = req.json()
            except:
                payment.info_data = {"error": True, "detail": req.text}
            payment.state = OrderPayment.PAYMENT_STATE_FAILED
            payment.save()
            payment.order.log_action(
                "pretix.event.order.payment.failed",
                {
                    "local_id": payment.local_id,
                    "provider": payment.provider,
                    "data": payment.info_data,
                },
            )
            raise PaymentException(
                _(
                    "We had trouble communicating with SecuConnect. Please try again and get in touch "
                    "with us if this problem persists."
                )
            )
        except RequestException as e:
            logger.exception("SecuConnect request error")
            payment.info_data = {"error": True, "detail": str(e)}
            payment.state = OrderPayment.PAYMENT_STATE_FAILED
            payment.save()
            payment.order.log_action(
                "pretix.event.order.payment.failed",
                {
                    "local_id": payment.local_id,
                    "provider": payment.provider,
                    "data": payment.info_data,
                },
            )
            raise PaymentException(
                _(
                    "We had trouble communicating with SecuConnect. Please try again and get in touch "
                    "with us if this problem persists."
                )
            )

        data = req.json()
        payment.info = json.dumps(data)
        payment.state = OrderPayment.PAYMENT_STATE_CREATED
        payment.save()
        request.session["payment_secuconnect_order_secret"] = payment.order.secret
        print("SecuPay success...")
        print("Response:", data)

        return self.redirect(request, data.get("payment_links").get('general'))

    def redirect(self, request, url):
        if request.session.get("iframe_session", False) and self.method in (
            "paypal",
            "sofort",
            "giropay",
            "paydirekt",
        ):
            return (
                build_absolute_uri(request.event, "plugins:pretix_secuconnect:redirect")
                + "?data="
                + signing.dumps(
                    {
                        "url": url,
                        "session": {
                            "payment_saferpay_order_secret": request.session[
                                "payment_saferpay_order_secret"
                            ],
                        },
                    },
                    salt="safe-redirect",
                )
            )
        else:
            return str(url)

    def shred_payment_info(self, obj: OrderPayment):
        if not obj.info:
            return
        d = json.loads(obj.info)
        if "details" in d:
            d["details"] = {k: "█" for k in d["details"].keys()}

        d["_shredded"] = True
        obj.info = json.dumps(d)
        obj.save(update_fields=["info"])


class SecuconnectCC(SecuconnectMethod):
    method = "creditcard"
    verbose_name = _("Credit card via SecuConnect")
    public_name = _("Credit card")


class SecuconnectDirectDebit(SecuconnectMethod):
    method = "debit"
    verbose_name = _("SEPA Direct Debit via Secuconnect")
    public_name = _("SEPA Direct Debit")


