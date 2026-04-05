# -*- coding: utf-8 -*-
import logging
import requests
from uuid import uuid4
from datetime import timedelta
from dateutil import parser as dateutil_parser

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.addons.payment.const import SENSITIVE_KEYS
from odoo.addons.payment.logging import get_payment_logger

_logger = get_payment_logger(__name__, sensitive_keys=SENSITIVE_KEYS)

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(selection_add=[('airwallex', 'Airwallex')], ondelete={'airwallex': 'set default'})

    # 配置欄位
    airwallex_client_id = fields.Char(string="Airwallex Client ID")
    airwallex_api_key = fields.Char(string="Airwallex API Key")
    airwallex_webhook_secret = fields.Char(string="Airwallex Webhook Secret", groups='base.group_system')

    # Token 管理（自動過期檢查）
    airwallex_access_token = fields.Char(string="Access Token", groups='base.group_system')
    airwallex_token_expiry = fields.Datetime(string="Token Expiry")

    # === 1. 支援功能定義 ===

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        for provider in self.filtered(lambda p: p.code == 'airwallex'):
            provider.support_refund = 'partial'
            provider.support_tokenization = True
            provider.support_manual_capture = 'full_only'

    # === 2. API 基礎設施 ===

    def _airwallex_get_api_url(self):
        return "https://api.airwallex.com/api/v1" if self.state == 'enabled' else "https://api-demo.airwallex.com/api/v1"

    def _airwallex_get_access_token(self):
        """ 獲取有效的 Bearer Token """
        self.ensure_one()
        now = fields.Datetime.now()
        if self.airwallex_access_token and self.airwallex_token_expiry:
            if now + timedelta(minutes=2) < self.airwallex_token_expiry:
                return self.airwallex_access_token

        url = f"{self._airwallex_get_api_url()}/authentication/login"
        headers = {'x-client-id': self.airwallex_client_id, 'x-api-key': self.airwallex_api_key}

        try:
            response = requests.post(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            token = data.get('token')
            expires_at_str = data.get('expires_at')
            expires_at = dateutil_parser.parse(expires_at_str).replace(tzinfo=None) if expires_at_str else now + timedelta(minutes=30)

            self.write({'airwallex_access_token': token, 'airwallex_token_expiry': expires_at})
            return token
        except Exception as e:
            _logger.error("Airwallex Auth Failed: %s", str(e))
            raise ValidationError(_("Could not authenticate with Airwallex. Please check your credentials."))

    def _airwallex_make_request(self, endpoint, data=None, method='POST'):
        """ 通用請求封裝 """
        self.ensure_one()
        url = f"{self._airwallex_get_api_url()}/{endpoint.lstrip('/')}"
        token = self._airwallex_get_access_token()
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'}
        
        try:
            response = requests.request(method, url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error("Airwallex API Error (%s): %s", endpoint, e.response.text if e.response else str(e))
            raise ValidationError(_("Airwallex API Error: %s", endpoint))

    # === 3. 業務動作 ===

    def _airwallex_create_intent(self, transaction):
        """ 建立交易意向，供 Transaction 呼叫 """
        self.ensure_one()
        payload = {
            'request_id': str(uuid4()),
            'amount': transaction.amount,
            'currency': transaction.currency_id.name,
            'merchant_order_id': transaction.reference,
            'metadata': {'odoo_id': transaction.id}
        }
        data = self._airwallex_make_request('pa/payment_intents/create', data=payload)
        return {
            'intent_id': data.get('id'),
            'client_secret': data.get('client_secret'),
        }