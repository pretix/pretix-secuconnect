import logging
import requests
from enum import Enum
from requests import RequestException

logger = logging.getLogger(__name__)


class PaymentStatusSimple(Enum):
    PROCEED = 0
    """Initial status of every transaction. The payment is not authorised, nor wanted to capture."""
    ACCEPTED = 1
    """The request for capture or for invoice payment was accepted. You can deliver now.
    (When you use invoice payment, you need to add the partial deliveries, and to request the capture along
    with your final delivery.)"""
    AUTHORIZED = 2
    """The payment is authorised, but was not requested to capture yet."""
    DENIED = 3
    """The payment was rejected for some reason."""
    ISSUE = 4
    """A problem occurred after the payment was accepted. For instance, the payer wanted a credit card charge back."""
    VOID = 5
    """The payment was cancelled, or refunded."""
    ISSUE_RESOLVED = 6
    """The problem is resolved. For instance, the charge back could be objected."""
    REFUND = 7
    """The payment is flagged for refund. It will be processed with our next bank cycle."""
    CREATED = 8
    PAID = 9
    """We have received the invoice payment."""
    PENDING = 10
    """The payment was requested to capture, but you must wait with delivery for status accepted. This status is
    always set when we wait for the advance payment. It is also used with other payment methods to perform an
    additional risk check."""
    SUBSCRIPTION_APPROVED = 11
    """The transaction used to authorise further payments has been approved."""
    SUBSCRIPTION_DECLINED = 12
    """The transaction used to authorise further payments has been declined."""
    ON_HOLD = 13
    WAITING_FOR_SHIPMENT = 14
    UNKNOWN = 99


class SecuconnectAPIClient:
    def __init__(self, cache, environment, client_id, client_secret):
        self.environment = environment
        self.cache = cache
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base_url = {
            "production": "https://connect.secucard.com",
            "testing": "https://connect-testing.secupay-ag.de",
            "showcase": "https://connect-showcase.secupay-ag.de",
        }[environment]

    def _get_auth_token(self):
        token = self.cache.get("payment_secuconnect_auth_token")
        logger.debug("Token from cache? %r", token)
        if not token:
            logger.debug("Requesting access token")
            try:
                r = requests.post(
                    "{base}/oauth/token".format(base=self.api_base_url),
                    timeout=20,
                    json={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                )
                r.raise_for_status()
            except RequestException as e:
                try:
                    error_object = r.json()
                except:  # noqa
                    error_object = {"exception": str(e)}
                logger.exception(
                    "Failed to retrieve secuconnect access token (%r)", error_object
                )
                raise

            response = r.json()
            logger.debug("Response %r", response)
            token = response["access_token"]
            self.cache.set(
                "payment_secuconnect_auth_token", token, response["expires_in"]
            )
        return token

    def _post(self, endpoint, *args, **kwargs):
        logger.debug("Sending secuconnect API POST request")
        logger.debug("endpoint: %r", endpoint)
        logger.debug("body:     %r", kwargs.get("json"))
        return self._perform_request("POST", endpoint, *args, **kwargs)

    def _get(self, endpoint, *args, **kwargs):
        return self._perform_request("GET", endpoint, *args, **kwargs)

    def _perform_request(self, method, endpoint, *args, **kwargs):
        auth_token = self._get_auth_token()
        try:
            r = requests.request(
                method,
                "{base}/api/{ep}".format(base=self.api_base_url, ep=endpoint),
                headers={"Authorization": "Bearer " + auth_token},
                timeout=20,
                *args,
                **kwargs
            )
            r.raise_for_status()
            return r.json()
        except RequestException as e:
            try:
                error_object = r.json()
            except:  # noqa
                error_object = {"exception": str(e)}
            logger.exception("secuconnect API returned error (%r)", error_object)
            if (
                error_object.get("status") == "error"
                and "error_details" in error_object
            ):
                raise SecuconnectException(error_object)

            raise

    def _scrub_customer_data(self, transaction_data):
        try:
            transaction_data["customer"] = {
                k: v if k in ("id", "object") else "█"
                for (k, v) in transaction_data["customer"].items()
            }
        except (TypeError, KeyError, AttributeError):
            pass
        return transaction_data

    def fetch_smart_transaction_info(self, transaction_id):
        return self._scrub_customer_data(
            self._get("v2/Smart/Transactions/{}".format(transaction_id))
        )

    def fetch_payment_transaction_info(self, transaction_id):
        return self._scrub_customer_data(
            self._get("v2/Payment/Transactions/{}".format(transaction_id))
        )

    def fetch_payment_transaction_status(self, transaction_id):
        return self._get(
            "v2/Payment/Transactions/{}/checkStatus".format(transaction_id)
        )

    def start_smart_transaction(self, body):
        return self._scrub_customer_data(self._post("v2/Smart/Transactions", json=body))

    def cancel_payment_transaction(self, transaction_id, reduce_amount_by):
        return [
            self._scrub_customer_data(transaction)
            for transaction in self._post(
                "v2/Payment/Transactions/{}/cancel".format(transaction_id),
                json={"reduce_amount_by": reduce_amount_by},
            )
        ]

    def set_payment_transaction_status_for_test(
        self, transaction_id, method, new_status
    ):
        return self._post(
            "v2/Payment/Secupay{}/{}/TestChangedPaymentStatus".format(
                method, transaction_id
            ),
            json={"new_status_id": new_status},
        )


class SecuconnectException(BaseException):
    """
    Raised by the API client in case the secuconnect API returns an error object as defined in
    https://developer.secuconnect.com/integration/API_Errors_-_secuconnect_API.html
    """

    def __init__(self, error_object):
        super().__init__(error_object["error_details"])
        self.error_object = error_object
