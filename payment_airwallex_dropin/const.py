# -*- coding: utf-8 -*-

from odoo.addons.payment.const import SENSITIVE_KEYS as PAYMENT_SENSITIVE_KEYS

# 1. 安全過濾
SENSITIVE_KEYS = {'airwallex_client_secret', 'airwallex_api_key', 'airwallex_webhook_secret'}
PAYMENT_SENSITIVE_KEYS.update(SENSITIVE_KEYS)

# 2. API 端點 (Base path /api/v1/)
AUTH_URL = 'authentication/login'
CREATE_INTENT_URL = 'pa/payment_intents/create' # 補上 pa/ 路徑

# 3. 狀態映射 (嚴格對應官方生命週期)
# 參考: https://www.airwallex.com/docs/payments/reference/payment-statuses
STATUS_MAPPING = {
    'draft': (
        'REQUIRES_PAYMENT_METHOD',  # 初始狀態或支付失敗後回退
        'REQUIRES_CUSTOMER_ACTION'  # 等待 3DS 或 Redirect
    ),
    'pending': (
        'PENDING', 
        'PENDING_REVIEW'            # 風控審核中
    ),
    'authorized': (
        'REQUIRES_CAPTURE',         # 已授權，待請款
    ),
    'done': (
        'SUCCEEDED',                # 資金已確認
    ),
    'cancel': (
        'CANCELLED',                # 已取消
    ),
    # 註：雖然文檔未明列 FAILED 為 PI 狀態，但作為防禦性編程保留
    'error': ('FAILED',), 
}

# 4. Webhook 事件 (修正非官方事件名稱並補強)
# 參考: https://www.airwallex.com/docs/payments/reference/payments-webhooks
HANDLED_WEBHOOK_EVENTS = [
    'payment_intent.succeeded',
    'payment_intent.cancelled',
    'payment_intent.requires_capture',
    'payment_intent.requires_payment_method', # 替代 payment_failed
    'payment_intent.pending_review',          # 處理風控通知
    'payment_intent.requires_customer_action', # 處理 3DS 切換
]

# 5. 貨幣精確度 (Airwallex 預設通常遵循 ISO 4217，但在零小數位貨幣上需注意)
# 這裡可以根據具體業務需求擴充