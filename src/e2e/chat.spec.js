const { test, expect } = require('@playwright/test');
test('history open and nav scroll', async ({ page }) => {
  await page.goto('http://172.30.174.62:58315/chat_page.html', {waitUntil: 'domcontentloaded'});
  await page.waitForTimeout(2000);
  await page.evaluate(()=>{ localStorage.setItem('cuttleStarredSlashCommands', JSON.stringify(['/cursor '])); localStorage.setItem('cuttleChatSessionPrefs', JSON.stringify({})); });
  await page.reload({waitUntil: 'domcontentloaded'});
  await page.waitForTimeout(2000);
  const result = await page.evaluate(async ()=>{
    const makeMsgs = (n)=>{ const arr=[]; for(let i=1;i<=n;i++){ arr.push({role: i%2?'user':'assistant', content: 'Message '+i+' content here. Lorem ipsum dolor sit amet consectetur adipiscing elit. This is a long message to make scrolling needed. '+ 'x'.repeat(80), timestamp: Date.now()+i*60000, slash_command: null}); } return arr; };
    const msgs = makeMsgs(40);
    const sessions={};
    sessions['test_long_chat_1']={id:'test_long_chat_1', title:'Test Long Chat', updated: Date.now(), messages: msgs};
    localStorage.setItem('chatSessions', JSON.stringify(sessions));
    localStorage.setItem('lastChatSessionId','test_long_chat_1');
    if(window.loadChatSession){ await window.loadChatSession('test_long_chat_1'); return 'called';}
    return 'no';
  });
  console.log("inject", result);
  await page.waitForTimeout(1500);
  const nav_info = await page.evaluate(()=>{
    const nav=document.getElementById('messageNav');
    const box=document.getElementById('chatMessages');
    return {navCount: nav.querySelectorAll('.message-nav-item').length, navScrollTop: nav.scrollTop, navScrollHeight: nav.scrollHeight, navClientHeight: nav.clientHeight, navIsAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<10, chatScrollTop: box.scrollTop, chatScrollHeight: box.scrollHeight, chatClientHeight: box.clientHeight};
  });
  console.log("NAV after open", JSON.stringify(nav_info));
  expect(nav_info.navIsAtBottom).toBeTruthy();
  // scroll nav to top, add message, expect no auto-scroll
  await page.evaluate(()=>{ document.getElementById('messageNav').scrollTop=0; });
  await page.waitForTimeout(400);
  await page.evaluate(()=>{ window.addMessageToUI('New message after scroll-up test','user',{timestamp:Date.now()}); });
  await page.waitForTimeout(600);
  const afterUp = await page.evaluate(()=>{ const nav=document.getElementById('messageNav'); return {top:nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<10}; });
  console.log("after scroll-up add", afterUp);
  expect(afterUp.isAtBottom).toBeFalsy();
  // hover pause check
  await page.evaluate(()=>{ document.getElementById('messageNav').scrollTop=0; });
  await page.hover('#messageNav');
  await page.waitForTimeout(300);
  await page.evaluate(()=>{ window.addMessageToUI('Hover test','user',{timestamp:Date.now()}); });
  await page.waitForTimeout(600);
  const afterHover = await page.evaluate(()=>{ const nav=document.getElementById('messageNav'); return {top:nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<10}; });
  console.log("after hover add", afterHover);
  expect(afterHover.isAtBottom).toBeFalsy();
  // scroll to bottom, add, expect at bottom
  await page.evaluate(()=>{ const nav=document.getElementById('messageNav'); nav.scrollTop=nav.scrollHeight; nav.dispatchEvent(new Event('scroll')); });
  await page.waitForTimeout(400);
  await page.evaluate(()=>{ window.addMessageToUI('Bottom test','assistant',{timestamp:Date.now()}); });
  await page.waitForTimeout(800);
  const afterBottom = await page.evaluate(()=>{ const nav=document.getElementById('messageNav'); return {top:nav.scrollTop, isAtBottom: Math.abs(nav.scrollHeight-nav.scrollTop-nav.clientHeight)<30}; });
  console.log("after bottom add", afterBottom);
  expect(afterBottom.isAtBottom).toBeTruthy();
  await page.screenshot({path: 'C:/Users/MainUser/Desktop/e2e_final.png'});
  console.log("done");
});
