# -*- coding: utf-8 -*-

import requests
import logging
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    # 1. 擴充 code 選項，加入 airwallex
    code = fields.Selection(
        selection_add=[('airwallex', 'Airwallex')],
        ondelete={'airwallex': 'set default'}
    )

    # 2. 定義憑證欄位 (Credentials)
    airwallex_client_id = fields.Char(
        string="Client ID",
        groups="base.group_system"
    )
    airwallex_api_key = fields.Char(
        string="API Key",
        groups="base.group_system",
        password=True
    )

    # --- Odoo 框架整合方法 ---

    def _get_payment_method_codes(self):
        """ 告訴 Odoo 這個 Provider 支援哪些付款代碼 """
        res = super()._get_payment_method_codes()
        if self.code == 'airwallex':
            # 這裡回傳基礎清單，Sync 按鈕會根據 API 實際開啟狀況來關聯
            return ['card', 'alipay', 'wechat_pay', 'unionpay', 'apple_pay', 'google_pay']
        return res

    def _get_supported_currencies(self):
        """ 返回此 Provider 支援的幣別 """
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'airwallex':
            # 返回所有啟用中的幣別，確保不會因為幣別不匹配而被隱藏
            return self.env['res.currency'].search([('active', '=', True)])
        return supported_currencies

    # --- Airwallex API 工具與動作 ---

    def _airwallex_get_token(self):
        """ 向 Airwallex 請求 Access Token """
        self.ensure_one()
        if not self.airwallex_client_id or not self.airwallex_api_key:
            return False

        api_domain = 'api-demo.airwallex.com' if self.state == 'test' else 'api.airwallex.com'
        url = f'https://{api_domain}/api/v1/authentication/login'
        
        headers = {
            'x-client-id': self.airwallex_client_id,
            'x-api-key': self.airwallex_api_key,
        }
        
        try:
            response = requests.post(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json().get('token')
        except Exception as e:
            _logger.error("Airwallex 認證失敗: %s", e)
            return False

    def action_airwallex_sync_methods(self):
        """ 
        這是視圖中按鈕呼叫的函式：從 Airwallex 同步支付方式並關聯至 Odoo
        """
        self.ensure_one()
        token = self._airwallex_get_token()
        
        if not token:
            return self._show_notification(
                _('Authentication Error'), 
                _('無法連接 Airwallex。請檢查 Client ID 與 API Key 是否正確。'), 
                'danger'
            )

        api_domain = 'api-demo.airwallex.com' if self.state == 'test' else 'api.airwallex.com'
        url = f'https://{api_domain}/api/v1/pa/config/payment_method_types'
        headers = {'Authorization': f'Bearer {token}'}
        
        try:
            response = requests.get(url, headers=headers, params={'active': 'true'}, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # 取得 Airwallex 端的代碼
            remote_codes = [item.get('name') for item in data.get('items', [])]
            
            # 轉換為 Odoo 識別的代碼
            mapping = {
                'card': 'card',
                'alipaycn': 'alipay',
                'wechatpay': 'wechat_pay',
                'unionpay': 'unionpay',
                'applepay': 'apple_pay',
                'googlepay': 'google_pay',
            }
            odoo_codes = [mapping.get(code, code) for code in remote_codes]

            # 搜尋並更新關聯
            methods = self.env['payment.method'].search([('code', 'in', odoo_codes)])
            self.write({'payment_method_ids': [(6, 0, methods.ids)]})

            return self._show_notification(
                _('同步成功'), 
                _('已成功從 Airwallex 同步 %s 種支付方式。') % len(methods), 
                'success'
            )

        except Exception as e:
            _logger.error("Airwallex 同步錯誤: %s", e)
            return self._show_notification(_('Sync Error'), str(e), 'danger')

    def _show_notification(self, title, message, sticky_type):
        """ 輔助：顯示頂部通知 """
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'sticky': False,
                'type': sticky_type,
            }
        }