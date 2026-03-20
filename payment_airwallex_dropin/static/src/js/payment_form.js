/** @odoo-module **/

import paymentForm from 'payment.payment_form';

paymentForm.include({

    // 1. 當使用者選擇付款方式時，準備 Airwallex Inline Form
    _prepareInlineForm: function (providerId, providerCode, paymentOptionId, paymentOptionType) {
        if (providerCode !== 'airwallex') {
            return this._super(...arguments);
        }

        // 這裡我們會向 Odoo 後端請求 Airwallex 的會話資訊 (Intent)
        // 暫時先寫下邏輯框架，等後端 Transaction 模型完成後對接
        return this._rpc({
            route: '/payment/airwallex/get_intent',
            params: {
                'provider_id': providerId,
            },
        }).then(data => {
            this._airwallexMountDropin(data.client_secret, data.intent_id);
        });
    },

    // 2. 初始化 Airwallex SDK 並掛載元件
    _airwallexMountDropin: function (clientSecret, intentId) {
        // 檢查 SDK 是否已載入（需在 template 引入 sdk.js）
        if (typeof Airwallex === 'undefined') {
            console.error('Airwallex SDK 未載入');
            return;
        }

        Airwallex.init({
            env: 'demo', // 測試環境使用 'demo'，正式環境改為 'prod'
            origin: window.location.origin,
        });

        const element = Airwallex.createElement('dropIn', {
            intent_id: intentId,
            client_secret: clientSecret,
        });

        element.mount('#airwallex-dropin-element');

        element.on('ready', () => {
            document.getElementById('airwallex-loading').classList.add('d-none');
        });

        element.on('error', (event) => {
            console.error('Airwallex 錯誤:', event.detail.error);
        });
    },
});