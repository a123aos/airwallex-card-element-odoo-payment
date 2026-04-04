/** @odoo-module **/

import { loadJS } from '@web/core/assets';
import { _t } from '@web/core/l10n/translation';
import { rpc } from '@web/core/network/rpc';
import { patch } from '@web/core/utils/patch';
import { PaymentForm } from '@payment/interactions/payment_form';

patch(PaymentForm.prototype, {

    setup() {
        super.setup(...arguments);
        this.airwallex_data = {
            initialized: false,
            card_element: null,
            sdk: null,
        };
    },

    /**
     * 預備支付表單：處理 Airwallex SDK 初始化與元件掛載
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'airwallex') {
            return super._prepareInlineForm(...arguments);
        }

        if (flow === 'token') return;
        this._setPaymentFlow('direct');

        const radio = document.querySelector(`input[name="o_payment_radio"][data-payment-option-id="${paymentOptionId}"]`);
        const inlineForm = this._getInlineForm(radio);
        const container = inlineForm.querySelector('[name="o_airwallex_container"]');
        const loader = inlineForm.querySelector('.airwallex-loading');
        const mountTarget = inlineForm.querySelector('#airwallex-card-element');

        if (container) container.classList.remove('d-none');
        if (loader) loader.style.display = 'block';

        try {
            // 1. 加載 SDK
            await loadJS('https://static.airwallex.com/components/sdk/v1/index.js');
            this.airwallex_data.sdk = window.AirwallexComponentsSDK;
            
            if (!this.airwallex_data.sdk) throw new Error(_t("無法載入 Airwallex 支付組件"));

            const config = JSON.parse(radio.dataset.airwallexConfig || '{}');

            // 2. 初始化 SDK
            if (!this.airwallex_data.initialized) {
                await this.airwallex_data.sdk.init({
                    env: config.env || 'demo',
                    enabledElements: ['payments'],
                });
                this.airwallex_data.initialized = true;
                console.log("Airwallex: SDK 初始化成功");
            }

            // 3. 建立信用卡元件 (恢復使用 await)
            if (this.airwallex_data.card_element) {
                this.airwallex_data.card_element.destroy();
            }

            this.airwallex_data.card_element = await this.airwallex_data.sdk.createElement('card', {
                style: { 
                    base: { 
                        fontSize: '16px',
                        color: '#495057',
                        fontFamily: '"Helvetica Neue", Helvetica, sans-serif',
                    } 
                }
            });

            // 4. 執行掛載
            if (mountTarget) {
                this.airwallex_data.card_element.mount(mountTarget);
                
                this.airwallex_data.card_element.on('ready', () => {
                    if (loader) loader.style.display = 'none';
                });

                const errorMsgContainer = inlineForm.querySelector('.airwallex-error-msg');
                this.airwallex_data.card_element.on('change', (event) => {
                    if (errorMsgContainer) {
                        errorMsgContainer.textContent = event.error ? event.error.message : '';
                        event.error ? errorMsgContainer.classList.remove('d-none') : errorMsgContainer.classList.add('d-none');
                    }
                });
            }

        } catch (error) {
            console.error("Airwallex 初始化錯誤:", error);
            if (loader) loader.style.display = 'none';
            this._displayErrorDialog(_t("初始化失敗"), error.message || error);
        }
    },

    /**
     * 處理支付提交
     */
    async processDirectPayment(providerCode, paymentOptionId, paymentMethodCode) {
        if (providerCode !== 'airwallex') {
            return super.processDirectPayment(...arguments);
        }

        this._disableButton();

        try {
            // 1. 向 Controller 請求 Intent 資料
            const result = await rpc('/payment/airwallex/create_intent', {
                'reference': this.paymentContext.reference,
            });

            if (result.error) {
                throw new Error(result.error);
            }

            console.log("Airwallex: 成功獲取 Intent，準備發起支付確認...");

            // 2. 恢復為直接對 card_element 實例調用 confirm()
            const confirmRes = await this.airwallex_data.card_element.confirm({
                intent_id: result.intent_id,
                client_secret: result.client_secret,
                payment_method: { 
                    billing_details: { 
                        name: this.paymentContext.partnerName || 'Customer' 
                    } 
                }
            });

            console.log("Airwallex 確認結果狀態:", confirmRes.status);

            // 3. 處理成功狀態 (包含需 Webhook 後續同步的 PENDING)
            const successStatuses = ['SUCCEEDED', 'REQUIRES_CAPTURE', 'PENDING', 'PENDING_REVIEW'];
            
            if (successStatuses.includes(confirmRes.status)) {
                window.location = '/payment/status';
            } else {
                throw new Error(confirmRes.message || _t("支付被拒絕，請檢查卡片資訊。"));
            }

        } catch (error) {
            console.error("Airwallex 支付失敗:", error);
            this._displayErrorDialog(_t("支付異常"), error.message || error);
            this._enableButton();
        }
    },
});