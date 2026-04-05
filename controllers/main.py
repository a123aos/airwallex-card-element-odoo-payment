# -*- coding: utf-8 -*-
import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

class AirwallexController(http.Controller):

    @http.route('/payment/airwallex/webhook', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def airwallex_webhook(self):
        """ 接收 Airwallex Webhook 通知的入口 """
        data = json.loads(request.httprequest.data)
        _logger.info("收到 Airwallex Webhook: %s", data)

        try:
            # 使用 sudo 權限執行，因為 Webhook 是 public 身份
            tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data('airwallex', data)
            
            # 檢查是否已經處理過這個 Event
            if tx_sudo.airwallex_last_event_id == data.get('id'):
                return request.make_json_response({'status': 'already_processed'}, status=200)

            tx_sudo._process_notification_data(data)
            return request.make_json_response({'status': 'ok'}, status=200)

        except Exception as e:
            _logger.error("處理 Webhook 出錯: %s", str(e))
            # 回傳 200 或 400 取決於你是否希望 Airwallex 重試，通常邏輯錯誤回 200 防止無限重試
            return request.make_json_response({'error': str(e)}, status=200)