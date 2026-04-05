# -*- coding: utf-8 -*-
import logging
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # 新增欄位儲存 Airwallex 資訊
    airwallex_client_secret = fields.Char(string="Airwallex Client Secret", readonly=True)
    airwallex_last_event_id = fields.Char(string="Last Airwallex Event ID", readonly=True, index=True)

    def _get_specific_processing_values(self, processing_values):
        """ 
        處理前端支付時所需的參數 
        解決 "Missing Airwallex configuration" 報錯
        """
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != 'airwallex':
            return res

        # 呼叫 provider 裡的 API 創建 Intent
        intent_data = self.provider_id._airwallex_create_intent(self)
        
        # 將回傳的 Intent ID 存入 Odoo 標準的 provider_reference 欄位
        self.provider_reference = intent_data.get('intent_id')
        self.airwallex_client_secret = intent_data.get('client_secret')

        # 這裡的 Key 必須與前端 JS 讀取的變數名一致
        res.update({
            'airwallex_intent_id': self.provider_reference,
            'airwallex_client_secret': self.airwallex_client_secret,
        })
        return res

    @api.model
    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ 
        Webhook 用：根據回傳數據尋找 Odoo 交易紀錄
        修正了 super() 報錯問題
        """
        if provider_code != 'airwallex':
            return super()._get_tx_from_notification_data(provider_code, notification_data)

        data_obj = notification_data.get('data', {}).get('object', {})
        
        # 1. 優先嘗試用 merchant_order_id (即 Odoo 的 reference) 搜尋
        reference = data_obj.get('merchant_order_id') or notification_data.get('merchant_order_id')
        tx = self.search([('reference', '=', reference), ('provider_code', '=', 'airwallex')], limit=1)

        # 2. 如果找不到，嘗試用 Intent ID (provider_reference) 搜尋
        if not tx:
            intent_id = data_obj.get('id') or data_obj.get('payment_intent_id')
            if intent_id:
                tx = self.search([('provider_reference', '=', intent_id), ('provider_code', '=', 'airwallex')], limit=1)

        if not tx:
            _logger.error("Airwallex: 找不到匹配的交易。數據: %s", notification_data)
            raise ValidationError("Airwallex: " + _("No transaction found."))
        
        return tx

    def _process_notification_data(self, notification_data):
        """ 處理 Webhook 狀態更新 """
        if self.provider_code != 'airwallex':
            return super()._process_notification_data(notification_data)

        # 儲存事件 ID 以實現冪等性（防止重複處理）
        self.airwallex_last_event_id = notification_data.get('id')
        
        data_obj = notification_data.get('data', {}).get('object', notification_data)
        status = data_obj.get('status')

        _logger.info("Airwallex: 開始處理交易 %s，狀態為 %s", self.reference, status)

        if status in ['SUCCEEDED', 'SETTLED', 'PAID']:
            self._set_done()
        elif status in ['PENDING', 'REQUIRES_CUSTOMER_ACTION', 'REQUIRES_CAPTURE']:
            self._set_pending()
        elif status == 'CANCELLED':
            self._set_canceled()
        elif status == 'FAILED':
            error_msg = data_obj.get('latest_payment_attempt', {}).get('failure_details', {}).get('message', 'Unknown error')
            self._set_error(f"Airwallex: {error_msg}")