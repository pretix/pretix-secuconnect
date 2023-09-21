import hashlib
import json
import logging
import urllib

from .api_client import PaymentStatusSimple
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
from pretix.base.payment import PaymentException
from pretix.multidomain.urlreverse import build_absolute_uri, eventreverse

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
        print("SecuconnectOrderView",kwargs)
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
        print("SecuPay return:",kwargs)
        info = self.payment.info_data
        transaction_id = info['smart_transaction']['id']
        if self.payment.state != OrderPayment.PAYMENT_STATE_CREATED:
            logger.warning("%s: secuconnect return URL was accessed in incorrect payment state", transaction_id)
            return self._redirect_to_order()

        try:
            smart_transaction = self.pprov.client.fetch_smart_transaction_info(transaction_id)
            print("SecuPay smart transaction details:", smart_transaction)
            payment_transaction = self.pprov.client.fetch_payment_transaction_info(smart_transaction['transactions'][0]['id'])
            print("SecuPay payment transaction details:", payment_transaction)
        except PaymentException as ex:
            messages.error(self.request, str(ex))
            return self._redirect_to_order()

        info['smart_transaction'] = smart_transaction
        info['payment_transaction'] = payment_transaction
        if kwargs.get("action") == "success":
            self.payment.info_data = info
            if smart_transaction['status'] == 'ok':
                try:
                    self.payment.confirm()
                except Quota.QuotaExceededException:
                    pass
            elif smart_transaction['status'] == 'pending':
                self.payment.state = OrderPayment.PAYMENT_STATE_PENDING
                self.payment.save(update_fields=["state", "info"])
            else:
                logger.error("%s: Unexpected payment state '%r' reported by secuconnect - failing payment",
                             transaction_id, smart_transaction['status'])
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


@method_decorator(csrf_exempt, 'dispatch')
class WebhookView(SecuconnectOrderView, View):
    def post(self, request: HttpRequest, *args, **kwargs):
        print("Request body:", request.body)
        json_body = json.loads(request.body)
        for event_object in json_body['data']:
            if event_object['object'] == 'payment.transactions':
                self._handle_payment_transaction_update(event_object['id'])
            else:
                logger.warning("SecuPay notified us of unknown object type change %r", event_object)
        return HttpResponse("ok")

    def _handle_payment_transaction_update(self, id):
        payment_transaction_details = self.pprov.client.fetch_payment_transaction_info(id)
        print("PaymentTransaction:", payment_transaction_details)

        info = self.payment.info_data
        status = PaymentStatusSimple(payment_transaction_details['details']['status_simple'])
        if info['payment_transaction']:
            old_status = PaymentStatusSimple(info['payment_transaction']['details']['status_simple'])
            if old_status == status:
                logging.info("%s: Transaction status update already processed, ignoring", id)
                return
        else:
            old_status = None
        info['payment_transaction'] = payment_transaction_details
        logging.info("%s: Transaction status update (%s -> %s)", id, old_status, status)

        if status == PaymentStatusSimple.ACCEPTED:
            self.payment.info_data = info
            try:
                self.payment.confirm()
            except Quota.QuotaExceededException:
                pass
        elif status == PaymentStatusSimple.PENDING and self.payment.state == OrderPayment.PAYMENT_STATE_CREATED:
            self.payment.info_data = info
            self.payment.state = OrderPayment.PAYMENT_STATE_PENDING
            self.payment.save(update_fields=["state", "info"])
        elif status == PaymentStatusSimple.DENIED and self.payment.state in (OrderPayment.PAYMENT_STATE_CREATED, OrderPayment.PAYMENT_STATE_PENDING):
            self.payment.fail(info=info)
        else:
            logger.warning("%s: Unexpected payment state '%r' reported by secuconnect",
                           id, payment_transaction_details['details'])
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

