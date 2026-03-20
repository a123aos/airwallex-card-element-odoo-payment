# -*- coding: utf-8 -*-

import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

class AirwallexController(http.Controller):

    @http.route('/payment/airwallex/get_intent', type='json', auth='public', methods=['POST'], csrf=False)
    def airwallex_get_intent(self, provider_id, **kwargs):
        """ 
        前端 JS 呼叫此接口以獲取 Airwallex 的 client_secret。
        這模仿了 Adyen 獲取付款會話的邏輯。
        """
        # 1. 獲取當前購物車或結帳流程中的交易記錄 (Transaction)
        # Odoo 19 通常會將 transaction_id 存在 session 中
        tx_id = request.session.get('__payment_tx_ids__')
        if not tx_id:
            return {'error': '找不到有效的交易記錄'}

        transaction = request.env['payment.transaction'].browse(tx_id[0])
        
        # 2. 調用我們在第九步寫好的模型方法
        try:
            intent_data = transaction._airwallex_get_client_secret()
            return {
                'client_secret': intent_data.get('client_secret'),
                'intent_id': intent_data.get('intent_id'),
            }
        except Exception as e:
            _logger.error("無法生成 Airwallex Intent: %s", e)
            return {'error': str(e)}