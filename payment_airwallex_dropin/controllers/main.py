# -*- coding: utf-8 -*-
import logging
import hmac
import hashlib
from pprint import pformat
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

class AirwallexController(http.Controller):

    @http.route('/payment/airwallex/return', type='http', auth='public', methods=['GET', 'POST'], csrf=False, save_session=False)
    def airwallex_return_from_checkout(self, **data):
        """處理從 Airwallex 支付頁面跳轉回 Odoo 的請求"""
        _logger.info("Airwallex: Return from checkout with data:\n%s", pformat(data))
        
        # 提取 payment_intent_id
        intent_id = data.get('payment_intent_id') or data.get('id')
        
        if not intent_id:
            _logger.warning("Airwallex: Return URL accessed without intent ID.")
            return request.redirect('/payment/status')

        # 查找交易紀錄
        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('airwallex_intent_id', '=', intent_id),
            ('provider_code', '=', 'airwallex')
        ], limit=1)

        if tx_sudo:
            # 主动查询 Intent 状态并处理
            intent_info = tx_sudo.provider_id._airwallex_make_request(
                f'/pa/payment_intents/{intent_id}', method='GET'
            )
            tx_sudo._handle_notification_data('airwallex', intent_info)
            _logger.info("Airwallex: Return processed for tx %s (status: %s)", 
                        tx_sudo.reference, intent_info.get('status'))
        else:
            _logger.warning("Airwallex: No transaction found for intent %s", intent_id)
        
        return request.redirect('/payment/status')

    @http.route('/payment/airwallex/webhook', type='jsonrpc', auth='public', methods=['POST'], csrf=False)
    def airwallex_webhook(self, **data):
        """處理 Airwallex 异步 Webhook（含签名验证）"""
        _logger.info("Airwallex Webhook received:\n%s", pformat(data))
        
        # ========== 1️⃣ 签名验证 ==========
        webhook_signature = request.httprequest.headers.get('x-signature')
        webhook_timestamp = request.httprequest.headers.get('x-timestamp')
        
        if not webhook_signature or not webhook_timestamp:
            _logger.warning("Airwallex Webhook: Missing signature headers (x-signature/x-timestamp).")
            return {'error': 'Missing signature headers'}
        
        # 获取 webhook secret
        provider = request.env['payment.provider'].sudo().search([
            ('code', '=', 'airwallex')
        ], limit=1)
        
        if not provider or not provider.airwallex_webhook_secret:
            _logger.error("Airwallex Webhook: No webhook secret configured in provider settings.")
            return {'error': 'Server misconfigured - missing webhook secret'}
        
        secret = provider.airwallex_webhook_secret
        raw_body = request.httprequest.get_data(as_text=True)
        
        # 按文档计算签名: timestamp + raw_body（无分隔符），hex digest
        value_to_digest = f"{webhook_timestamp}{raw_body}"
        expected_sig = hmac.new(
            secret.encode('utf-8'), 
            value_to_digest.encode('utf-8'), 
            hashlib.sha256
        ).hexdigest()
        
        # 恒定时间比较
        if not hmac.compare_digest(expected_sig, webhook_signature):
            # ✅ 安全警告：不记录签名值，仅记录验证失败
            _logger.warning(
                "Airwallex Webhook: Signature verification failed. "
                "Possible causes: wrong webhook secret, payload tampered, or replayed request."
            )
            return {'error': 'Invalid signature'}
        
        _logger.info("Airwallex Webhook: Signature verified successfully.")
        
        # ========== 2️⃣ 解析事件（✅ 根据文档结构获取）==========
        event_type = data.get('name')  # 文档使用 'name' 字段
        resource = data.get('data', {}).get('object', {})  # 文档：resource 在 data.object 下
        
        if not event_type:
            _logger.warning("Airwallex Webhook: Missing 'name' field in payload.")
            return {'error': 'Missing event name'}
        
        if not resource:
            _logger.warning("Airwallex Webhook: Missing resource object in data.object.")
            return {'error': 'Missing resource object'}
        
        _logger.debug("Airwallex Webhook: Event type: %s, Resource ID: %s", 
                     event_type, resource.get('id'))
        
        # ========== 3️⃣ 根据事件类型查找交易 ==========
        tx = None
        if event_type.startswith('payment_intent.'):
            # 支付事件：使用 payment_intent_id 查找
            intent_id = resource.get('id')
            if intent_id:
                tx = request.env['payment.transaction'].sudo().search([
                    ('airwallex_intent_id', '=', intent_id),
                    ('provider_code', '=', 'airwallex')
                ], limit=1)
        elif event_type.startswith('refund.'):
            # 退款事件：使用 refund_id 查找
            refund_id = resource.get('id')
            if refund_id:
                tx = request.env['payment.transaction'].sudo().search([
                    ('airwallex_refund_id', '=', refund_id),
                    ('provider_code', '=', 'airwallex')
                ], limit=1)
        
        if not tx:
            _logger.warning("Airwallex Webhook: No transaction found for event %s (resource ID: %s)", 
                          event_type, resource.get('id'))
            # 注意：某些事件（如refund.received）可能在tx记录前到达，不应返回错误
            if event_type.startswith('refund.') and event_type != 'refund.failed':
                _logger.info("Airwallex Webhook: Refund event for unknown refund ID %s - may be normal", 
                           refund_id)
                return {'status': 'ok'}  # 忽略未知退款事件
            return {'error': 'Transaction not found'}
        
        _logger.info("Airwallex Webhook: Found transaction %s for event %s", 
                    tx.reference, event_type)
        
        # ========== 4️⃣ 事件处理 ==========
        try:
            # --- PaymentIntent 事件 ---
            if event_type == 'payment_intent.succeeded':
                tx._set_done()
                _logger.info("Airwallex Webhook: Payment succeeded → tx %s set to 'done'", 
                            tx.reference)
            elif event_type == 'payment_intent.cancelled':
                tx._set_canceled()
                _logger.info("Airwallex Webhook: Payment cancelled → tx %s set to 'cancelled'", 
                            tx.reference)
            elif event_type in (
                'payment_intent.requires_customer_action',
                'payment_intent.requires_capture',
                'payment_intent.pending',
                'payment_intent.pending_review'
            ):
                tx._set_pending()
                _logger.info("Airwallex Webhook: Payment pending (event: %s) → tx %s set to 'pending'", 
                            event_type, tx.reference)
            elif event_type == 'payment_intent.requires_payment_method':
                tx._set_error(_("Payment requires a new payment method."))
                _logger.info("Airwallex Webhook: Requires payment method → tx %s set to 'error'", 
                            tx.reference)
            elif event_type in ('payment_intent.created', 'payment_intent.updated'):
                # 仅记录，不改变状态
                _logger.info("Airwallex Webhook: %s for tx %s (status unchanged)", 
                            event_type, tx.reference)
            
            # --- Refund 事件 ---
            elif event_type == 'refund.accepted':
                tx._set_done()
                _logger.info("Airwallex Webhook: Refund accepted → tx %s (refund: %s) set to 'done'", 
                            tx.reference, resource.get('id'))
            elif event_type == 'refund.failed':
                failure_reason = resource.get('failure_reason', 'Unknown reason')
                tx._set_error(_("Refund failed: %s", failure_reason))
                _logger.warning("Airwallex Webhook: Refund failed → tx %s set to 'error' (reason: %s)", 
                              tx.reference, failure_reason)
            elif event_type in ('refund.received', 'refund.settled'):
                # 记录但不改变交易状态
                _logger.info("Airwallex Webhook: %s for refund %s (tx %s status unchanged)", 
                            event_type, resource.get('id'), tx.reference)
            else:
                _logger.warning("Airwallex Webhook: Unhandled event type: %s (tx: %s)", 
                              event_type, tx.reference)
        
        except Exception as e:
            _logger.exception("Airwallex Webhook: Error processing event %s for tx %s: %s", 
                            event_type, tx.reference if tx else 'unknown', str(e))
            return {'error': 'Processing error'}
        
        return {'status': 'ok'}