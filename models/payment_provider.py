# -*- coding: utf-8 -*-
import logging
import requests
import uuid
from datetime import timedelta
from dateutil import parser as dateutil_parser

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Datetime

from odoo.addons.payment_airwallex import const

_logger = logging.getLogger(__name__)

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('airwallex', "Airwallex")], ondelete={'airwallex': 'set default'})
    
    # === 憑據配置 (保護敏感資料) ===
    airwallex_client_id = fields.Char(
        string="Airwallex Client ID", 
        groups='base.group_system'
    )
    airwallex_api_key = fields.Char(
        string="Airwallex API Key", 
        groups='base.group_system'
    )
    airwallex_webhook_secret = fields.Char(
        string="Airwallex Webhook Secret", 
        groups='base.group_system',
        copy=False
    )

    # === Token 緩存機制 ===
    airwallex_access_token = fields.Char(groups='base.group_system', copy=False)
    airwallex_token_expiry = fields.Datetime(groups='base.group_system', copy=False)

    # === 1. 特性支援配置 ===
    def _compute_feature_support_fields(self):
        """ 啟用 Airwallex 支援的功能 """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'airwallex').update({
            'support_refund': 'partial',
            'support_tokenization': True,
            'support_manual_capture': 'partial', 
        })

    def _get_default_payment_method_codes(self):
        """ 自動載入支付方式 """
        self.ensure_one()
        if self.code != 'airwallex':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    # === 2. 業務邏輯：建立支付意向 (修正版) ===
    def _airwallex_create_intent(self, transaction):
        """ 
        建立 PaymentIntent，包含 FORCE_3DS 與 return_url。
        """
        self.ensure_one()
        
        payload = {
            'request_id': f"INTENT_{transaction.reference}_{uuid.uuid4().hex[:6]}",
            'amount': transaction.amount,
            'currency': transaction.currency_id.name,
            'merchant_order_id': transaction.reference,
            'return_url': f"{self.get_base_url().rstrip('/')}/payment/airwallex/return",
            'metadata': {
                'odoo_transaction_id': transaction.id,
                'reference': transaction.reference,
            },
            # 根據官方文件，強制執行 3DS 驗證
            'payment_method_options': {
                'card': {
                    'three_ds_action': 'FORCE_3DS'
                }
            },
        }

        _logger.info("Airwallex: Creating intent for %s (3DS Forced)", transaction.reference)
        
        # 使用官方推薦的路徑 pa/payment_intents/create
        res = self._airwallex_make_request('pa/payment_intents/create', payload=payload)
        
        return {
            'intent_id': res.get('id'),
            'client_secret': res.get('client_secret'),
        }

    # === 3. API 通訊核心 ===

    def _airwallex_get_api_url(self, endpoint=None):
        """ 環境切換邏輯 """
        base_url = 'https://api.airwallex.com/api/v1/' if self.state == 'enabled' else 'https://api-demo.airwallex.com/api/v1/'
        return f"{base_url}{endpoint.lstrip('/')}" if endpoint else base_url

    def _airwallex_get_access_token(self):
        """ Token 緩存與自動刷新 """
        self.ensure_one()
        now = Datetime.now()
        
        # 預留 5 分鐘公差
        if self.airwallex_access_token and self.airwallex_token_expiry:
            if now + timedelta(minutes=5) < self.airwallex_token_expiry:
                return self.airwallex_access_token

        url = self._airwallex_get_api_url('authentication/login')
        headers = {
            'x-client-id': self.airwallex_client_id,
            'x-api-key': self.airwallex_api_key,
        }
        
        try:
            response = requests.post(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            token = data.get('token')
            expires_at = dateutil_parser.parse(data.get('expires_at')).replace(tzinfo=None)
            
            # 使用 sudo().write() 確保跨 Session 寫入
            self.sudo().write({
                'airwallex_access_token': token,
                'airwallex_token_expiry': expires_at,
            })
            
            return token
        except Exception as e:
            _logger.error("Airwallex Authentication Failed: %s", e)
            raise ValidationError(_("Airwallex 身份驗證失敗。"))

    def _airwallex_make_request(self, endpoint, method='POST', payload=None, **kwargs):
        """ 統一請求封裝與錯誤處理 """
        self.ensure_one()
        url = self._airwallex_get_api_url(endpoint)
        token = self._airwallex_get_access_token()
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }
        
        try:
            response = requests.request(method, url, json=payload, headers=headers, timeout=15)
            
            if not response.ok:
                error_msg = response.text
                try:
                    error_data = response.json()
                    error_msg = error_data.get('message') or error_data.get('error', {}).get('message', error_msg)
                except: pass
                
                _logger.error("Airwallex API Error (%s): %s", endpoint, error_msg)
                raise ValidationError(_("Airwallex 請求失敗: %s", error_msg))
                
            return response.json()
        except requests.exceptions.RequestException as e:
            _logger.error("Airwallex Network Error: %s", e)
            raise ValidationError(_("無法連接至 Airwallex 伺服器。"))

    # === 4. 自動化動作 ===

    def action_airwallex_create_webhook(self):
        """ 一鍵建立 Webhook 並儲存 Secret """
        self.ensure_one()
        
        webhook_route = '/payment/airwallex/webhook'
        base_url = self.get_base_url()
        webhook_url = f"{base_url.rstrip('/')}{webhook_route}"
        
        payload = {
            'url': webhook_url,
            'version': '2023-11-01',
            'events': const.SUPPORTED_WEBHOOK_EVENTS,
            'request_id': str(uuid.uuid4()),
        }
        
        webhook_data = self._airwallex_make_request('webhooks/create', payload=payload)
        self.sudo().write({
            'airwallex_webhook_secret': webhook_data.get('secret'),
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _("Airwallex Webhook 建立成功！"),
                'type': 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }