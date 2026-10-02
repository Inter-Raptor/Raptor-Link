const {chromium}=require('/opt/codex/runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('fs'),assert=require('assert');
(async()=>{
const server=require('child_process').spawn('python',['RaptorLink/app.py','--demo','--headless']);
process.on('exit',()=>server.kill());let browser;
try{
 await new Promise((resolve,reject)=>{server.stdout.once('data',resolve);server.once('exit',()=>reject(Error('server quit')));});
 const instance=JSON.parse(fs.readFileSync('RaptorLink/demo-data/instance.json')),base=`http://127.0.0.1:${instance.port}`,headers={'X-Aurora-Token':instance.token};
 browser=await chromium.launch({executablePath:process.cwd()+'/raptor-build/chromium-root/usr/lib/chromium/chromium-headless-shell',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1360,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
 await page.request.post(base+'/api/config',{headers,data:{settings:{tutorial:'never'},targets:[]}});
 await page.goto(base+'/#'+instance.token);await page.waitForSelector('#alarm-add',{state:'attached'});
 await page.click('#empty-add');await page.fill('#target-ip','192.168.1.175');await page.fill('#target-count','257');await page.fill('#target-name','Bureau');await page.fill('#route-end','257');await page.selectOption('#zone-source','rainbow');
 await page.click('#alarm-add');await page.fill('#alarm-name','Matin');await page.fill('#alarm-time','09:00');await page.click('#alarm-add');await page.fill('#alarm-name','Pause');await page.fill('#alarm-time','15:00');await page.click('#alarm-enabled');
 await page.locator('.zone-details summary').click();await page.fill('#zone-smooth','1.2');await page.click('#save');
 let cfg=await (await page.request.get(base+'/api/config',{headers})).json();assert.equal(cfg.targets[0].alarms.length,2);assert.equal(cfg.targets[0].alarms[1].time,'15:00');assert.equal(cfg.targets[0].alarms[1].enabled,false);assert.equal(cfg.targets[0].routes[0].smooth,1.2);
 await page.click('[data-page="plan"]');await page.selectOption('#plan-shape','desk');await page.click('#plan-apply');await page.check('#plan-manual');
 for(const [i,n] of [5,100,152].entries())await page.locator(`[data-segment="${i}"]`).fill(String(n));await page.locator('#plan-total').click();
 assert((await page.locator('#plan-total').innerText()).includes('257 / 257'));await page.click('#save');await page.click('#toggle');await page.waitForTimeout(600);
 assert.equal(await page.locator('[data-plan-led]').count(),257);assert((await page.locator('[data-plan-led="256"]').getAttribute('fill')).startsWith('rgb('));
 await page.screenshot({path:'raptor-build/034-plan-leds.png',fullPage:true});
 await page.click('#plan-source-edit');await page.selectOption('#zone-source','screen');await page.selectOption('#screen-mode','custom');await page.check('#screen-list input[value="demo2"]');await page.click('#save');await page.waitForTimeout(100);
 cfg=await (await page.request.get(base+'/api/config',{headers})).json();assert.deepEqual(cfg.targets[0].routes[0].screen_ids,['demo1','demo2']);assert.deepEqual(cfg.targets[0].routes[0].path_counts,[5,100,152]);
 await page.click('[data-page="plan"]');assert((await page.locator('#plan-heading').innerText()).includes('écran'));assert.equal(await page.locator('.screen-surface').count(),2);
 await page.selectOption('#plan-tool','screens');const box=await page.locator('[data-screen="demo2"]').boundingBox();await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2-80,box.y+box.height/2+80,{steps:5});await page.mouse.up();
 assert((await page.evaluate(()=>planRoute().screen_layout.demo2[1]))>0);await page.click('#save');await page.waitForTimeout(100);cfg=await (await page.request.get(base+'/api/config',{headers})).json();assert(cfg.targets[0].routes[0].screen_layout.demo2[1]>0);
 await page.selectOption('#plan-tool','led');await page.screenshot({path:'raptor-build/034-ecrans.png',fullPage:true});
 await page.reload();await page.waitForSelector('#plan-manual',{state:'attached'});await page.click('[data-page="plan"]');assert.equal(await page.locator('[data-plan-led]').count(),257);assert.equal(await page.locator('.screen-surface').count(),2);assert((await page.evaluate(()=>planRoute().screen_layout.demo2[1]))>0);await page.click('#plan-windows-layout');assert.deepEqual(await page.evaluate(()=>planRoute().screen_layout),{});await page.click('#save');
 await page.click('#plan-source-edit');assert.equal(await page.locator('#alarm-select option').count(),2);assert.equal(await page.locator('#zone-smooth').inputValue(),'1.2');
 await page.selectOption('#alarm-select',await page.locator('#alarm-select option').nth(1).getAttribute('value'));await page.click('#alarm-delete');assert.equal(await page.locator('#alarm-select option').count(),1);
 await page.setViewportSize({width:1000,height:760});await page.click('[data-page="plan"]');await page.screenshot({path:'raptor-build/034-compact.png',fullPage:true});
 assert.deepEqual(errors,[]);console.log('PASS: multiple alarms, persistence, fade, segment counts, 257 live LEDs, multi-screen selection, source explanations, deletion, no UI errors');
}finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exit(1)});
