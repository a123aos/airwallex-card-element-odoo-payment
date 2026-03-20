{
    'name': 'Airwallex Drop-in Payment Provider',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Acquirers',
    'sequence': 10,
    'summary': 'Airwallex Drop-in 嵌入式付款（自訂）',
    'description': '支援 Airwallex Drop-in Element 的 custom payment provider',
    'author': 'Your Name',
    'depends': ['payment', 'website_sale'],
    'data': [
        # 1. 必須先載入基礎視圖與模板，這樣系統才會註冊 ID
        'views/payment_airwallex_templates.xml',
        'views/payment_provider_views.xml',
	'data/payment_provider_data.xml',
	'data/payment_method.xml',
        
        # 2. 最後載入依賴上述模板的資料檔
        'data/payment_provider_data.xml', 
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_airwallex_dropin/static/src/js/dropin.js',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}