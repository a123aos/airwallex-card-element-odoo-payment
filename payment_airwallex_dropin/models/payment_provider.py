# -*- coding: utf-8 -*-
import json
import logging
import requests
import re
import uuid
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('airwallex', "Airwallex")], 
        ondelete={'airwallex': 'set default'}
    )
    
    # 憑證與金鑰設定
    airwallex_client_id = fields.Char(string="Airwallex Client ID", groups='base.group_system')
    airwallex_api_key = fields.Char(string="Airwallex API Key", groups='base.group_system', password=True)
    airwallex_webhook_secret = fields.Char(string="Airwallex Webhook Secret", groups='base.group_system', password=True)
    
    # Access Token 快取
    #airwallex_access_token = fields.Char(groups='base.group_system')
    #airwallex_token_expiry = fields.Datetime(groups='base.group_system')

    def _airwallex_get_access_token(self):
        """ 獲取並快取 Access Token，具備時區清洗與 2 分鐘緩衝 """
        self.ensure_one()
        now = fields.Datetime.now()
        
        if self.airwallex_access_token and self.airwallex_token_expiry and self.airwallex_token_expiry > now + timedelta(minutes=2):
            return self.airwallex_access_token

        base_url = "https://api-demo.airwallex.com" if self.state == 'test' else "https://api.airwallex.com"
        headers = {
            'x-client-id': self.airwallex_client_id,
            'x-api-key': self.airwallex_api_key,
            'Content-Type': 'application/json',
        }
        
        try:
            _logger.info("Airwallex: 請求新 Token")
            response = requests.post(f"{base_url}/api/v1/authentication/login", headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # 精確解析並清洗時區字串
            expires_at_str = data.get('expires_at')
            if expires_at_str:
                clean_str = re.sub(r'(Z|[+-]\d{2}:\d{2})$', '', expires_at_str)
                expiry = fields.Datetime.to_datetime(clean_str)
            else:
                expiry = now + timedelta(minutes=30)
            
            self.write({
                'airwallex_access_token': data.get('token'),
                'airwallex_token_expiry': expiry,
            })
            return data.get('token')
        except Exception as e:
            _logger.error("Airwallex Auth Error: %s", str(e))
            raise UserError(_("Airwallex 身份驗證失敗。"))

    def _get_airwallex_inline_form_values(self):
        self.ensure_one()
        return json.dumps({'env': 'demo' if self.state == 'test' else 'prod'})

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    airwallex_intent_id = fields.Char(string="Airwallex Intent ID", readonly=True)
    airwallex_client_secret = fields.Char(string="Airwallex Client Secret", readonly=True)
    airwallex_intent_at = fields.Datetime(string="Intent Created At", readonly=True)

    def _airwallex_create_intent(self):
        """ 建立或刷新 Intent，具備 50 分鐘檢查與確定性 UUID """
        self.ensure_one()
        now = fields.Datetime.now()
        
        if self.airwallex_client_secret and self.airwallex_intent_at and \
           (now - self.airwallex_intent_at).total_seconds() < 3000:
            return True

        provider = self.provider_id
        token = provider._airwallex_get_access_token()
        base_url = "https://api-demo.airwallex.com" if provider.state == 'test' else "https://api.airwallex.com"
        
        # 使用 uuid5 確保 request_id 格式正確且具備冪等性
        request_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"odoo-{self.reference}-{self.id}"))

        payload = {
            "request_id": request_id,
            "amount": float(self.amount),
            "currency": self.currency_id.name,
            "merchant_order_id": self.reference,
            "return_url": f"{provider.get_base_url()}/payment/status",
            "metadata": {
                "odoo_transaction_id": self.id,
                "odoo_reference": self.reference
            }
        }

        if provider.capture_manually:
            payload["payment_method_options"] = {"card": {"auto_capture": False}}

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

        try:
            res = requests.post(f"{base_url}/api/v1/pa/payment_intents/create", json=payload, headers=headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            
            self.write({
                'airwallex_intent_id': data.get('id'),
                'airwallex_client_secret': data.get('client_secret'),
                'airwallex_intent_at': now,
            })
            return True
        except Exception as e:
            _logger.error("Airwallex Intent Error: %s", str(e))
            return False

    def _handle_notification_data(self, provider_code, data):
        """ 
        處理 Webhook 狀態同步，包含風控審核與手動請款邏輯 
        """
        if provider_code != 'airwallex':
            return super()._handle_notification_data(provider_code, data)

        status = data.get('status')
        _logger.info("Airwallex Webhook: 交易 %s 狀態更新為 [%s]", self.reference, status)

        if status == 'SUCCEEDED':
            # 支付成功並扣款
            self._set_done()
            
        elif status == 'REQUIRES_CAPTURE':
            # 已授權，等待手動請款
            self._set_authorized()
            
        elif status in ['PENDING', 'PENDING_REVIEW']:
            # PENDING: 異步支付處理中
            # PENDING_REVIEW: 風控審核中，需等待後續結果
            self._set_pending()
            
        elif status in ['CANCELLED', 'REJECTED']:
            self._set_canceled()
            
        elif status == 'FAILED':
            error_msg = data.get('last_payment_error', {}).get('message', '支付失敗')
            self._set_error(error_msg)

    def _get_specific_rendering_values(self):
        res = super()._get_specific_rendering_values()
        if self.provider_code != 'airwallex':
            return res
        res.update({'reference': self.reference})
        return res