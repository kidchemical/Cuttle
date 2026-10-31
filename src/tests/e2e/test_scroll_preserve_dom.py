"""CuttleScroll.setHtml in a real DOM: poll repaints keep the reader's place.

Browsers re-serialize markup, so an ``innerHTML !== html`` guard rebuilds on
every poll; the helper compares against the markup it last wrote instead.
"""
from .test_shared_diff_modal import browser, static_server  # noqa: F401

LIST = '''<div class="outer" style="height:120px;overflow:auto">
  <details data-key="a" open><summary>A</summary>
    <pre class="inner" style="height:60px;overflow:auto">{lines}</pre></details>
  <input data-key="filter" value="abc">
  {rows}
</div>'''


def _markup(extra=''):
    lines = '\n'.join(f'line {i}' for i in range(80))
    rows = ''.join(f'<p data-row="{i}">Row {i}</p>' for i in range(60))
    return LIST.format(lines=lines, rows=rows + extra)


def test_set_html_keeps_nested_scroll_details_and_focus(browser, static_server):
    page = browser.new_page()
    try:
        page.goto(static_server + '/jobs_page.html', wait_until='domcontentloaded')
        page.wait_for_function('() => window.CuttleScroll && CuttleScroll.setHtml')
        page.evaluate('''html => {
            const box = document.createElement('div');
            box.id = 'pollBox';
            document.body.appendChild(box);
            CuttleScroll.setHtml(box, html);
        }''', _markup())
        page.evaluate('''() => {
            const box = document.getElementById('pollBox');
            const input = box.querySelector('input');
            input.focus();
            input.setSelectionRange(1, 2);
            box.querySelector('.outer').scrollTop = 300;
            box.querySelector('.inner').scrollTop = 200;
            window.churn = 0;
            new MutationObserver(m => { window.churn += m.length; })
                .observe(box, { childList: true, subtree: true });
        }''')

        # Unchanged poll: no DOM writes at all.
        assert page.evaluate("html => CuttleScroll.setHtml(document.getElementById('pollBox'), html)",
                             _markup()) is False
        page.wait_for_timeout(50)
        assert page.evaluate('window.churn') == 0

        # Changed poll: content swaps, reader position survives.
        page.evaluate('''() => document.querySelector('#pollBox details').open = true''')
        assert page.evaluate("html => CuttleScroll.setHtml(document.getElementById('pollBox'), html)",
                             _markup('<p data-row="60">New</p>')) is True
        state = page.evaluate('''() => {
            const box = document.getElementById('pollBox');
            const input = box.querySelector('input');
            return {
                outer: box.querySelector('.outer').scrollTop,
                inner: box.querySelector('.inner').scrollTop,
                open: box.querySelector('details').open,
                focused: document.activeElement === input,
                sel: [input.selectionStart, input.selectionEnd],
                rows: box.querySelectorAll('[data-row]').length,
            };
        }''')
        assert state == {'outer': 300, 'inner': 200, 'open': True, 'focused': True,
                         'sel': [1, 2], 'rows': 61}

        # Someone else replaced the content: the same markup is written again.
        page.evaluate("() => { document.getElementById('pollBox').innerHTML = '<b>x</b>'; }")
        assert page.evaluate("html => CuttleScroll.setHtml(document.getElementById('pollBox'), html)",
                             _markup('<p data-row="60">New</p>')) is True
    finally:
        page.close()
