// Deterministic browser contract: partial paint is never reported as ready.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const instruction=(...parts)=>parts.map(x=>`${String(x).length}.${x}`).join(',')+';';
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try{
 const page=await browser.newPage({viewport:{width:800,height:600}});page.on('pageerror',e=>console.error('Page error:',e.message));
 await page.route('**/api/session',route=>route.fulfill({json:{expires:0,hostname:'Static desktop fixture'}}));
 let socket,mouse=[];
 await page.routeWebSocket('**/tunnel*',ws=>{
  socket=ws;ws.onMessage(data=>{if(String(data).startsWith('5.mouse,'))mouse.push(String(data));});
  ws.send(instruction('','00000000-0000-4000-8000-000000000001')+instruction('size',0,640,480)+instruction('rect',0,200,150,240,180)+instruction('cfill',14,0,30,120,200,255)+instruction('sync',1));
 });
 await page.goto(process.env.YESMACHINE_PHONE_TEST_URL,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>typeof window.yesmachineRemote==='object'&&connected);
 await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>yesmachineRemote.ready()),false,'A center tile alone must not reveal the desktop');
 assert.equal(await page.locator('#screen-state').isVisible(),true);
 assert.equal(mouse.length,0,'No mouse motion should be needed to get initial contents');
 socket.send(instruction('rect',0,0,0,640,480)+instruction('cfill',14,0,20,40,60,255)+instruction('rect',0,0,0,80,80)+instruction('cfill',14,0,240,30,60,255)+instruction('rect',0,560,400,80,80)+instruction('cfill',14,0,30,240,60,255)+instruction('size',-1,4,4)+instruction('rect',-1,0,0,4,4)+instruction('cfill',14,-1,255,255,255,255)+instruction('cursor',1,1,-1,0,0,4,4)+instruction('mouse',80,70,0,2)+instruction('sync',2));
 try{await page.waitForFunction(()=>yesmachineRemote.ready(),null,{timeout:5000});}catch(error){console.error(await page.evaluate(()=>({status:document.querySelector('#status').textContent,connected,painted,size:[display.getWidth(),display.getHeight()],pixels:[...display.getDefaultLayer().getCanvas().getContext('2d').getImageData(0,0,1,1).data],nativeCursor})));throw error;}
 assert.equal(mouse.length,0,'Full desktop arrives without remote input');
 assert.equal(await page.locator('#pointer-handle').count(),0,'The mock pointer bubble must be removed');
 const corners=await page.evaluate(()=>{const c=display.getDefaultLayer().getCanvas().getContext('2d');return [[...c.getImageData(0,0,1,1).data],[...c.getImageData(639,479,1,1).data]]});
 assert.deepEqual(corners,[[240,30,60,255],[30,240,60,255]]);
 assert.equal(await page.evaluate(()=>yesmachineRemote.view().pointerX),80);
 await page.mouse.move(240,220);await page.waitForTimeout(100);
 assert.ok(mouse.length>0,'Hover-only movement must reach the host');
 assert.ok(mouse.every(message=>message.endsWith(',1.0;')),'Hover must not press any mouse button');
 const cursor=await page.locator('#desktop').evaluate(node=>getComputedStyle(node).cursor);
 assert.ok(cursor.startsWith('url('),'Use the host-provided cursor bitmap as the native browser cursor');
 socket.send(instruction('mouse',400,300,0,3));
 await page.waitForFunction(()=>yesmachineRemote.view().pointerX===400);
 console.log('Initial-frame contract passed: complete static screen before input, native cursor, hover and host position updates.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
