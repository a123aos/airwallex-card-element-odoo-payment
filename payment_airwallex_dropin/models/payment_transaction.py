# -*- coding: utf-8 -*-
import logging
import uuid
from odoo import api, fields, models, _
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    airwallex_intent_id = fields.Char(string="Airwallex Intent ID", readonly=True, index=True)
    airwallex_client_secret = fields.Char(string="Airwallex Client Secret", readonly=True)
    airwallex_intent_at = fields.Datetime(string="Intent Created At", readonly=True)

    def _airwallex_create_intent(self):
        """ 建立或刷新 Airwallex Payment Intent (具備冪等性) """
        self.ensure_one()
        now = fields.Datetime.now()
        
        # 1. 檢查有效期 (50分鐘緩衝)
        if self.airwallex_intent_id and self.airwallex_intent_at:
            if (now - self.airwallex_intent_at).total_seconds() < 3000:
                return {
                    'intent_id': self.airwallex_intent_id,
                    'client_secret': self.airwallex_client_secret,
                }

        # 2. 生成確定的 UUID 作為 request_id，確保重試時不會產生重複 Intent
        # 格式：odoo-{database_name}-{transaction_id}
        namespace_str = f"odoo-{self.env.cr.dbname}-{self.id}"
        deterministic_request_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, namespace_str))

        # 3. 準備 Payload (補回 return_url)
        base_url = self.provider_id.get_base_url()
        payload = {
            'request_id': deterministic_request_id,
            'amount': self.amount,
            'currency': self.currency_id.name,
            'merchant_order_id': self.reference,
            'return_url': f"{base_url}/payment/status", # 支付完成後的重定向網址
        }

        try:
            data = self.provider_id._make_airwallex_request('/pa/payment_intents/create', payload=payload, method='POST')
            
            if not data or 'id' not in data:
                _logger.error("Airwallex API 回傳異常: %s", data)
                return {'error': 'Invalid API response'}

            self.write({
                'airwallex_intent_id': data['id'],
                'airwallex_client_secret': data['client_secret'],
                'airwallex_intent_at': now,
            })
            
            return {
                'intent_id': data['id'],
                'client_secret': data['client_secret'],
            }
        except Exception as e:
            _logger.exception("Airwallex Intent 建立失敗")
            return {'error': str(e)}

    # ====================== Webhook 處理邏輯 ======================

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ 根據 Webhook 傳來的 Intent ID 尋找交易紀錄 """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'airwallex' or len(tx) == 1:
            return tx
        
        # Controller 應確保傳入的是 data.object
        intent_id = notification_data.get('id')
        if not intent_id:
            return tx
            
        return self.search([('airwallex_intent_id', '=', intent_id), ('provider_code', '=', 'airwallex')], limit=1)

    def _handle_notification_data(self, provider_code, notification_data):
        """ 根據 Airwallex 狀態機更新 Odoo 訂單狀態 """
        tx = super()._handle_notification_data(provider_code, notification_data)
        if provider_code != 'airwallex' or tx.state in ['done', 'cancel']:
            return tx

        status = notification_data.get('status')
        _logger.info("處理 Airwallex Webhook: TX %s, Status %s", tx.reference, status)

        if status == 'SUCCEEDED':
            tx._set_done()
        elif status == 'REQUIRES_CAPTURE':
            tx._set_authorized()
        elif status in ['PENDING', 'PENDING_REVIEW']:
            tx._set_pending()
        elif status in ['FAILED', 'CANCELLED']:
            tx._set_canceled()
        
        return tx