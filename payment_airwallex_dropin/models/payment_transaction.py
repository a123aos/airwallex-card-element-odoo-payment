# -*- coding: utf-8 -*-

import requests
import logging
from odoo import _, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _airwallex_get_client_secret(self):
        """ 向 Airwallex 請求 Access Token 並建立 Intent，返回 client_secret """
        self.ensure_one()
        provider = self.provider_id
        
        # 1. 獲取 Access Token
        auth_url = 'https://api-demo.airwallex.com/api/v1/authentication/login'
        headers = {
            'x-client-id': provider.airwallex_client_id,
            'x-api-key': provider.airwallex_api_key,
        }
        try:
            auth_response = requests.post(auth_url, headers=headers, timeout=10)
            auth_response.raise_for_status()
            token = auth_response.json().get('token')
        except Exception as e:
            _logger.error("Airwallex 認證失敗: %s", e)
            raise ValidationError("無法連接至 Airwallex 認證伺服器")

        # 2. 建立 Payment Intent
        create_url = 'https://api-demo.airwallex.com/api/v1/pa/payment_intents/create'
        intent_data = {
            'request_id': f'TRANSACTION_{self.id}',
            'amount': self.amount,
            'currency': self.currency_id.name,
            'merchant_order_id': self.reference,
        }
        headers = {'Authorization': f'Bearer {token}'}
        
        try:
            intent_response = requests.post(create_url, headers=headers, json=intent_data, timeout=10)
            intent_response.raise_for_status()
            res_data = intent_response.json()
            return {
                'client_secret': res_data.get('client_secret'),
                'intent_id': res_data.get('id'),
            }
        except Exception as e:
            _logger.error("Airwallex 建立 Intent 失敗: %s", e)
            raise ValidationError("Airwallex 支付初始化失敗")