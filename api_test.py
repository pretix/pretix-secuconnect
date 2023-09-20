#!/usr/bin/env python3
import argparse
import os
import pprint
import logging
import json

class MyCache:
    def __init__(self):
        self.data = dict()

    def get(self, name):
        return self.data.get(name)

    def set(self, name, value, timeout=1000):
        self.data[name] = value

#pp = pprint.PrettyPrinter(indent=4)
def print_obj(obj):
    #pp.pprint(obj)
    print(json.dumps(obj, indent=4))

parser = argparse.ArgumentParser(description='client for testing SecuConnect API')
parser.add_argument('--env', type=str, default='testing')
parser.add_argument('--debug', default=False, action='store_true')
subparsers = parser.add_subparsers(required=True)


def stx_info(args):
    print_obj(client.fetch_smart_transaction_info(args.id))

parser_stx = subparsers.add_parser('stx')
parser_stx.add_argument('id', type=str)
parser_stx.set_defaults(func=stx_info)


def ptx_info(args):
    print_obj(client.fetch_payment_transaction_info(args.id))

parser_ptx = subparsers.add_parser('ptx')
parser_ptx.add_argument('id', type=str)
parser_ptx.set_defaults(func=ptx_info)


def ptx_cancel(args):
    print_obj(client.cancel_payment_transaction(args.id, args.amount))

parser_ptx = subparsers.add_parser('ptx-cancel')
parser_ptx.add_argument('id', type=str)
parser_ptx.add_argument('amount', type=int)
parser_ptx.set_defaults(func=ptx_cancel)


args = parser.parse_args()
logging.basicConfig()
logging.getLogger().setLevel(level=logging.DEBUG if args.debug else logging.INFO)

from pretix_secuconnect.api_client import *

client = SecuconnectAPIClient(MyCache(), args.env, os.environ['CLIENT_ID'], os.environ['CLIENT_SECRET'])

args.func(args)
