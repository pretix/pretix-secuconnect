from enum import Enum

import requests


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
        print("Token from cache?", token)
        if not token:
            print("Requesting access token")
            r = requests.post(
                "{base}/oauth/token".format(base=self.api_base_url),
                timeout=20,
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
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
        print("Sending SecuConnect API POST request")
        print("endpoint: ", endpoint)
        print("body: ", kwargs.get('json'))
        r = requests.post(
            "{base}/api/{ep}".format(base=self.api_base_url, ep=endpoint),
            headers={"Authorization": "Bearer " + self._get_auth_token()},
            timeout=20,
            *args,
            **kwargs
        )
        return r

    def _get(self, endpoint, *args, **kwargs):
        r = requests.get(
            "{base}/api/{ep}".format(base=self.api_base_url, ep=endpoint),
            headers={"Authorization": "Bearer " + self._get_auth_token()},
            timeout=20,
            *args,
            **kwargs
        )
        return r

    def fetch_smart_transaction_info(self, transaction_id):
        return self._get('v2/Smart/Transactions/{}'.format(transaction_id)).json()

    def fetch_payment_transaction_info(self, transaction_id):
        return self._get('v2/Payment/Transactions/{}'.format(transaction_id)).json()


class SmartTransaction:
    pass

