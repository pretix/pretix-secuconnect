from django.dispatch import receiver
from django.utils.translation import gettext as _
from pretix.base.signals import logentry_display, register_payment_providers


@receiver(register_payment_providers, dispatch_uid="payment_secuconnect")
def register_payment_provider(sender, **kwargs):
    from .payment import (
        SecuconnectCC,
        SecuconnectDirectDebit,
        SecuconnectEasycredit,
        SecuconnectEPS,
        SecuconnectGiropay,
        SecuconnectInvoice,
        SecuconnectPaypal,
        SecuconnectPrepaid,
        SecuconnectSettingsHolder,
        SecuconnectSofort,
    )

    return [
        SecuconnectSettingsHolder,
        SecuconnectCC,
        SecuconnectDirectDebit,
        SecuconnectPrepaid,
        SecuconnectSofort,
        SecuconnectEasycredit,
        SecuconnectEPS,
        SecuconnectGiropay,
        SecuconnectInvoice,
        SecuconnectPaypal,
    ]


@receiver(signal=logentry_display, dispatch_uid="secuconnect_logentry_display")
def pretixcontrol_logentry_display(sender, logentry, **kwargs):
    if logentry.action_type == "pretix_secuconnect.event.status_update":
        return _("secuconnect reported a status update: {}").format(
            logentry.parsed_data.get("new_status")
        )
