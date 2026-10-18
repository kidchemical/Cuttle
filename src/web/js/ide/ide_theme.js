/* Translate Cuttle's CSS palette into the concrete colors Monaco and xterm
 * accept. The workspace owns the glass surface; engines paint through it. */
(function (root) {
    'use strict';

    function palette() {
        const probe = document.createElement('span');
        probe.style.cssText = 'position:fixed;visibility:hidden;pointer-events:none';
        document.body.appendChild(probe);
        const ctx = document.createElement('canvas').getContext('2d');
        function color(value) {
            probe.style.color = value;
            ctx.clearRect(0, 0, 1, 1);
            ctx.fillStyle = getComputedStyle(probe).color;
            ctx.fillRect(0, 0, 1, 1);
            return '#' + Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3)
                .map((v) => v.toString(16).padStart(2, '0')).join('');
        }
        const p = {
            light: document.documentElement.classList.contains('light-mode'),
            text: color('var(--ide-text)'), muted: color('var(--ide-muted)'),
            accent: color('var(--ide-accent)'), border: color('var(--ide-border)'),
            surface: color('var(--bg-secondary)'),
        };
        probe.remove();
        return p;
    }

    function monacoTheme() {
        const p = palette();
        // Monaco blends minimap glyphs against the editor's RGB even when its
        // alpha is zero. Transparent black creates opaque black character cells
        // in light themes; retain the surface RGB beneath the transparent alpha.
        const clearSurface = p.surface + '00';
        return {
            base: p.light ? 'vs' : 'vs-dark', inherit: true,
            rules: [{ token: '', foreground: p.text.slice(1) },
                { token: 'comment', foreground: p.muted.slice(1) }],
            colors: {
                'editor.background': clearSurface, 'editor.foreground': p.text,
                'editorGutter.background': clearSurface, 'minimap.background': clearSurface,
                'editorOverviewRuler.background': '#00000000',
                'editorOverviewRuler.border': '#00000000', 'scrollbar.shadow': '#00000000',
                'editorCursor.foreground': p.accent, 'editorLineNumber.foreground': p.muted,
                'editorLineNumber.activeForeground': p.text,
                'editor.selectionBackground': p.accent + '40',
                'editor.inactiveSelectionBackground': p.accent + '24',
                'editor.lineHighlightBackground': p.accent + '0a',
                'editor.lineHighlightBorder': '#00000000',
                'editorIndentGuide.background1': p.muted + '28',
                'editorIndentGuide.activeBackground1': p.accent + '70',
                'editorStickyScroll.background': p.surface,
                'editorWidget.background': p.surface, 'editorWidget.foreground': p.text,
                'editorWidget.border': p.border,
                'editorSuggestWidget.background': p.surface, 'editorSuggestWidget.foreground': p.text,
                'editorSuggestWidget.border': p.border,
                'editorSuggestWidget.selectedBackground': p.accent + '30',
                'editorHoverWidget.background': p.surface, 'editorHoverWidget.foreground': p.text,
                'editorHoverWidget.border': p.border,
                'diffEditor.unchangedRegionBackground': '#00000000',
                'diffEditorGutter.insertedLineBackground': p.accent + '18',
                'scrollbarSlider.background': p.muted + '30',
                'scrollbarSlider.hoverBackground': p.muted + '50',
                focusBorder: p.accent,
            },
        };
    }

    function terminalTheme() {
        const p = palette();
        return { background: '#00000000', foreground: p.text, cursor: p.accent,
            selectionBackground: p.accent + '40' };
    }

    root.CuttleIdeTheme = { palette: palette, monacoTheme: monacoTheme, terminalTheme: terminalTheme };
})(globalThis);
