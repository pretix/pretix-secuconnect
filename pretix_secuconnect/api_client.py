import requests


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

