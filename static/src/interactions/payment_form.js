/** @odoo-module **/

import { _t } from '@web/core/l10n/translation';
import { registry } from '@web/core/registry';
import { PaymentForm } from '@payment/interactions/payment_form';

/**
 * Airwallex 專用的支付表單互動邏輯
 * 繼承自 Odoo 19 原生 PaymentForm Interaction
 */
export class AirwallexPaymentForm extends PaymentForm {
    
    setup() {
        super.setup();
        this.airwallexLoaded = false;
        this.cardElement = null;
    }

    // #=== OVERRIDES ===#

    /**
     * 準備 Inline Form：初始化 SDK 並掛載 Iframe
     * @override
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'airwallex') {
            return super._prepareInlineForm(...arguments);
        }

        // 強制設定為 Direct 模式，確保觸發 _processDirectFlow
        this._setPaymentFlow('direct');

        // 1. 確保 Airwallex SDK 已載入
        if (!this.airwallexLoaded) {
            await this._loadAirwallexSDK();
            
            // 初始化 SDK
            const env = this.paymentContext.providerState === 'enabled' ? 'prod' : 'demo';
            Airwallex.init({
                env: env,
                enabledElements: ['payments'],
            });
            this.airwallexLoaded = true;
        }

        // 2. 建立並掛載 Card Element
        if (!this.cardElement) {
            this.cardElement = Airwallex.createElement('card', {
                style: {
                    base: {
                        fontSize: '16px',
                        color: '#32325d',
                        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                        '::placeholder': { color: '#aab7c4' },
                    },
                    invalid: {
                        color: '#fa755a',
                        iconColor: '#fa755a',
                    },
                },
            });
            
            // 掛載至 div#airwallex-card-element
            this.cardElement.mount('airwallex-card-element');
        }
    }

    /**
     * 執行 Direct 支付確認
     * 當用戶點擊「立即付款」且 Odoo 成功建立後端 Transaction 後觸發
     * @override
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'airwallex') {
            return super._processDirectFlow(...arguments);
        }

        // --- 開始處理支付 UI ---
        this._disableButton(); // 禁用按鈕（Odoo 內建方法）
        const payButton = document.querySelector('button[name="o_payment_submit_button"]');
        if (payButton) {
            // 將按鈕改為轉圈圈狀態
            payButton.innerHTML = '<i class="fa fa-refresh fa-spin me-2"></i>' + _t("Processing...");
        }

        try {
            const intentId = processingValues['airwallex_intent_id'];
            const clientSecret = processingValues['airwallex_client_secret'];

            if (!intentId || !clientSecret) {
                throw new Error(_t("Missing Airwallex configuration (Intent ID or Client Secret)."));
            }

            // 呼叫 Card Element 的 confirm 方法
            const result = await this.cardElement.confirm({
                intent_id: intentId,
                client_secret: clientSecret,
            });

            // 檢查結果狀態
            if (result && (result.status === 'SUCCEEDED' || result.status === 'CAPTURED')) {
                // 成功：導向至結果頁面
                window.location = this.paymentContext['landingRoute'] || '/payment/status';
            } else if (result && result.error) {
                // 支付被拒絕或發生錯誤
                this._displayErrorDialog(_t("Payment Failed"), result.error.message);
                this._resetPayButton(payButton);
            } else {
                // 其他非預期狀態 (如使用者取消)
                console.warn("Airwallex status:", result?.status);
                this._resetPayButton(payButton);
            }
        } catch (err) {
            // 技術性錯誤（如網路斷線、SDK 崩潰）
            this._displayErrorDialog(_t("Technical Error"), err.message);
            this._resetPayButton(payButton);
        }
    }

    // #=== HELPERS ===#

    /**
     * 輔助方法：將按鈕恢復為原始狀態
     */
    _resetPayButton(btn) {
        if (btn) {
            // 恢復原本的按鈕內容 (請根據你 XML 裡的文字設定，通常是 Pay Now)
            btn.innerHTML = _t("Pay Now");
        }
        this._enableButton(); // 重新啟用按鈕（Odoo 內建方法）
    }

    /**
     * 動態載入 Airwallex 最新官方 Bundle
     */
    _loadAirwallexSDK() {
        return new Promise((resolve, reject) => {
            if (window.Airwallex) return resolve();
            const script = document.createElement('script');
            script.src = "https://checkout.airwallex.com/assets/elements.bundle.min.js";
            script.async = true;
            script.onload = resolve;
            script.onerror = () => reject(new Error(_t("Failed to load Airwallex SDK.")));
            document.head.appendChild(script);
        });
    }
}

/**
 * 使用 force: true 強制覆蓋原生的 PaymentForm Interaction
 */
registry.category('public.interactions').add('payment.payment_form', AirwallexPaymentForm, { force: true });