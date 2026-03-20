from odoo import fields, models

class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('airwallex', 'Airwallex')],
        ondelete={'airwallex': 'set default'}
    )

    # 只保留這兩個核心欄位
    airwallex_client_id = fields.Char(string="Client ID", groups="base.group_system")
    airwallex_api_key = fields.Char(string="API Key", groups="base.group_system", password=True)
    
    # Secret 欄位暫時註解掉，以後需要再打開
    # airwallex_client_secret = fields.Char(string="Client Secret", groups="base.group_system", password=True)
    # airwallex_webhook_secret = fields.Char(string="Webhook Secret", groups="base.group_system", password=True)