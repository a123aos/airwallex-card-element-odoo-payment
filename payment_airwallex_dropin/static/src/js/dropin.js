odoo.define('payment_airwallex_dropin.airwallex', function (require) {
    'use strict';

    var publicWidget = require('web.public.widget');

    publicWidget.registry.AirwallexDropin = publicWidget.Widget.extend({
        selector: '.o_airwallex_dropin_container',

        start: function () {
            this._super.apply(this, arguments);
            this._loadAirwallex();
        },

        _loadAirwallex: function () {
            var self = this;
            if (typeof Airwallex === 'undefined') {
                var script = document.createElement('script');
                script.src = 'https://checkout.airwallex.com/static/js/dropin.js';
                script.async = true;
                script.onload = function () { self._initDropin(); };
                document.head.appendChild(script);
            } else {
                self._initDropin();
            }
        },

        _initDropin: function () {
            // 之後會從後端傳 clientKey + PaymentIntent client_secret
            // 目前先用 placeholder 測試載入
            Airwallex.initDropin({
                env: 'demo',  // 改成 'prod' 上線
                origin: window.location.origin,
                // clientKey: '...',       // 從 provider 取
                // paymentIntent: { ... }  // 之後實作
            }).mount('#airwallex-dropin-element');
        }
    });

    return publicWidget.registry.AirwallexDropin;
});