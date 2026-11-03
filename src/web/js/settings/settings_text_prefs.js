/**
 * CuttleSettingsTextPrefs — Settings → Appearance → Text & fonts.
 *
 * Renders one select per preference from CuttleUiBoot.textPrefs() (the
 * single owner of keys, allowed values and labels) into a container. Every
 * select starts with "Use theme setting" (empty value = no override). A
 * change writes through CuttleUiBoot.setTextPref and then calls
 * host.notify() so the shell re-applies prefs in every open frame.
 */
(function (global) {
    'use strict';

    var THEME_DEFAULT_LABEL = 'Use theme setting';

    function create(host) {
        var boot = host.boot;
        var container = host.container;
        var notify = host.notify || function () {};
        var toast = host.toast || function () {};

        function row(pref) {
            var item = document.createElement('div');
            item.className = 'setting-item';
            var label = document.createElement('div');
            label.className = 'setting-label';
            var strong = document.createElement('strong');
            strong.textContent = pref.label;
            label.appendChild(strong);
            var control = document.createElement('div');
            control.className = 'setting-control';
            var select = document.createElement('select');
            select.className = 'setting-input';
            select.id = 'textPref-' + pref.id;
            select.dataset.textPref = pref.id;
            select.style.minWidth = '200px';
            var def = document.createElement('option');
            def.value = '';
            def.textContent = THEME_DEFAULT_LABEL;
            select.appendChild(def);
            pref.options.forEach(function (o) {
                var opt = document.createElement('option');
                opt.value = o.value;
                opt.textContent = o.label;
                select.appendChild(opt);
            });
            select.value = pref.value;
            select.addEventListener('change', function () {
                boot.setTextPref(pref.id, select.value);
                notify();
                toast(pref.label + ': ' + (select.value
                    ? select.options[select.selectedIndex].textContent
                    : THEME_DEFAULT_LABEL.toLowerCase()));
            });
            control.appendChild(select);
            item.appendChild(label);
            item.appendChild(control);
            return item;
        }

        function render() {
            if (!container || !boot || typeof boot.textPrefs !== 'function') return;
            container.textContent = '';
            boot.textPrefs().forEach(function (pref) { container.appendChild(row(pref)); });
        }

        function resetAll() {
            if (!boot || typeof boot.textPrefs !== 'function') return;
            boot.textPrefs().forEach(function (pref) { boot.setTextPref(pref.id, ''); });
            render();
            notify();
            toast('Text & fonts reset to theme settings');
        }

        return { render: render, resetAll: resetAll };
    }

    global.CuttleSettingsTextPrefs = { create: create, THEME_DEFAULT_LABEL: THEME_DEFAULT_LABEL };
})(window);
