import hashlib
import json

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from pretix.base.models import Order, OrderPayment
from pretix.multidomain.urlreverse import eventreverse


@xframe_options_exempt
def redirect_view(request: HttpRequest, *args, **kwargs):
    return HttpResponse("hi there")


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
        print("SecuPay return:",kwargs)
        transaction_id = self.payment.info_data['id']
        transaction_details = self.pprov.client.fetch_transaction_info(transaction_id)
        print("SecuPay transaction details:",transaction_details)
        if kwargs.get("action") == "success":
            self.payment.info_data = transaction_details
            if transaction_details['status'] == 'ok':
                self.payment.confirm()
            elif transaction_details['status'] == 'pending':
                self.payment.state = OrderPayment.PAYMENT_STATE_PENDING
                self.payment.save(update_fields=["state", "info"])
            else:
                return HttpResponse("todo handle payment state\n\n"+json.dumps(transaction_details,indent=4),content_type="text/plain")
        elif kwargs.get("action") == "fail":
            self.payment.fail(info=transaction_details)
        elif kwargs.get("action") == "abort":
            self.order.log_action(
                "pretix.event.order.payment.canceled",
                {
                    "local_id": self.payment.local_id,
                    "provider": self.payment.provider,
                },
            )
            self.payment.info_data = transaction_details
            self.payment.state = OrderPayment.PAYMENT_STATE_CANCELED
            self.payment.save(update_fields=["state"])

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
        json_data = json.loads(request.body)
        print(json_data)
        raise Http404()
