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
- Multi-worker Token Caching
- Async Refund Handling
    """,
    'author': 'Your Company / Developer Name',
    'depends': ['payment', 'website_sale'],
    'data': [
        # 1. 必須先定義 Template (ID: airwallex_inline_form)
        'views/payment_airwallex_templates.xml', 
        
        # 2. 後端 View 配置
        'views/payment_provider_views.xml',
        
        # 3. 支付方式基礎數據 (如有引用則放前)
        'data/payment_method_data.xml',
        
        # 4. 最後建立 Provider 實例 (引用了 Template ID)
        'data/payment_provider_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_airwallex_dropin/static/src/interactions/payment_form.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}