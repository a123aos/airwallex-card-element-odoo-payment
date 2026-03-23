# -*- coding: utf-8 -*-
import logging
import uuid
from datetime import datetime, timedelta
import requests
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

try:
    from dateutil import parser as dtparser
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False
    _logger.warning("dateutil not available, using fallback datetime parsing")


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('airwallex', 'Airwallex')],
        ondelete={'airwallex': 'set default'},
    )

    airwallex_client_id = fields.Char(string="Client ID", groups='base.group_system')
    airwallex_api_key = fields.Char(string="API Key", groups='base.group_system')
    airwallex_webhook_secret = fields.Char(string="Webhook Secret", groups='base.group_system')

    airwallex_access_token = fields.Char(copy=False, groups='base.group_system')
    airwallex_token_expiry = fields.Datetime(copy=False, groups='base.group_system')

    @api.constrains('code', 'airwallex_client_id', 'airwallex_api_key', 'state')
    def _check_airwallex_credential_fields(self):
        for provider in self:
            if provider.code == 'airwallex' and provider.state == 'enabled':
                if not provider.airwallex_client_id:
                    raise ValidationError(_("Airwallex Client ID is required when enabled."))
                if not provider.airwallex_api_key:
                    raise ValidationError(_("Airwallex API Key is required when enabled."))

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != 'airwallex':
            return super()._get_default_payment_method_codes()
        return ['card', 'alipay', 'wechatpay']

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'airwallex').update({
            'support_manual_capture': 'partial',
            'support_refund': 'partial',
            'support_tokenization': True,
        })

    def _airwallex_get_access_token(self):
        self.ensure_one()
        now = fields.Datetime.now()
        if self.airwallex_access_token and self.airwallex_token_expiry and now < self.airwallex_token_expiry:
            return self.airwallex_access_token

        _logger.info("Requesting new Airwallex access token for provider %s", self.id)

        base_url = 'https://api.airwallex.com' if self.state == 'enabled' else 'https://api-demo.airwallex.com'
        url = f"{base_url}/api/v1/authentication/login"

        headers = {
            'x-client-id': self.airwallex_client_id,
            'x-api-key': self.airwallex_api_key,
            'Content-Type': 'application/json',
        }

        try:
            response = requests.post(url, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()

            token = result.get('token')
            expires_at_str = result.get('expires_at')

            if not token:
                raise ValidationError(_("No access token received from Airwallex"))

            if expires_at_str:
                if HAS_DATEUTIL:
                    token_expiry = dtparser.parse(expires_at_str).replace(tzinfo=None)
                else:
                    token_expiry = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00')).replace(tzinfo=None)
            else:
                token_expiry = now + timedelta(minutes=30)

            self.sudo().write({
                'airwallex_access_token': token,
                'airwallex_token_expiry': token_expiry - timedelta(minutes=5),
            })
            return token

        except requests.exceptions.RequestException as e:
            _logger.error("Airwallex token request failed: %s", str(e))
            raise ValidationError(_("Failed to get Airwallex access token: %s") % str(e))

    def _make_airwallex_request(self, endpoint, payload=None, method='POST'):
        self.ensure_one()
        token = self._airwallex_get_access_token()

        base_url = 'https://api.airwallex.com/api/v1' if self.state == 'enabled' else 'https://api-demo.airwallex.com/api/v1'
        url = f"{base_url}{endpoint}"

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
        }

        try:
            response = requests.request(method, url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.exceptions.RequestException as e:
            _logger.error("Airwallex API request failed: %s", str(e))
            raise ValidationError(_("Airwallex API request failed: %s") % str(e))

    def _get_processing_values(self, processing_values):
        res = super()._get_processing_values(processing_values)
        if self.code != 'airwallex':
            return res

        order_id = processing_values.get('order_id')
        if not order_id:
            raise ValidationError(_("Order ID is required for Airwallex payment"))

        order = self.env['sale.order'].sudo().browse(order_id)
        if not order.exists():
            raise ValidationError(_("Order not found"))

        amount = order.currency_id.round(order.amount_total)
        currency_code = order.currency_id.name

        payload = {
            'request_id': str(uuid.uuid4()),
            'amount': amount,
            'currency': currency_code,
            'merchant_order_id': order.name,
        }

        payment_method_id = processing_values.get('payment_method_id')
        if payment_method_id:
            payment_method = self.env['payment.method'].sudo().browse(payment_method_id)
            if payment_method.code in ['alipay', 'wechatpay']:
                payload['return_url'] = order.get_portal_url()
        else:
            redirect_required = any(m.code in ['alipay', 'wechatpay'] for m in self.payment_method_ids)
            if redirect_required:
                payload['return_url'] = order.get_portal_url()

        try:
            payment_intent = self._make_airwallex_request('/pa/payment_intents/create', payload=payload)
        except Exception as e:
            _logger.error("Airwallex PaymentIntent creation failed for order %s: %s", order.name, str(e))
            raise

        res.update({
            'airwallex_intent_id': payment_intent.get('id'),
            'airwallex_client_secret': payment_intent.get('client_secret'),
            'airwallex_env': 'prod' if self.state == 'enabled' else 'demo',
            'airwallex_currency': currency_code,
        })
        return res

    def action_airwallex_sync_methods(self):
        self.ensure_one()
        if not self.airwallex_client_id or not self.airwallex_api_key:
            raise ValidationError(_("Please fill in Client ID and API Key before syncing."))

        _logger.info("Syncing Airwallex payment methods for provider %s", self.id)

        try:
            response = self._make_airwallex_request(
                '/pa/config/payment_method_types?active=true&transaction_mode=oneoff',
                method='GET'
            )
            remote_methods = response.get('items', [])
            remote_names = [m.get('name') for m in remote_methods if m.get('active')]

            mapping = {
                'card': 'card',
                'alipaycn': 'alipay',
                'wechatpay': 'wechatpay',
            }
            odoo_codes = [mapping.get(name) for name in remote_names if name in mapping]

            methods = self.env['payment.method'].search([('code', 'in', odoo_codes)])
            self.payment_method_ids = [(6, 0, methods.ids)]

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Success"),
                    'message': _("Synced %s payment methods.") % len(odoo_codes),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            _logger.exception("Sync failed")
            raise ValidationError(_("Sync failed: %s") % str(e))