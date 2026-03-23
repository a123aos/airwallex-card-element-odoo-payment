# -*- coding: utf-8 -*-
import logging
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # Airwallex 特定字段
    airwallex_intent_id = fields.Char(string="Airwallex Intent ID", readonly=True)
    airwallex_refund_id = fields.Char(string="Airwallex Refund ID", readonly=True)

    def _get_specific_processing_values(self, processing_values):
        """从渲染值中存储 intent_id"""
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != 'airwallex':
            return res
        self.airwallex_intent_id = processing_values.get('airwallex_intent_id')
        return res

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """通过 intent_id 查找交易（Webhook 场景）"""
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'airwallex' or len(tx) == 1:
            return tx

        # 尝试从 notification_data 获取 intent_id（兼容不同事件结构）
        intent_id = notification_data.get('airwallex_intent_id')
        if not intent_id:
            # 备用：从 webhook 标准结构 data.object.id 提取
            intent_id = notification_data.get('data', {}).get('object', {}).get('id')
        
        if not intent_id:
            raise ValidationError(_("Airwallex: No intent ID found in notification data."))
        
        tx = self.search([
            ('airwallex_intent_id', '=', intent_id),
            ('provider_code', '=', 'airwallex')
        ], limit=1)
        if not tx:
            raise ValidationError(_("Airwallex: No transaction found for Intent ID %s.", intent_id))
        return tx

    def _handle_notification_data(self, provider_code, notification_data):
        """处理 Airwallex 的支付通知（用于 Return URL 场景）"""
        tx = super()._handle_notification_data(provider_code, notification_data)
        if provider_code != 'airwallex':
            return tx

        # ✅ 获取状态（API 返回值为大写，如 'SUCCEEDED'）
        if 'status' in notification_data:
            intent_info = notification_data
        else:
            # 理论上不应发生：控制器应已传递完整 intent_info
            intent_info = self.provider_id._airwallex_make_request(
                f'/pa/payment_intents/{self.airwallex_intent_id}', 
                method='GET'
            )
        
        status = intent_info.get('status', '')  # ✅ 保持原始大写格式

        # ✅ 状态映射（按 Airwallex 文档使用大写值）
        if status == 'SUCCEEDED':
            tx._set_done()
            _logger.info("Airwallex: Transaction %s set to 'done' (status: %s)", tx.reference, status)
        elif status in ['REQUIRES_CAPTURE', 'REQUIRES_CUSTOMER_ACTION', 'PENDING', 'PENDING_REVIEW']:
            tx._set_pending()
            _logger.info("Airwallex: Transaction %s set to 'pending' (status: %s)", tx.reference, status)
        elif status in ['CANCELLED', 'EXPIRED']:
            tx._set_canceled()
            _logger.info("Airwallex: Transaction %s set to 'canceled' (status: %s)", tx.reference, status)
        elif status == 'REQUIRES_PAYMENT_METHOD':
            tx._set_error(_("Payment requires a new payment method."))
            _logger.warning("Airwallex: Transaction %s set to 'error' (requires_payment_method)", tx.reference)
        else:
            # 未知状态，保持 pending 并记录警告
            _logger.warning("Airwallex: Unknown status '%s' for transaction %s - keeping 'pending'", status, tx.reference)
            tx._set_pending()

        return tx

    def _send_refund_request(self, amount_to_refund=None):
        """发起 Airwallex 退款（使用 /pa/refunds/create）"""
        if self.provider_code != 'airwallex':
            return super()._send_refund_request(amount_to_refund=amount_to_refund)

        # 生成唯一的退款请求 ID
        refund_ref = f"REFUND-{self.reference}-{fields.Datetime.now().strftime('%y%m%d%H%M%S')}"
        refund_amount = amount_to_refund or self.amount

        payload = {
            'request_id': refund_ref,
            'payment_intent_id': self.airwallex_intent_id,
            'amount': refund_amount,  # ✅ 浮点数（如 10.50）
            'reason': 'requested_by_customer',  # ✅ 可选，最长 128 字符
        }

        _logger.info("Airwallex: Initiating refund for tx %s, payload: %s", self.reference, payload)
        
        try:
            refund_data = self.provider_id._airwallex_make_request(
                '/pa/refunds/create',
                payload=payload,
                method='POST'
            )
        except Exception as e:
            _logger.error("Airwallex: Refund API call failed for tx %s: %s", self.reference, str(e))
            raise ValidationError(_("Failed to initiate refund: %s") % str(e))

        if 'error' in refund_data:
            error_msg = refund_data['error'].get('message', 'Unknown error') if isinstance(refund_data['error'], dict) else str(refund_data['error'])
            _logger.error("Airwallex: Refund API error for tx %s: %s", self.reference, error_msg)
            raise ValidationError(_("Airwallex Refund Error: %s") % error_msg)

        refund_id = refund_data.get('id')
        if refund_id:
            self.airwallex_refund_id = refund_id
            _logger.info("Airwallex: Refund created with ID %s for tx %s", refund_id, self.reference)
        else:
            _logger.warning("Airwallex: Refund response missing 'id' field: %s", refund_data)

        self._set_pending()  # ✅ 退款请求后设为 pending（等待 webhook 确认）
        return self