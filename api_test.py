#!/usr/bin/env python3
import argparse
import json
import logging
import os


class MyCache:
    def __init__(self):
        self.data = dict()

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value, timeout=1000):
        self.data[name] = value


def print_obj(obj):
    print(json.dumps(obj, indent=4))


def register_command(command_name, *parameters):
    def handler(fn):
        subparser = subparsers.add_parser(command_name)
        subparser.set_defaults(func=fn)
        for param_name, kwargs in parameters:
            subparser.add_argument(param_name, **kwargs)
        return fn

    return handler


parser = argparse.ArgumentParser(description="client for testing SecuConnect API")
parser.add_argument("--env", type=str, default="testing")
parser.add_argument("--debug", default=False, action="store_true")
subparsers = parser.add_subparsers(required=True)


@register_command("stx", ("id", {"type": str}))
def stx_info(args):
    print_obj(client.fetch_smart_transaction_info(args.id))


@register_command("ptx", ("id", {"type": str}))
def ptx_info(args):
    print_obj(client.fetch_payment_transaction_info(args.id))


@register_command("ptx-checkstatus", ("id", {"type": str}))
def ptx_status(args):
    print_obj(client.fetch_payment_transaction_status(args.id))


@register_command("ptx-cancel", ("id", {"type": str}), ("amount", {"type": int}))
def ptx_cancel(args):
    print_obj(client.cancel_payment_transaction(args.id, args.amount))


@register_command(
    "ptx-forcestatus",
    ("id", {"type": str}),
    ("method", {"type": str, "choices": ["debits", "prepays", "invoices", "creditcards", "sofort"]}),
    ("status", {"type": int}),
)
def ptx_forcestatus(args):
    print_obj(
        client.set_payment_transaction_status_for_test(
            args.id, args.method, args.status
        )
    )


args = parser.parse_args()
logging.basicConfig()
logging.getLogger().setLevel(level=logging.DEBUG if args.debug else logging.INFO)

from pretix_secuconnect.api_client import SecuconnectAPIClient

client = SecuconnectAPIClient(
    MyCache(), args.env, os.environ["CLIENT_ID"], os.environ["CLIENT_SECRET"]
)

args.func(args)
