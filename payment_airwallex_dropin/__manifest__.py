{
    'name': 'Airwallex Drop-in Payment Provider',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 10,
    'summary': 'Airwallex Drop-in 嵌入式付款',
    'author': 'Jeffrey',
    'depends': ['payment'], # 核心依賴
    'data': [
        'views/payment_airwallex_templates.xml',  # 1. 先定義前端模板 ID
        'views/payment_provider_views.xml',       # 2. 繼承後台視圖
        'data/payment_provider_data.xml',         # 3. 建立 Provider 預設資料
    ],
    'assets': {
        'web.assets_frontend': [
            # 這是之後放置 Airwallex SDK JS 的地方
            'payment_airwallex_dropin/static/src/js/payment_form.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}