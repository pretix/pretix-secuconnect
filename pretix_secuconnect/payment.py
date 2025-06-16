from typing import Union

import hashlib
import json
import logging
from collections import OrderedDict
from decimal import Decimal
from django import forms
from django.conf import settings
from django.core import signing
from django.http import HttpRequest
from django.template.loader import get_template
from django.utils.translation import gettext, gettext_lazy as _, pgettext
from pretix.base.decimal import round_decimal
from pretix.base.forms import SecretKeySettingsField
from pretix.base.models import Event, InvoiceAddress, Order, OrderPayment, OrderRefund
from pretix.base.payment import BasePaymentProvider, PaymentException
from pretix.base.settings import SettingsSandbox
from pretix.multidomain.urlreverse import build_absolute_uri
from requests import RequestException

from .api_client import SecuconnectAPIClient, SecuconnectException

logger = logging.getLogger(__name__)


class SecuconnectSettingsHolder(BasePaymentProvider):
    identifier = "secuconnect"
    verbose_name = _("secuconnect")
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
                "is_demo",
                forms.BooleanField(
                    label=_("Demo Mode"),
                    required=False,
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
                SecretKeySettingsField(
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
                    "method_prepaid",
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
                # Giropay has been retired as such it is not offered as a setting anymore.
                # (
                #     "method_giropay",
                #     forms.BooleanField(
                #         label=_("giropay"),
                #         required=False,
                #     ),
                # ),
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
    abort_pending_allowed = True
    refunds_allowed = True
    cancel_flow = True
    payment_methods = []

    allow_business = True
    required_customer_info = (
        "forename",
        "surname",
        "email",
    )
    checkout_template_id = "COT_WD0DE66HN2XWJHW8JM88003YG0NEA2"

    def __init__(self, event: Event):
        super().__init__(event)
        self.settings = SettingsSandbox("payment", "secuconnect", event)
        self.cache = self.event.cache
        if self.settings.get("environment"):
            self.client = SecuconnectAPIClient(
                cache=self.cache,
                environment=self.settings.get("environment"),
                client_id=self.settings.get("client_id"),
                client_secret=self.settings.get("client_secret"),
            )

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

    def payment_prepare(self, request: HttpRequest, payment: OrderPayment):
        return self.checkout_prepare(request, None)

    def payment_is_valid_session(self, request: HttpRequest):
        return True

    def payment_form_render(
        self, request: HttpRequest, total: Decimal, order: Order = None
    ) -> str:
        template = get_template("pretix_secuconnect/checkout_payment_form.html")
        ctx = {"request": request, "event": self.event, "settings": self.settings}
        return template.render(ctx)

    def checkout_confirm_render(
        self, request: HttpRequest, order: Order = None, info_data: dict = None
    ) -> str:
        template = get_template("pretix_secuconnect/checkout_payment_confirm.html")
        ctx = {
            "request": request,
            "event": self.event,
            "settings": self.settings,
            "provider": self,
        }
        return template.render(ctx)

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
        payment_info = payment.info_data
        return {
            "id": payment_info.get("smart_transaction", {}).get("id"),
            "status": (
                (payment_info.get("payment_transaction", {}) or {})
                .get("details", {})
                .get("status_simple_text")
                or payment_info.get("smart_transaction", {}).get("status")
            ),
            "payment_method": payment_info.get("smart_transaction", {}).get(
                "payment_method"
            ),
            "payment_instructions": payment_info.get("smart_transaction", {}).get(
                "payment_instructions"
            ),
            "payment_context": payment_info.get("smart_transaction", {}).get(
                "payment_context"
            ),
            "payment_transaction_status_string": (
                payment_info.get("payment_transaction", {}) or {}
            ).get("status_text"),
            "payment_transaction_id": (
                payment_info.get("payment_transaction", {}) or {}
            ).get("id"),
        }

    def execute_refund(self, refund: OrderRefund):
        try:
            info = refund.payment.info_data
            transactions = self.client.cancel_payment_transaction(
                info["payment_transaction"]["id"],
                self._decimal_to_int(refund.amount),
            )
            for transaction in transactions:
                if transaction["id"] == info["payment_transaction"]["id"]:
                    info["payment_transaction"] = transaction
                    refund.payment.info_data = info
                    refund.payment.save(update_fields=["info"])
                else:
                    refund.info_data = transaction
        except SecuconnectException as ex:
            refund.info_data = ex.error_object
            raise PaymentException(
                _(
                    "We had trouble communicating with secuconnect. Please try again and get in touch "
                    "with us if this problem persists."
                )
            )
        except RequestException:
            raise PaymentException(
                _(
                    "We had trouble communicating with secuconnect. Please try again and get in touch "
                    "with us if this problem persists."
                )
            )
        else:
            refund.done()

    @property
    def test_mode_message(self):
        if self.is_test_mode:
            return _(
                "The secuconnect plugin is operating in test mode. No money will actually be transferred."
            )
        return None

    @property
    def is_test_mode(self):
        return (
            self.settings.environment == "testing"
            or self.settings.environment == "showcase"
            or self.settings.is_demo
        )

    def amount_to_decimal(self, cents):
        places = settings.CURRENCY_PLACES.get(self.event.currency, 2)
        return round_decimal(float(cents) / (10**places), self.event.currency)

    def _decimal_to_int(self, amount):
        places = settings.CURRENCY_PLACES.get(self.event.currency, 2)
        return int(amount * 10**places)

    def _return_url(self, type, payment, status):
        return build_absolute_uri(
            self.event,
            "plugins:pretix_secuconnect:" + type,
            kwargs={
                "order": payment.order.code,
                "payment": payment.pk,
                "hash": hashlib.sha1(payment.order.secret.lower().encode()).hexdigest(),
                "action": status,
            },
        )

    def _build_customer_info(self, order):
        try:
            ia = order.invoice_address
        except InvoiceAddress.DoesNotExist:
            ia = InvoiceAddress()

        customer = {}

        if ia.name_parts.get("family_name"):
            customer["surname"] = ia.name_parts.get("family_name", "")[:50]
            customer["forename"] = ia.name_parts.get("given_name", "")[:50]
        elif ia.name:
            customer["surname"] = ia.name.rsplit(" ", 1)[-1][:50]
            customer["forename"] = ia.name.rsplit(" ", 1)[0][:50]

        if not customer:
            # If no name is provided, supply an empty customer object. This makes secuconnect show its own
            # customer details form. Otherwise the payment would fail with an unspecific error message.
            return customer

        customer["email"] = order.email
        if ia.company:
            customer["companyname"] = ia.company[:50]

        if ia.name_parts.get("salutation"):
            customer["salutation"] = ia.name_parts.get("salutation", "")[:10]
        if ia.name_parts.get("title"):
            customer["title"] = ia.name_parts.get("title", "")[:20]
        if "address" in self.required_customer_info:
            if ia.street and ia.zipcode and ia.city and ia.country:
                customer["address"] = {
                    "street": ia.street[:50],
                    "postal_code": ia.zipcode[:10],
                    "city": ia.city[:50],
                    "country": str(ia.country),
                }
            else:
                # If no address is provided, but required by the method, supply an empty customer object. This makes
                # secuconnect show its own customer details form instead of failing with an unspecific error message.
                return {}

        return customer

    def _build_smart_transaction_init_body(self, payment):
        customer = self._build_customer_info(payment.order)
        b = {
            "is_demo": self.settings.get("is_demo", as_type=bool),  # self.is_test_mode
            "contract": {"id": self.settings.contract_id},
            "customer": {"contact": customer} if customer else {},
            "intent": "sale",
            "basket": {
                "products": [
                    {
                        "id": 1,
                        "desc": gettext("Order {order} for {event}").format(
                            event=payment.order.event.name, order=payment.order.code
                        ),
                        "priceOne": self._decimal_to_int(payment.amount),
                        "quantity": 1,
                        "tax": 0,
                    }
                ]
            },
            "merchantRef": f"{self.event.slug.upper()}-{payment.full_id}",
            "transactionRef": f"{self.event.slug.upper()}-{payment.full_id}",
            "basket_info": {
                "sum": self._decimal_to_int(payment.amount),
                "currency": self.event.currency,
            },
            "application_context": {
                # template ID for checkout (not subscription)
                "checkout_template": self.checkout_template_id,
                "return_urls": {
                    "url_success": self._return_url("return", payment, "success"),
                    "url_error": self._return_url("return", payment, "fail"),
                    "url_abort": self._return_url("return", payment, "abort"),
                    "url_push": self._return_url("webhook", payment, "webhook"),
                },
            },
            "payment_context": {
                "auto_capture": True,
            },
        }
        return b

    def execute_payment(self, request: HttpRequest, payment: OrderPayment):
        try:
            data = self.client.start_smart_transaction(
                self._build_smart_transaction_init_body(payment)
            )
        except SecuconnectException as ex:
            payment.fail(log_data=ex.error_object)
            raise PaymentException(
                _(
                    "We had trouble communicating with secuconnect. Please try again and get in touch "
                    "with us if this problem persists."
                )
            )
        except RequestException as ex:
            payment.fail(log_data={"error_details": str(ex)})
            raise PaymentException(
                _(
                    "We had trouble communicating with secuconnect. Please try again and get in touch "
                    "with us if this problem persists."
                )
            )

        payment.info_data = {"smart_transaction": data, "payment_transaction": None}
        payment.save(update_fields=["info"])
        request.session["payment_secuconnect_order_secret"] = payment.order.secret

        try:
            redirect_url = data["payment_links"][self.method]
        except KeyError:
            raise PaymentException(
                _(
                    "Requested payment method not supported. Please get in touch "
                    "with us if this problem persists."
                )
            )
        return self.redirect(request, redirect_url)

    def redirect(self, request: HttpRequest, url):
        if request.session.get("iframe_session", False):
            return (
                build_absolute_uri(request.event, "plugins:pretix_secuconnect:redirect")
                + "?data="
                + signing.dumps(
                    {
                        "url": url,
                        "session": {
                            "payment_secuconnect_order_secret": request.session[
                                "payment_secuconnect_order_secret"
                            ],
                        },
                    },
                    salt="safe-redirect",
                )
            )
        else:
            return str(url)

    def shred_payment_info(self, obj: Union[OrderPayment, OrderRefund]):
        if not obj.info:
            return
        d = obj.info_data
        if d.get("payment_transaction"):
            d["payment_transaction"]["payment_data"] = "█"
        if d.get("payment_data"):
            d["payment_data"] = "█"

        d["_shredded"] = True
        obj.info_data = d
        obj.save(update_fields=["info"])


class SecuconnectCC(SecuconnectMethod):
    method = "creditcard"
    verbose_name = _("Credit card via secuconnect")
    public_name = _("Credit card")


class SecuconnectDirectDebit(SecuconnectMethod):
    method = "debit"
    verbose_name = _("SEPA Direct Debit via secuconnect")
    public_name = _("SEPA Direct Debit")
    required_customer_info = (
        "forename",
        "surname",
        "address",
        "email",
    )
    # ...address only required if payment guarantee/scoring contracted


class SecuconnectPrepaid(SecuconnectMethod):
    method = "prepaid"
    verbose_name = _("Bank transfer via secuconnect")
    public_name = _("Bank transfer")


class SecuconnectSofort(SecuconnectMethod):
    method = "sofort"
    verbose_name = _("SOFORT via secuconnect")
    public_name = _("SOFORT")
    required_customer_info = (
        "forename",
        "surname",
        "address",
        "email",
    )


class SecuconnectEasycredit(SecuconnectMethod):
    method = "easycredit"
    verbose_name = _("easycredit via secuconnect")
    public_name = _("easycredit")
    required_customer_info = (
        "forename",
        "surname",
        "address",
        "email",
    )
    allow_business = False
    checkout_template_id = "COT_3DP70FK5H2XP02TCVQ28000NG095A2"


class SecuconnectEPS(SecuconnectMethod):
    method = "eps"
    verbose_name = _("EPS via secuconnect")
    public_name = _("EPS")


class SecuconnectGiropay(SecuconnectMethod):
    method = "giropay"
    verbose_name = _("GiroPay via secuconnect")
    public_name = _("GiroPay")

    def is_allowed(self, request: HttpRequest, total: Decimal = None) -> bool:
        # giropay has shut down
        return False

    def order_change_allowed(self, order: Order, request: HttpRequest = None) -> bool:
        # giropay has shut down
        return False


class SecuconnectInvoice(SecuconnectMethod):
    method = "invoice"
    verbose_name = _("Invoice via secuconnect")
    public_name = _("Invoice")
    required_customer_info = (
        "forename",
        "surname",
        "address",
        "email",
    )
    # ...address only required if payment guarantee/scoring contracted


class SecuconnectPaypal(SecuconnectMethod):
    method = "paypal"
    verbose_name = _("PayPal via secuconnect")
    public_name = _("PayPal")
    # required_customer_info = ("forename", "surname", "address", "email",)
    # ...address only required for physical shipment
