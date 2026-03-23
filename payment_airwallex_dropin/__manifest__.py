# -*- coding: utf-8 -*-
{
    'name': 'Payment Provider: Airwallex Drop-in',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'summary': 'Airwallex Components SDK (Drop-in) integration for Odoo 19.',
    'description': """
Airwallex Payment Gateway Integration:
- Support for Components SDK (Drop-in)
- Secure Webhook verification (HMAC-SHA256)
- Multi-worker Token Caching (FOR UPDATE)
- Async Refund Handling
    """,
    'author': 'Your Company / Developer Name',
    'depends': ['payment', 'website_sale'],
    'data': [
    'data/payment_method_data.xml',
    'data/payment_provider_data.xml',
    'views/payment_provider_views.xml',
],
'qweb': [
    'static/src/xml/airwallex_templates.xml',
],
    'assets': {
        'web.assets_frontend': [
            'payment_airwallex_dropin/static/src/js/payment_form.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}