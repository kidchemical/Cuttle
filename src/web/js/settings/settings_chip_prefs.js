/**
 * CuttleSettingsChipPrefs — Settings → Appearance → Chat chips.
 *
 * Renders one toggle per chip from CuttleUiBoot.chipPrefs() (owner of keys
 * and labels) into a container, using the page's toggle-switch markup. A
 * change writes through CuttleUiBoot.setChipPref and calls host.notify() so
 * the shell re-applies it in every open frame.
 */
(function (global) {
    'use strict';

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
            var toggle = document.createElement('label');
            toggle.className = 'toggle-switch' + (pref.enabled ? ' active' : '');
            toggle.id = 'chipPref-' + pref.id;
            toggle.setAttribute('role', 'switch');
            toggle.setAttribute('aria-label', pref.label);
            toggle.setAttribute('aria-checked', pref.enabled ? 'true' : 'false');
            toggle.tabIndex = 0;
            var slider = document.createElement('span');
            slider.className = 'slider';
            toggle.appendChild(slider);
            function flip(event) {
                if (event) event.preventDefault();
                var on = !toggle.classList.contains('active');
                boot.setChipPref(pref.id, on);
                toggle.classList.toggle('active', on);
                toggle.setAttribute('aria-checked', on ? 'true' : 'false');
                notify();
                toast(pref.label + (on ? ' shown' : ' hidden'));
            }
            toggle.addEventListener('click', flip);
            toggle.addEventListener('keydown', function (e) {
                if (e.key === ' ' || e.key === 'Enter') flip(e);
            });
            control.appendChild(toggle);
            item.appendChild(label);
            item.appendChild(control);
            return item;
        }

        function render() {
            if (!container || !boot || typeof boot.chipPrefs !== 'function') return;
            container.textContent = '';
            boot.chipPrefs().forEach(function (pref) { container.appendChild(row(pref)); });
        }

        return { render: render };
    }

    global.CuttleSettingsChipPrefs = { create: create };
})(window);
