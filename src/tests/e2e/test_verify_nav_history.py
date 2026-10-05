"""
Headless verification: history open -> right-side circles scrolled to bottom,
standby auto-scroll, pause on scroll-up / hover / focus-within, multi-frame FPS,
screenshot.
"""
import os, sys, time, threading, http.server, socketserver
from pathlib import Path

src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

def _static_available(port):
    import requests
    try:
        r = requests.get(f"http://127.0.0.1:{port}/chat_page.html", timeout=2)
        return r.ok and "Cuttle" in r.text
    except Exception:
        return False

def get_base():
    import requests
    for base in ["http://127.0.0.1:8080", "http://127.0.0.1:58315"]:
        try:
            r = requests.get(f"{base}/chat_page.html", timeout=2)
            if r.ok:
                return base
        except Exception:
            pass
    # ephemeral
    web_dir = src_root / "web"
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(web_dir), **k)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler, bind_and_activate=False)
    httpd.allow_reuse_address = True
    httpd.server_bind()
    httpd.server_activate()
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    for _ in range(20):
        if _static_available(port):
            break
        time.sleep(0.2)
    return f"http://127.0.0.1:{port}"

def run():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP no playwright")
        return 0
    base = get_base()
    print(f"BASE {base}")
    chat_url = f"{base}/chat_page.html"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width":1280,"height":800})
        page = ctx.new_page()
        page.goto(chat_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector("#chatInput, #welcomeChatInput", timeout=10000)
        page.wait_for_timeout(1000)
        # setup: clear, create 2 history chats each with many messages so nav is scrollable
        page.evaluate("""() => {
            localStorage.clear();
            sessionStorage.clear();
            const now = Date.now();
            const mk = (n, prefix) => Array.from({length:n}, (_,i)=>({
                role: i%2?'user':'assistant',
                content: (prefix||'') + `Message ${i+1} ` + 'x'.repeat(120),
                timestamp: now + i*60000,
                slash_command: prefix || null
            }));
            const sessions = {};
            sessions['hist_a'] = {id:'hist_a', title:'History A - long', updated: now-100000, messages: mk(45, '/muse ')};
            sessions['hist_b'] = {id:'hist_b', title:'History B - long', updated: now-50000, messages: mk(38, '/cursor ')};
            localStorage.setItem('chatSessions', JSON.stringify(sessions));
            // also set starred to cursor so new chat has badge
            localStorage.setItem('cuttleStarredSlashCommands', JSON.stringify(['/cursor ']));
            localStorage.setItem('cuttleChatSessionPrefs', JSON.stringify({
                'hist_a': {stickyChips:[{prefix:'/muse ', label:'Muse Code', category:'command'}]},
                'hist_b': {stickyChips:[{prefix:'/cursor ', label:'Cursor Agent', category:'command'}]}
            }));
            // mock muse/cursor models to avoid fetch churn
            window._musePrefMap = {'hist_a':'muse-spark-1.2-contributor','hist_b':'muse-spark-1.2'};
            const origFetch = window.fetch;
            window.fetch = (url, opts)=>{
                const u=String(url||'');
                if(u.includes('/api/muse/models')){
                    const s=new URL(u, location.href).searchParams.get('session')||'';
                    const pref = window._musePrefMap[s]||'muse-spark-1.2';
                    return Promise.resolve({ok:true, json:()=>Promise.resolve({success:true, preferredModel:pref, models:[
                        {id:'muse-spark-1.2', label:'Muse Spark 1.2', current: pref==='muse-spark-1.2'},
                        {id:'muse-spark-1.2-contributor', label:'Muse Spark 1.2 (Contributor)', current: pref==='muse-spark-1.2-contributor'}
                    ]})});
                }
                if(u.includes('/api/cursor-agent/models')){
                    return Promise.resolve({ok:true, json:()=>Promise.resolve({success:true, preferredModel:'auto', models:[{id:'auto',label:'Auto'}]})});
                }
                return origFetch(url, opts);
            };
        }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#welcomeChatInput", timeout=10000)
        page.wait_for_timeout(1500)
        # Open history via public UI: click history panel button then click hist_a item
        # First ensure page has rendered history items: need to trigger refresh?
        # Use direct JS to ensure history panel is populated, then click via UI
        page.evaluate("""() => {
            // Force history render if available
            if (window.refreshChatHistoryList) window.refreshChatHistoryList();
        }""")
        page.wait_for_timeout(1000)
        # Click the history panel button (corner button)
        hist_btn = page.locator("#chatHistoryPanelButton")
        if hist_btn.is_visible():
            hist_btn.click()
            page.wait_for_timeout(800)
        # Now locate history item for hist_a and click it via public UI
        # History items have .chat-history-item with data-session-id
        item_a = page.locator(".chat-history-item[data-session-id='hist_a']").first
        # If not found, try generic
        if item_a.count()==0:
            # fallback: any history item
            item_a = page.locator(".chat-history-item").first
        item_a.wait_for(state="visible", timeout=5000)
        item_a.click()
        page.wait_for_timeout(1800)
        # Verify via JS that we are on hist_a and nav is at bottom
        info = page.evaluate("""() => {
            const nav=document.getElementById('messageNav');
            const box=document.getElementById('chatMessages');
            const chip = document.getElementById('chatSlashChipsRow')?.innerText || '';
            return {
                current: window.currentSessionId || localStorage.getItem('lastChatSessionId'),
                navCount: nav ? nav.querySelectorAll('.message-nav-item').length : -1,
                navTop: nav ? nav.scrollTop : -1,
                navHeight: nav ? nav.scrollHeight : -1,
                navClient: nav ? nav.clientHeight : -1,
                isAtBottom: nav ? Math.abs(nav.scrollHeight - nav.scrollTop - nav.clientHeight) < 12 : false,
                chatTop: box ? box.scrollTop : -1,
                chatAtBottom: box ? Math.abs(box.scrollHeight - box.scrollTop - box.clientHeight) < 40 : false,
                chip,
                navDisplay: nav ? getComputedStyle(nav).display : 'none'
            };
        }""")
        print(f"[open via history] {info}")
        assert info["current"]=="hist_a", f"should be on hist_a, got {info['current']}"
        assert info["isAtBottom"], f"nav should default to bottom on open, got top={info['navTop']} height={info['navHeight']} client={info['navClient']}"
        print("PASS default bottom")
        # Standby auto-scroll: while at bottom, add a message -> should stay at bottom
        page.evaluate("""() => { window._e2eChat.addMessageToUI('Standby auto-scroll check','user',{timestamp: Date.now()}); }""")
        page.wait_for_timeout(800)
        after_standby = page.evaluate("""() => {
            const nav=document.getElementById('messageNav');
            return {top: nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<15};
        }""")
        print(f"[standby] {after_standby}")
        assert after_standby["isAtBottom"], "standby should auto-scroll to bottom"
        print("PASS standby")
        # Scroll-up pause
        page.evaluate("""() => { const nav=document.getElementById('messageNav'); nav.scrollTop=0; nav.dispatchEvent(new Event('scroll')); }""")
        page.wait_for_timeout(500)
        page.evaluate("""() => { window._e2eChat.addMessageToUI('Should NOT auto-scroll while scrolled up','user',{timestamp: Date.now()}); }""")
        page.wait_for_timeout(700)
        after_up = page.evaluate("""() => {
            const nav=document.getElementById('messageNav');
            return {top: nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<15};
        }""")
        print(f"[scroll-up pause] {after_up}")
        assert not after_up["isAtBottom"] and after_up["top"]<=35, f"should stay at top, got {after_up}"
        print("PASS scroll-up pause")
        # Hover pause: scroll to top, hover nav, add while hovering -> stay
        page.evaluate("""() => { const nav=document.getElementById('messageNav'); nav.scrollTop=0; }""")
        page.wait_for_timeout(200)
        page.hover("#messageNav")
        page.wait_for_timeout(400)
        # while hovering, ensure focus not stolen
        active_before = page.evaluate("""() => document.activeElement ? document.activeElement.id || document.activeElement.tagName : 'none'""")
        page.evaluate("""() => { window._e2eChat.addMessageToUI('Hover pause check','user',{timestamp: Date.now()}); }""")
        page.wait_for_timeout(700)
        after_hover = page.evaluate("""() => {
            const nav=document.getElementById('messageNav');
            return {top: nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<15, active: document.activeElement ? document.activeElement.id||document.activeElement.tagName : 'none'};
        }""")
        print(f"[hover pause] before={active_before} after={after_hover}")
        assert not after_hover["isAtBottom"], "hover should pause auto-scroll"
        # focus-within pause: focus a nav item
        page.evaluate("""() => { const nav=document.getElementById('messageNav'); nav.scrollTop=0; }""")
        page.wait_for_timeout(300)
        first_nav_btn = page.locator("#messageNav .message-nav-item").first
        first_nav_btn.focus()
        page.wait_for_timeout(300)
        focused = page.evaluate("""() => document.activeElement ? document.activeElement.outerHTML.slice(0,300) : 'none'""")
        print(f"focused nav item: {focused[:200]}")
        page.evaluate("""() => { window._e2eChat.addMessageToUI('Focus-within pause check','user',{timestamp: Date.now()}); }""")
        page.wait_for_timeout(700)
        after_focus = page.evaluate("""() => {
            const nav=document.getElementById('messageNav');
            return {top: nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<15, active: document.activeElement ? document.activeElement.className : 'none'};
        }""")
        print(f"[focus-within pause] {after_focus}")
        assert not after_focus["isAtBottom"], "focus-within should pause"
        print("PASS focus pause")
        # Resume: scroll to bottom, clear hover/focus, add -> should auto-scroll
        page.evaluate("""() => { document.activeElement && document.activeElement.blur(); }""")
        page.mouse.move(10,10)
        page.wait_for_timeout(300)
        page.evaluate("""() => { const nav=document.getElementById('messageNav'); nav.scrollTop=nav.scrollHeight; nav.dispatchEvent(new Event('scroll')); }""")
        page.wait_for_timeout(400)
        page.evaluate("""() => { window._e2eChat.addMessageToUI('Resume after interaction','assistant',{timestamp: Date.now()}); }""")
        page.wait_for_timeout(800)
        after_resume = page.evaluate("""() => {
            const nav=document.getElementById('messageNav');
            return {top: nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<15};
        }""")
        print(f"[resume] {after_resume}")
        assert after_resume["isAtBottom"], "should resume auto-scroll on standby"
        print("PASS resume")
        # Multi-frame FPS
        fps = page.evaluate("""() => new Promise(res=>{
            let frames=0; const s=performance.now();
            function tick(){ frames++; if(performance.now()-s<1000) requestAnimationFrame(tick); else res({frames, fps:frames, dur: performance.now()-s}); }
            requestAnimationFrame(tick);
        })""")
        print(f"FPS {fps}")
        assert fps["fps"] >= 30, f"low fps {fps}"
        # Screenshot
        preview = src_root.parent / "temp" / "verify_nav_history.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(preview))
        print("screenshot saved")
        browser.close()
        return 0

if __name__ == "__main__":
    sys.exit(run())
