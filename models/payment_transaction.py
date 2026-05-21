# -*- coding: utf-8 -*-
import logging
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    airwallex_client_secret = fields.Char(string="Airwallex Client Secret", groups='base.group_system')

    # === 商業邏輯 - 預處理 ===

    def _get_specific_processing_values(self, processing_values):
        """ 覆寫以回傳 Airwallex 特有的處理值 """
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != 'airwallex' or self.operation == 'online_token':
            return res

        if not self.provider_reference or not self.airwallex_client_secret:
            intent_data = self.provider_id._airwallex_create_intent(self)
            self.sudo().write({
                'provider_reference': intent_data.get('intent_id'),
                'airwallex_client_secret': intent_data.get('client_secret'),
            })

        res.update({
            'client_secret': self.airwallex_client_secret,
            'intent_id': self.provider_reference,
            'currency': self.currency_id.name,
            'amount': self.amount,
            'airwallex_auto_capture': not self.provider_id.capture_manually,
        })
        return res

    # === 商業邏輯 - 執行動作 ===

    def _send_capture_request(self):
        """ 發送請款請求 """
        if self.provider_code != 'airwallex':
            return super()._send_capture_request()

        source_tx = self.source_transaction_id
        remote_intent = self.provider_id._airwallex_make_request(
            f'pa/payment_intents/{source_tx.provider_reference}', method='GET'
        )
        if remote_intent.get('status') == 'SUCCEEDED':
            self._set_done()
            return

        request_id = f'capture_{self.reference}_{int(fields.Datetime.now().timestamp())}'
        result = self.provider_id._airwallex_make_request(
            f'pa/payment_intents/{source_tx.provider_reference}/capture',
            payload={'request_id': request_id, 'amount': self.amount},
            method='POST'
        )
        self.provider_reference = result.get('id')
        self._process('airwallex', {'airwallex_obj': result})

    def _send_void_request(self):
        """ 發送取消請求 """
        if self.provider_code != 'airwallex':
            return super()._send_void_request()

        source_tx = self.source_transaction_id
        request_id = f'void_{self.reference}_{int(fields.Datetime.now().timestamp())}'
        result = self.provider_id._airwallex_make_request(
            f'pa/payment_intents/{source_tx.provider_reference}/cancel',
            payload={'request_id': request_id},
            method='POST'
        )
        self._process('airwallex', {'airwallex_obj': result})

    def _send_refund_request(self):
        """ 發送退款請求 """
        if self.provider_code != 'airwallex':
            return super()._send_refund_request()

        source_tx = self.source_transaction_id
        request_id = f'refund_{self.reference}_{int(fields.Datetime.now().timestamp())}'
        result = self.provider_id._airwallex_make_request(
            'pa/refunds/create',
            payload={
                'request_id': request_id,
                'payment_intent_id': source_tx.provider_reference,
                'amount': abs(self.amount),
            },
            method='POST'
        )
        self.provider_reference = result.get('id')
        self._process('airwallex', {'airwallex_obj': result})

    # === 商業邏輯 - 後處理 ===

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        if provider_code != 'airwallex':
            return super()._search_by_reference(provider_code, payment_data)

        air_obj = payment_data.get('airwallex_obj', {})
        provider_ref = air_obj.get('id')
        if provider_ref:
            tx = self.search([('provider_reference', '=', provider_ref), ('provider_code', '=', 'airwallex')], limit=1)
            if tx:
                return tx

        reference = payment_data.get('reference') or air_obj.get('merchant_order_id')
        if reference:
            tx = self.search([('reference', '=', reference), ('provider_code', '=', 'airwallex')], limit=1)
            return tx
        
        return self.env['payment.transaction']

    def _extract_amount_data(self, payment_data):
        if self.provider_code != 'airwallex':
            return super()._extract_amount_data(payment_data)

        air_obj = payment_data.get('airwallex_obj', {})
        raw_amount = air_obj.get('amount')
        amount = abs(float(raw_amount)) if raw_amount is not None else self.amount
        return {
            'amount': amount,
            'currency_code': air_obj.get('currency', self.currency_id.name).upper(),
        }

    def _apply_updates(self, payment_data):
        """ 映射狀態，並加入官方標準的 Cron 觸發 """
        if self.provider_code != 'airwallex':
            return super()._apply_updates(payment_data)

        air_obj = payment_data.get('airwallex_obj', {})
        status = air_obj.get('status', '').upper()

        # 1. 退款交易 (Refund) 狀態映射邏輯
        if self.operation == 'refund':
            if status == 'SETTLED':
                self._set_done()
            elif status in ['RECEIVED', 'ACCEPTED']:
                if self.state not in ['done']:
                    self._set_pending()
            elif status == 'FAILED':
                msg = air_obj.get('failure_details', {}).get('message')
                self._set_error(msg or _("Airwallex 退款失敗。"))
        
        # 2. 一般支付 (PaymentIntent) 狀態映射邏輯
        else:
            if status == 'SUCCEEDED':
                if self.state != 'done':
                    self._set_done()
            elif status in ['PENDING', 'PENDING_REVIEW', 'REQUIRES_PAYMENT_METHOD', 'REQUIRES_CUSTOMER_ACTION']:
                if self.state not in ['done', 'authorized']:
                    self._set_pending()
            elif status == 'REQUIRES_CAPTURE':
                if self.state != 'authorized':
                    self._set_authorized()
            elif status == 'CANCELLED':
                self._set_canceled()
            elif status in ['FAILED', 'EXPIRED']:
                msg = air_obj.get('latest_payment_attempt', {}).get('failure_details', {}).get('message')
                self._set_error(msg or _("Airwallex 交易失敗或已過期。"))

        # 3. 官方標準：退款成功後立即觸發 Cron 核銷發票
        if self.operation == 'refund' and self.state == 'done':
            _logger.info("Airwallex: 退款成功，觸發後續處理 Cron。")
            self.env.ref('payment.cron_post_process_payment_tx')._trigger()