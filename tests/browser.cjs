// Optional browser smoke: NODE_PATH=/path/to/node_modules node tests/browser.cjs
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.TEST_URL || 'http://localhost:8000');
    await page.getByRole('heading', {name:'Make room for what matters.'}).waitFor();
    await page.screenshot({path:'.local/welcome.png',fullPage:true});
    await page.getByRole('button', {name:'Explore the local demo'}).click();
    await page.locator('#demo-banner').waitFor({state:'visible'});
    for (const reply of ['Asia/Jerusalem','9-17; commute is short','7-22; family time','Guitar, exercise, and relationships','Keep evenings pleasant']) {
      await page.locator('#input').fill(reply);
      await page.getByRole('button', {name:'Send message'}).click();
      await page.waitForFunction(() => !document.getElementById('send').disabled && document.getElementById('input').value === '');
    }
    await page.locator('#input').fill('Call brother tomorrow\nAsk about the sample');
    await page.getByRole('button', {name:'Send message'}).click();
    await page.locator('#proposal').waitFor({state:'visible'});
    await page.getByRole('button', {name:'Find a better fit'}).click();
    await page.locator('#input').fill('הוא בעבודה עד 16:00');
    await page.getByRole('button', {name:'Send message'}).click();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.getElementById('input').value === '');
    const corrected = await page.evaluate(async () => (await fetch('/api/state')).json());
    assert.ok(Number(new Intl.DateTimeFormat('en-GB', {hour:'2-digit', timeZone:'Asia/Jerusalem', hourCycle:'h23'}).format(new Date(corrected.proposal.start))) >= 16);
    await page.screenshot({path:'.local/proposal.png',fullPage:true});
    const before = await page.evaluate(async () => (await fetch('/api/state')).json());
    await page.getByRole('button', {name:'Approve in demo'}).click();
    await page.locator('#proposal').waitFor({state:'hidden'});
    await page.getByRole('button', {name:'Close the day'}).click();
    await page.getByRole('heading', {name:'A gentle close to the day'}).waitFor();
    // Tomorrow is intentionally excluded from today's review. Exercise its move via the same API.
    await page.evaluate(async () => {
      const state = await (await fetch('/api/state')).json();
      const response = await fetch('/api/reschedule', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':state.csrf}, body:JSON.stringify({id:state.achievements[0].id})});
      if (!response.ok) throw new Error('Rescheduling failed');
    });
    await page.reload();
    await page.locator('#proposal').waitFor({state:'visible'});
    assert.match(await page.locator('#proposal').innerText(), /SAME ACHIEVEMENT/);
    const moving = await page.evaluate(async () => (await fetch('/api/state')).json());
    assert.equal(moving.proposal.event_id, before.proposal.event_id);
    assert.equal(moving.proposal.achievement.note, 'Ask about the sample');
    await page.getByRole('button', {name:'Approve in demo'}).click();
    await page.locator('#proposal').waitFor({state:'hidden'});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'.local/mobile.png',fullPage:true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    assert.deepEqual(errors,[]);
    console.log('Browser smoke passed: onboarding, Hebrew correction, approval, daily close, same-event rescheduling, notes, mobile layout; no JS errors.');
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exit(1);});
