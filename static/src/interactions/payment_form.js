/** @odoo-module **/

import { _t } from '@web/core/l10n/translation';
import { PaymentForm } from '@payment/interactions/payment_form';
import { patch } from '@web/core/utils/patch';

patch(PaymentForm.prototype, {

    setup() {
        super.setup();
        this.airwallexLoaded = false;
        this.airwallexCardElement = null;
    },

    // #=== DOM 準備階段 ===#

    /**
     * 當用戶在前端選擇 Airwallex 支付選項時觸發
     * @override
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'airwallex') {
            return super._prepareInlineForm(...arguments);
        }

        // 強制設置為直連流，確保 Odoo 不會嘗試跳轉到外部支付頁
        this._setPaymentFlow('direct');

        try {
            // 1. 等待並獲取正確的 SDK 全域變數
            const sdk = await this._getAirwallexSDK();

            // 2. 初始化 SDK (只執行一次)
            if (!this.airwallexLoaded) {
                const env = this.paymentContext.providerState === 'enabled' ? 'prod' : 'demo';
                await sdk.init({
                    env: env,
                    enabledElements: ['payments'],
                });
                this.airwallexLoaded = true;
            }

            // 3. 建立並掛載 Card Element (注意 await)
            const container = document.getElementById('airwallex-card-element');
            if (container && !this.airwallexCardElement) {
                this.airwallexCardElement = await sdk.createElement('card', {
                    style: {
                        base: {
                            fontSize: '16px',
                            color: '#32325d',
                            '::placeholder': { color: '#aab7c4' },
                        },
                    },
                });

                if (!this.airwallexCardElement) {
                    throw new Error(_t("Failed to create Airwallex Card Element."));
                }

                // 掛載到 XML 中定義的 ID 容器
                this.airwallexCardElement.mount('airwallex-card-element');

                // 監聽 SDK 內部錯誤 (如卡號無效)
                container.addEventListener('onError', (event) => {
                    const { error } = event.detail;
                    this._displayErrorDialog(_t("Card Error"), error.message);
                    this._enableButton(); // 恢復支付按鈕
                });
            }
        } catch (err) {
            this._displayErrorDialog(_t("Technical Error"), err.message);
            this._enableButton();
        }
    },

    // #=== 支付執行階段 ===#

    /**
     * 當用戶點擊 Odoo 的「立即支付」按鈕時觸發
     * @override
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'airwallex') {
            return super._processDirectFlow(...arguments);
        }

        // 從後端傳回的 processingValues 中提取必要密鑰
        const intentId = processingValues['intent_id'] || processingValues['airwallex_intent_id'];
        const clientSecret = processingValues['client_secret'] || processingValues['airwallex_client_secret'];
        const autoCapture = processingValues['airwallex_auto_capture'] !== undefined ? processingValues['airwallex_auto_capture'] : true;

        if (!intentId || !clientSecret || !this.airwallexCardElement) {
            this._displayErrorDialog(_t("Error"), _t("Payment element not initialized correctly."));
            this._enableButton();
            return;
        }

        try {
            // 調用 Airwallex 確認支付 (這會自動處理 3DS 驗證彈窗)
            const result = await this.airwallexCardElement.confirm({
                intent_id: intentId,
                client_secret: clientSecret,
                payment_method_options: {
                    card: { auto_capture: autoCapture }
                }
            });

            // 使用 Odoo 標準方式處理跳轉
            this._handleAirwallexResult(result);

        } catch (err) {
            this._displayErrorDialog(_t("Technical Error"), err.message);
            this._enableButton();
        }
    },

    // #=== 私有輔助方法 ===#

    /**
     * 處理支付結果並執行 Odoo 跳轉
     * @private
     */
    _handleAirwallexResult(result) {
        // 定義 Airwallex 視為成功的狀態碼
        const successStatuses = ['SUCCEEDED', 'CAPTURED', 'AUTHORIZED', 'REQUIRES_CAPTURE', 'PENDING'];

        if (result && successStatuses.includes(result.status)) {
            // ✅ 關鍵：跳轉到 Odoo 的標準狀態頁面，觸發後端訂單確認
            window.location = '/payment/status';
        } else if (result && result.error) {
            this._displayErrorDialog(_t("Payment Failed"), result.error.message);
            this._enableButton();
        } else {
            // 其他未知情況，恢復按鈕讓用戶重試
            this._enableButton();
        }
    },

    /**
     * 獲取 Airwallex SDK，處理變數名稱與載入時序
     */
    async _getAirwallexSDK(timeout = 5000) {
        const start = Date.now();
        return new Promise((resolve, reject) => {
            const check = () => {
                const sdk = window.AirwallexComponentsSDK || window.Airwallex;
                if (sdk && typeof sdk.init === 'function') {
                    resolve(sdk);
                } else if (Date.now() - start > timeout) {
                    reject(new Error(_t("Airwallex SDK loading timeout.")));
                } else {
                    setTimeout(check, 100);
                }
            };
            check();
        });
    },
});