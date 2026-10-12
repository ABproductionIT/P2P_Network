const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
(async () => {
  const server = spawn(process.env.PYTHON || 'python3', ['signaling_server.py','--host','127.0.0.1','--port','19090'], {cwd:require('node:path').join(__dirname,'..')});
  let browser;
  try {
    for(let i=0;i<50;i++) { try { if((await fetch('http://127.0.0.1:19090/health')).ok) break; } catch {} await new Promise(r=>setTimeout(r,100)); }
    browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    const errors=[];
    const pages=[];
    for(const name of ['Alice','Bob','Carol']) {
      const page=await browser.newPage(); pages.push(page);
      page.on('pageerror',e=>errors.push(e.message));
      await page.goto('http://127.0.0.1:19090');
      // Local host-candidate test must not depend on internet STUN.
      await page.evaluate(() => { const Native=window.RTCPeerConnection; window.RTCPeerConnection=class extends Native {constructor(config){super({...config,iceServers:[]});}}; });
      await page.fill('#name',name); await page.fill('#room','browser-test-room'); await page.click('#join');
    }
    for(const page of pages) await page.waitForFunction(()=>document.querySelectorAll('#peers li').length===2 && [...document.querySelectorAll('#peers li')].every(li=>li.textContent.includes('чат подключён')),null,{timeout:20000});
    await pages[0].fill('#text','Hello from Alice <script>alert(1)</script>'); await pages[0].click('#send');
    for(const page of pages) await page.waitForFunction(()=>document.querySelector('#messages').textContent.includes('Hello from Alice <script>alert(1)</script>'));
    assert.equal(await pages[1].locator('#messages script').count(),0);
    await pages[1].fill('#text','Привет от Bob'); await pages[1].click('#send');
    await pages[0].waitForFunction(()=>document.querySelector('#messages').textContent.includes('Привет от Bob'));
    await pages[2].click('#leave');
    await pages[0].waitForFunction(()=>document.querySelector('#count').textContent==='2');
    // Closing signaling recovers room membership and channels.
    const oldId = await pages[1].evaluate(()=>selfId);
    await pages[1].evaluate(()=>socket.close());
    await pages[1].waitForFunction(id => selfId && selfId !== id, oldId, {timeout:15000});
    await pages[1].waitForFunction(()=>document.querySelector('#status').textContent.includes('В комнате') && !document.querySelector('#send').disabled,null,{timeout:15000});
    await pages[1].fill('#text','After reconnect'); await pages[1].click('#send');
    await pages[0].waitForFunction(()=>document.querySelector('#messages').textContent.includes('After reconnect'));
    await pages[0].setViewportSize({width:390,height:844});
    assert.ok(await pages[0].evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);
    console.log('PASS: 3-browser mesh, bidirectional messages, safe text rendering, leave, signaling reconnect, mobile layout');
  } finally { await browser?.close(); server.kill('SIGTERM'); }
})().catch(e=>{console.error(e);process.exitCode=1;});
