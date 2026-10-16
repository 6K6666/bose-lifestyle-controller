"""Domain substitution: rewrites only the underscore token (default bose_lifestyle, the repo domain).

Hyphen forms (/run/bose-control, bose-control-card.js, ll-strategy-*-bose-control) are left alone.
A longer identifier that merely starts with the token (bose_controller_hub) is left alone too.
"""
import re

TOKEN = 'bose_lifestyle'


def token_pattern(token=TOKEN):
    return re.compile(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])')


def subst(text, domain, token=TOKEN):
    return token_pattern(token).sub(lambda m: domain, text)
