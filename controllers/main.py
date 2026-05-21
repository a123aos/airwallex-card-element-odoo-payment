# -*- coding: utf-8 -*-
import hashlib
import hmac
import logging
import time
import json
import re

from werkzeug.exceptions import Forbidden

from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)

class AirwallexController(http.Controller):
    _webhook_url = '/payment/airwallex/webhook'
    _return_url = '/payment/airwallex/return'
    WEBHOOK_AGE_TOLERANCE = 10 * 60  # 600 秒

    @http.route(_webhook_url, type='http', auth='public', methods=['POST'], csrf=False)
    def airwallex_webhook(self):
        """ 處理 Airwallex Webhook (支援退款識別與簽名驗證版本) """
        
        # 1. 獲取原始數據
        raw_data = request.httprequest.data
        headers = request.httprequest.headers

        # 2. 提取識別資訊：merchant_order_id (支付) 或 object.id (退款 rfd_...)
        merchant_order_id = self._extract_merchant_order_id(raw_data)
        provider_ref = self._extract_object_id(raw_data) 

        tx_sudo = None

        # 優先：使用 Airwallex 物件 ID 查找 (適用於退款交易的 provider_reference)
        if provider_ref:
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('provider_reference', '=', provider_ref),
                ('provider_code', '=', 'airwallex')
            ], limit=1)

        # 備援：使用商戶訂單號查找 (適用於初始支付交易的 reference)
        if not tx_sudo and merchant_order_id:
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('reference', '=', merchant_order_id),
                ('provider_code', '=', 'airwallex')
            ], limit=1)

        # 如果兩者都找不到，則忽略該 Webhook
        if not tx_sudo:
            _logger.warning("Airwallex: 找不到對應交易 (provider_ref=%s, order_id=%s)。", 
                            provider_ref, merchant_order_id)
            return request.make_json_response({"status": "not_found"}, status=200)

        # 3. 驗證簽名 (必須在 tx_sudo 確定後，才能獲取對應 provider 的 secret)
        try:
            self._verify_signature(headers, raw_data, tx_sudo.provider_id)
        except Forbidden as e:
            _logger.error("Airwallex Webhook 簽名驗證失敗: %s", e)
            raise Forbidden()

        # 4. 簽名驗證通過後解析 JSON
        try:
            data = json.loads(raw_data)
        except (ValueError, TypeError):
            return request.make_json_response({"status": "invalid_json"}, status=400)

        air_obj = data.get('data', {}).get('object', {})
        _logger.info("Airwallex: 簽名驗證成功，處理交易 %s (狀態: %s)", 
                     tx_sudo.reference, air_obj.get('status'))

        # 5. 執行 Odoo 支付處理流程
        tx_sudo._process('airwallex', {'airwallex_obj': air_obj})

        return request.make_json_response({"status": "accepted"}, status=200)

    def _extract_object_id(self, raw_data):
        """ 從 Raw Body 中安全提取 Airwallex 物件 ID (如 rfd_...) """
        try:
            content = raw_data.decode('utf-8')
            # 優先搜尋 data.object 內的 id
            match = re.search(r'"data"\s*:\s*\{[^}]*"object"\s*:\s*\{[^}]*"id"\s*:\s*"([^"]+)"', content)
            if not match:
                # 備援匹配格式為 rfd_ 開頭的字串
                match = re.search(r'"id"\s*:\s*"(rfd_[^"]+)"', content)
            return match.group(1) if match else None
        except Exception:
            return None

    def _extract_merchant_order_id(self, raw_data):
        """ 從 Raw Body 中安全提取 merchant_order_id """
        try:
            content = raw_data.decode('utf-8')
            match = re.search(r'"merchant_order_id"\s*:\s*"([^"]+)"', content)
            return match.group(1) if match else None
        except Exception:
            return None

    @staticmethod
    def _verify_signature(headers, raw_data, provider_sudo):
        """ 依照 Airwallex 規範驗證簽名 """
        timestamp = headers.get('x-timestamp')
        signature = headers.get('x-signature')

        if not timestamp or not signature:
            raise Forbidden("Missing signature headers")

        webhook_secret = provider_sudo.airwallex_webhook_secret
        if not webhook_secret:
            raise Forbidden("Webhook Secret not configured")

        signed_payload = timestamp.encode('utf-8') + raw_data
        expected_sig = hmac.new(
            webhook_secret.encode('utf-8'),
            msg=signed_payload,
            digestmod=hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            raise Forbidden("Invalid signature")

    @http.route(_return_url, type='http', auth='public', methods=['GET'], csrf=False, save_session=False)
    def airwallex_return(self, **data):
        """ 處理同步跳轉回傳 """
        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference('airwallex', data)
        if tx_sudo:
            try:
                air_obj = tx_sudo.provider_id._airwallex_make_request(
                    f'pa/payment_intents/{tx_sudo.provider_reference}', method='GET'
                )
                tx_sudo._process('airwallex', {'airwallex_obj': air_obj})
            except Exception:
                _logger.error("Airwallex: Return 查詢失敗")
        return request.redirect('/payment/status')