# -*- coding: utf-8 -*-
import json
import hmac
import hashlib
import logging

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)

class AirwallexController(http.Controller):

    # -------------------------------------------------------------------------
    # 1. 前端呼叫：建立或獲取 Payment Intent
    # -------------------------------------------------------------------------
    @http.route('/payment/airwallex/create_intent', type='json', auth='public', methods=['POST'], csrf=False)
    def airwallex_create_intent(self, reference, **kwargs):
        """
        JS 呼叫此接口獲取最新 client_secret。
        適配最新 Model：處理回傳的字典 (Dict) 結構。
        """
        try:
            _logger.info("Airwallex: 收到 Intent 請求 (交易參考: %s)", reference)
            
            # 獲取交易記錄 (使用 sudo 確保權限)
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('reference', '=', reference)
            ], limit=1)
            
            if not tx_sudo:
                _logger.error("Airwallex: 找不到對應的交易記錄 %s", reference)
                return {'error': _('找不到交易記錄')}

            # 呼叫 Model 層：現在回傳的是 {'intent_id': ..., 'client_secret': ...} 或 {'error': ...}
            result = tx_sudo._airwallex_create_intent()
            
            # 檢查 Model 是否回傳了錯誤訊息
            if 'error' in result:
                _logger.error("Airwallex: 交易 %s 初始化失敗 - %s", reference, result['error'])
                return {'error': result['error']}

            # 成功時回傳給前端 JS
            return {
                'intent_id': result['intent_id'],
                'client_secret': result['client_secret'],
            }

        except Exception as e:
            _logger.exception("Airwallex Create Intent 發生未預期異常")
            return {'error': str(e)}

    # -------------------------------------------------------------------------
    # 2. Webhook 處理：嚴格簽名驗證 (HMAC-SHA256)
    # -------------------------------------------------------------------------
    @http.route('/payment/airwallex/webhook', type='http', auth='public', methods=['POST'], csrf=False)
    def airwallex_webhook(self):
        """
        Airwallex Webhook 進入點。
        安全性核心：驗證 x-signature 確保請求來自 Airwallex 官方。
        """
        # A. 獲取原始 Request Data 與 Header
        raw_body_bytes = request.httprequest.get_data()
        raw_body_str = raw_body_bytes.decode('utf-8')
        timestamp = request.httprequest.headers.get('x-timestamp')
        received_signature = request.httprequest.headers.get('x-signature')

        # B. 獲取 Webhook Secret
        provider_sudo = request.env['payment.provider'].sudo().search([
            ('code', '=', 'airwallex'),
            ('state', '!=', 'disabled')
        ], limit=1)
        
        # 確保後端有設定 Webhook Secret
        webhook_secret = provider_sudo.airwallex_webhook_secret

        if not all([webhook_secret, timestamp, received_signature]):
            _logger.error("Airwallex Webhook: 缺少必要驗證資訊 (Timestamp/Signature/Secret)")
            return request.make_response('UNAUTHORIZED', status=401)

        # C. 【核心安全性】計算 HMAC-SHA256 簽名
        # 官方規範：signature = hmac_sha256(webhook_secret, timestamp + raw_body)
        string_to_sign = timestamp + raw_body_str
        expected_signature = hmac.new(
            webhook_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # D. 安全比較簽名 (防範計時攻擊)
        if not hmac.compare_digest(expected_signature, received_signature):
            _logger.warning("Airwallex Webhook: 簽名驗證失敗！")
            return request.make_response('INVALID_SIGNATURE', status=401)

        # E. 解析資料並同步至 Model
        try:
            notification = json.loads(raw_body_str)
            event_type = notification.get('name')
            # 取得核心數據物件 (PaymentIntent Object)
            payload = notification.get('data', {}).get('object', {})
            intent_id = payload.get('id')

            _logger.info("Airwallex Webhook 驗證通過: %s (Intent: %s)", event_type, intent_id)

            # 透過 Model 層的搜尋邏輯定位 Transaction
            tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
                'airwallex', payload
            )

            if tx_sudo:
                # 更新訂單狀態機
                tx_sudo._handle_notification_data('airwallex', payload)
                _logger.info("Airwallex Webhook: 交易 %s 狀態已更新", tx_sudo.reference)
            else:
                _logger.warning("Airwallex Webhook: 無法匹配交易 (Intent ID: %s)", intent_id)

            return request.make_response('OK', status=200)

        except Exception as e:
            _logger.error("Airwallex Webhook 處理異常: %s", str(e))
            return request.make_response('INTERNAL_SERVER_ERROR', status=500)