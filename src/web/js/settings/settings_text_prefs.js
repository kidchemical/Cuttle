/**
 * CuttleSettingsTextPrefs — Settings → Appearance → Text & fonts.
 *
 * One row per chat area from CuttleUiBoot.textAreas() (the single owner of
 * keys, allowed values and labels), each with a Font and a Size select. Every
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

        function selectFor(area, pref, kind) {
            var select = document.createElement('select');
            select.className = 'setting-input';
            select.id = 'textPref-' + pref.id;
            select.dataset.textPref = pref.id;
            var name = area.label + ' ' + (kind === 'font' ? 'font' : 'size');
            select.setAttribute('aria-label', name);
            select.style.minWidth = kind === 'font' ? '170px' : '120px';
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
                toast(name + ': ' + (select.value
                    ? select.options[select.selectedIndex].textContent
                    : THEME_DEFAULT_LABEL.toLowerCase()));
            });
            return select;
        }

        function cell(caption, child) {
            var wrap = document.createElement('span');
            wrap.className = 'text-pref-cell';
            wrap.style.cssText = 'display:inline-flex;flex-direction:column;gap:2px;margin-left:8px;';
            var cap = document.createElement('span');
            cap.textContent = caption;
            cap.style.cssText = 'font-size:0.75em;opacity:0.7;';
            wrap.appendChild(cap);
            wrap.appendChild(child);
            return wrap;
        }

        function row(area) {
            var item = document.createElement('div');
            item.className = 'setting-item';
            item.dataset.textArea = area.area;
            var label = document.createElement('div');
            label.className = 'setting-label';
            var strong = document.createElement('strong');
            strong.textContent = area.label;
            label.appendChild(strong);
            if (area.hint) {
                var info = document.createElement('button');
                info.type = 'button';
                info.className = 'cuttle-info';
                info.dataset.tooltip = area.hint;
                info.setAttribute('aria-label', 'About ' + area.label);
                info.textContent = 'i';
                label.appendChild(info);
            }
            var control = document.createElement('div');
            control.className = 'setting-control';
            control.appendChild(cell('Font', selectFor(area, area.font, 'font')));
            if (area.size) control.appendChild(cell('Size', selectFor(area, area.size, 'size')));
            item.appendChild(label);
            item.appendChild(control);
            return item;
        }

        function render() {
            if (!container || !boot || typeof boot.textAreas !== 'function') return;
            container.textContent = '';
            boot.textAreas().forEach(function (area) { container.appendChild(row(area)); });
        }

        function resetAll() {
            if (!boot || typeof boot.textAreas !== 'function') return;
            boot.textAreas().forEach(function (area) {
                boot.setTextPref(area.font.id, '');
                if (area.size) boot.setTextPref(area.size.id, '');
            });
            render();
            notify();
            toast('Text & fonts reset to theme settings');
        }

        return { render: render, resetAll: resetAll };
    }

    global.CuttleSettingsTextPrefs = { create: create, THEME_DEFAULT_LABEL: THEME_DEFAULT_LABEL };
})(window);
