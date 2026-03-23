/** @odoo-module **/

import { PaymentForm } from "@payment/js/payment_form"; 
import { patch } from "@web/core/utils/patch";
import { loadJS } from "@web/core/assets";

paymentForm.include({
    _airwallexInitialized: false,
    _airwallexElement: null,

    async start() {
        // 检查是否有 Airwallex 支付方式，需要加载 SDK
        const airwallexProvider = this.paymentOptions.find(p => p.code === 'airwallex');
        if (airwallexProvider) {
            // 加载 Airwallex SDK（版本化 URL）
            await loadJS('https://static.airwallex.com/components/sdk/v1/index.js');
        }
        return this._super(...arguments);
    },

    async _prepareInlineForm(providerCode, providerId, paymentOptionId, flow) {
        if (providerCode !== 'airwallex') {
            return this._super(...arguments);
        }

        const provider = this.paymentOptions.find(p => p.id === providerId);
        const containerId = `airwallex-container-${providerId}`;
        const container = document.getElementById(containerId);

        if (!container) {
            console.error(`Airwallex container #${containerId} not found`);
            return Promise.resolve();
        }

        try {
            // ✅ 关键：从服务端渲染值获取 PaymentIntent 数据（通过 _get_processing_values）
            // 这些值在服务器端已通过 _get_processing_values 计算并传递
            const intentId = provider.airwallex_intent_id;
            const clientSecret = provider.airwallex_client_secret;
            const env = provider.airwallex_env || 'demo';
            const currency = provider.airwallex_currency || this._getTransactionCurrency();
            
            if (!intentId || !clientSecret) {
                throw new Error('PaymentIntent not initialized. Missing intent_id or client_secret from server.');
            }

            // 初始化 SDK（单例模式）
            if (!this._airwallexInitialized) {
                await window.Airwallex.init({
                    env: env,  // 使用服务端提供的环境（'prod' 或 'demo'）
                    enabledElements: ['payments'],
                });
                this._airwallexInitialized = true;
            }

            // 清理旧实例
            if (this._airwallexElement) {
                this._airwallexElement.destroy();
                this._airwallexElement = null;
            }
            container.innerHTML = '';

            this._airwallexElement = window.Airwallex.createElement('dropIn', {
    intent_id: intentId,
    client_secret: clientSecret,
    currency: currency,
    layout: {
        alwaysShowMethodLabel: true,  // 強制顯示 label & icon
    },
});

            // 挂载并绑定事件
            this._airwallexElement.mount(containerId);

            this._airwallexElement.on('ready', () => {
                console.log('Airwallex Drop-in: ready');
            });

            this._airwallexElement.on('success', (event) => {
                const { intent } = event.detail; // ✅ 正确：按文档结构
                console.log('Airwallex payment succeeded:', intent.id);
                // 提交表单，传递 PaymentIntent ID
                this._submitForm(providerId, paymentOptionId, flow, {
                    'airwallex_intent_id': intent.id,
                });
            });

            this._airwallexElement.on('error', (event) => {
                const { error, code, message } = event.detail || {};
                console.error('Airwallex error:', { error, code, message });
                this._displayError(message || 'Payment failed. Please try again.');
            });

            this._airwallexElement.on('cancel', () => {
                console.log('Airwallex payment cancelled by user');
            });

        } catch (err) {
            console.error('Airwallex setup failed:', err);
            this._displayError('Payment initialization failed. Please refresh the page.');
        }

        return Promise.resolve();
    },

    /**
     * 从交易上下文获取货币代码
     */
    _getTransactionCurrency() {
        // 场景1: 标准结账（有 this.order）
        if (this.order && this.order.currency_id) {
            const currency = this.order.currency_id;
            return Array.isArray(currency) ? currency[1] : currency.code || currency.name;
        }

        // 场景2: 门户支付（有 this.transaction）
        if (this.transaction && this.transaction.currency_id) {
            const currency = this.transaction.currency_id;
            return Array.isArray(currency) ? currency[1] : currency.code || currency.name;
        }

        // 场景3: Odoo 17+ paymentContext
        if (this.paymentContext && this.paymentContext.currency_id) {
            const currency = this.paymentContext.currency_id;
            return Array.isArray(currency) ? currency[1] : currency.code || currency.name;
        }

        // 场景4: 从隐藏字段获取（备用）
        if (this.$form) {
            const currencyInput = this.$form.find('input[name="currency"]');
            if (currencyInput.length) return currencyInput.val();
        }

        // 默认回退（应避免）
        console.warn('Airwallex: Could not determine currency, using USD fallback');
        return 'USD';
    },

    /**
     * 显示错误信息给用户
     */
    _displayError(message) {
        if (this._showPaymentError) {
            this._showPaymentError(message);
        } else {
            const container = document.getElementById('payment_form_error');
            if (container) {
                container.innerHTML = `<div class="alert alert-danger">${message}</div>`;
            } else {
                alert(message);
            }
        }
    },
});
