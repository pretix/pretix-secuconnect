import hashlib
import json
import logging
import urllib
from django.contrib import messages
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from pretix.base.models import Order, OrderPayment, Quota
from pretix.multidomain.urlreverse import build_absolute_uri, eventreverse
from requests import RequestException

from .api_client import PaymentStatusSimple, SecuconnectException

logger = logging.getLogger(__name__)


@xframe_options_exempt
def redirect_view(request: HttpRequest, *args, **kwargs):
    try:
        data = signing.loads(request.GET.get("data", ""), salt="safe-redirect")
    except signing.BadSignature:
        return HttpResponseBadRequest("Invalid parameter")

    if "go" in request.GET:
        if "session" in data:
            for k, v in data["session"].items():
                request.session[k] = v
        return redirect(data["url"])
    else:
        params = request.GET.copy()
        params["go"] = "1"
        r = render(
            request,
            "pretix_secuconnect/redirect.html",
            {
                "url": build_absolute_uri(
                    request.event, "plugins:pretix_secuconnect:redirect"
                )
                + "?"
                + urllib.parse.urlencode(params),
            },
        )
        r._csp_ignore = True
        return r


class SecuconnectOrderView:
    def dispatch(self, request: HttpRequest, *args, **kwargs):
        try:
            self.order = request.event.orders.get(code=kwargs["order"])
            if (
                hashlib.sha1(self.order.secret.lower().encode()).hexdigest()
                != kwargs["hash"].lower()
            ):
                raise Http404("")
        except Order.DoesNotExist:
            # Do a hash comparison as well to harden timing attacks
            if (
                "abcdefghijklmnopq".lower()
                == hashlib.sha1("abcdefghijklmnopq".encode()).hexdigest()
            ):
                raise Http404("")
            else:
                raise Http404("")
        return super().dispatch(request, *args, **kwargs)

    @cached_property
    def payment(self):
        return get_object_or_404(
            self.order.payments,
            pk=self.kwargs["payment"],
            provider__startswith="secuconnect",
        )

    @cached_property
    def pprov(self):
        return self.payment.payment_provider


class ReturnView(SecuconnectOrderView, View):
    def get(self, request: HttpRequest, *args, **kwargs):
        info = self.payment.info_data
        transaction_id = info["smart_transaction"]["id"]
        if self.payment.state != OrderPayment.PAYMENT_STATE_CREATED:
            logger.warning(
                "%s: secuconnect return URL was accessed in incorrect payment state",
                transaction_id,
            )
            return self._redirect_to_order()

        try:
            smart_transaction = self.pprov.client.fetch_smart_transaction_info(
                transaction_id
            )
            info["smart_transaction"] = smart_transaction
            if smart_transaction["transactions"]:
                payment_transaction = self.pprov.client.fetch_payment_transaction_info(
                    smart_transaction["transactions"][0]["id"]
                )
                info["payment_transaction"] = payment_transaction
        except (RequestException, SecuconnectException):
            messages.error(
                self.request,
                _(
                    "We had trouble communicating with secuconnect. Please try again and get in touch "
                    "with us if this problem persists."
                ),
            )
            return self._redirect_to_order()

        if kwargs.get("action") == "success":
            self.payment.info_data = info
            if smart_transaction["status"] == "ok":
                try:
                    self.payment.confirm()
                except Quota.QuotaExceededException:
                    pass
            elif smart_transaction["status"] == "pending":
                self.payment.state = OrderPayment.PAYMENT_STATE_PENDING
                self.payment.save(update_fields=["state", "info"])
            else:
                logger.error(
                    "%s: Unexpected payment state '%r' reported by secuconnect - failing payment",
                    transaction_id,
                    smart_transaction["status"],
                )
                self.payment.fail(log_data=smart_transaction, info=info)
        elif kwargs.get("action") == "fail":
            self.payment.fail(log_data=smart_transaction, info=info)
        elif kwargs.get("action") == "abort":
            self.order.log_action(
                "pretix.event.order.payment.canceled",
                {
                    "local_id": self.payment.local_id,
                    "provider": self.payment.provider,
                },
            )
            self.payment.info_data = info
            self.payment.state = OrderPayment.PAYMENT_STATE_CANCELED
            self.payment.save(update_fields=["state", "info"])

        return self._redirect_to_order()

    def _redirect_to_order(self):
        self.order.refresh_from_db()
        if (
            self.request.session.get("payment_secuconnect_order_secret")
            != self.order.secret
        ):
            messages.error(
                self.request,
                _(
                    "Sorry, there was an error in the payment process. Please check the link "
                    "in your emails to continue."
                ),
            )
            return redirect(eventreverse(self.request.event, "presale:event.index"))

        return redirect(
            eventreverse(
                self.request.event,
                "presale:event.order",
                kwargs={"order": self.order.code, "secret": self.order.secret},
            )
            + ("?paid=yes" if self.order.status == Order.STATUS_PAID else "")
        )


@method_decorator(csrf_exempt, "dispatch")
class WebhookView(SecuconnectOrderView, View):
    def post(self, request: HttpRequest, *args, **kwargs):
        json_body = json.loads(request.body)
        for event_object in json_body["data"]:
            if event_object["object"] == "payment.transactions":
                self._handle_payment_transaction_update(event_object["id"])
            else:
                logger.warning(
                    "secuconnect notified us of unknown object type change %r",
                    event_object,
                )
        return HttpResponse("ok")

    def _handle_payment_transaction_update(self, id):
        transaction = self.pprov.client.fetch_payment_transaction_info(id)

        info = self.payment.info_data
        status = PaymentStatusSimple(transaction["details"]["status_simple"])
        if info["payment_transaction"]:
            old_status = PaymentStatusSimple(
                info["payment_transaction"]["details"]["status_simple"]
            )
        else:
            old_status = None
        info["payment_transaction"] = transaction
        logging.info("%s: Transaction status update (%s -> %s)", id, old_status, status)

        if old_status == status:
            logging.info(
                "%s: Transaction status update already processed, ignoring", id
            )
        else:
            if status == PaymentStatusSimple.ACCEPTED:
                self.payment.info_data = info
                try:
                    self.payment.confirm()
                except Quota.QuotaExceededException:
                    pass
            elif (
                status == PaymentStatusSimple.PENDING
                and self.payment.state == OrderPayment.PAYMENT_STATE_CREATED
            ):
                self.payment.info_data = info
                self.payment.state = OrderPayment.PAYMENT_STATE_PENDING
                self.payment.save(update_fields=["state", "info"])
            elif status == PaymentStatusSimple.DENIED and self.payment.state in (
                OrderPayment.PAYMENT_STATE_CREATED,
                OrderPayment.PAYMENT_STATE_PENDING,
            ):
                self.payment.fail(info=info)
            elif (
                status
                in (
                    PaymentStatusSimple.ISSUE,
                    PaymentStatusSimple.REFUND,
                    PaymentStatusSimple.VOID,
                )
                and self.payment.state == OrderPayment.PAYMENT_STATE_CONFIRMED
            ):
                payment_status = self.pprov.client.fetch_payment_transaction_status(id)
                remaining_amount = self.pprov.amount_to_decimal(
                    payment_status["amount"]
                )
                if remaining_amount < self.payment.amount:
                    self.payment.create_external_refund(
                        info=json.dumps(transaction),
                        amount=self.payment.amount - remaining_amount,
                    )
            else:
                logger.warning(
                    "%s: Unexpected payment state '%r' reported by secuconnect",
                    id,
                    transaction["details"],
                )
                self.payment.info_data = info
                self.payment.save(update_fields=["state", "info"])

            self.payment.order.log_action(
                "pretix_secuconnect.event.status_update",
                {
                    "local_id": self.payment.local_id,
                    "provider": self.payment.provider,
                    "new_status": str(status),
                },
            )

        if transaction.get("related_transactions"):
            known_refunds = {
                r.info_data.get("id"): r for r in self.payment.refunds.all()
            }
            for related_ref in transaction["related_transactions"]:
                if (
                    related_ref["object"] == "payment.transactions"
                    and related_ref["hierarchy"] == "child"
                ):
                    if related_ref["id"] not in known_refunds:
                        related = self.pprov.client.fetch_payment_transaction_info(
                            related_ref["id"]
                        )
                        self.payment.create_external_refund(
                            amount=abs(self.pprov.amount_to_decimal(related["amount"])),
                            info=json.dumps(related),
                        )
