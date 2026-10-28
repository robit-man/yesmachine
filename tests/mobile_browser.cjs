const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
let redact='';
(async()=>{
 let input='';for await(const chunk of process.stdin)input+=chunk;
 const config=JSON.parse(input);redact=config.key;
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 try {
 const page=await browser.newPage({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(config.url+(config.fragment?'#key='+encodeURIComponent(config.key):''),{waitUntil:'domcontentloaded'});
 if(!config.fragment){await page.getByLabel('Access key',{exact:true}).fill(config.key);await page.getByRole('button',{name:'Connect',exact:true}).click();}
 try{await page.waitForFunction(()=>window.yesmachineRemote?.ready(),null,{timeout:35000});}
 catch(error){console.error('Mobile viewer status:',await page.locator('#status').textContent());console.error('Browser errors:',JSON.stringify(errors));throw error;}
 assert.equal(await page.evaluate(()=>location.hash),'');
 assert.equal(await page.locator('#screen-state').isVisible(),false);
 const frame=await page.evaluate(()=>{const c=display.flatten(),p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;const colors=new Set();for(let i=0;i<p.length;i+=400)colors.add(`${p[i]},${p[i+1]},${p[i+2]},${p[i+3]}`);return {w:c.width,h:c.height,colors:colors.size};});
 assert.ok(frame.w>=320&&frame.h>=240&&frame.colors>16,'A real, nonblank desktop must render');
 assert.equal(await page.evaluate(()=>window.yesmachineRemote.view().mode),'viewport');
 fs.mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/phone-desktop-live.png'});
 await page.getByRole('button',{name:'Open session menu',exact:true}).click();
 await page.getByRole('button',{name:'Fit screen',exact:true}).click();
 assert.equal(await page.locator('#session-menu').isVisible(),false);
 const cdp=await page.context().newCDPSession(page);
 const send=(type,points)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:points});
 const before=await page.evaluate(()=>window.yesmachineRemote.view());
 await send('touchStart',[{x:150,y:400,id:1}]);await send('touchMove',[{x:190,y:430,id:1}]);await send('touchEnd',[]);
 const moved=await page.evaluate(()=>window.yesmachineRemote.view());assert.ok(moved.pointerX>before.pointerX,'Dragging moves the remote mouse');
 await send('touchStart',[{x:140,y:400,id:1},{x:240,y:400,id:2}]);await send('touchMove',[{x:100,y:400,id:1},{x:280,y:400,id:2}]);await send('touchEnd',[]);
 const pinched=await page.evaluate(()=>window.yesmachineRemote.view());assert.ok(pinched.scale>moved.scale*1.5,'Pinch zoom enlarges the desktop');
 await page.setViewportSize({width:844,height:390});await page.waitForTimeout(200);
 assert.equal(await page.evaluate(()=>window.yesmachineRemote.view().mode),'fit');
 await page.getByRole('button',{name:'Open session menu',exact:true}).click();
 await page.screenshot({path:'artifacts/phone-menu-landscape.png'});
 const box=await page.locator('#session-menu').boundingBox();assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=844&&box.y+box.height<=390,'Menu stays in viewport');
 await page.getByRole('button',{name:'Reconnect',exact:true}).click();
 await page.waitForFunction(()=>window.yesmachineRemote.ready(),null,{timeout:35000});
 assert.deepEqual(errors,[]);
 fs.writeFileSync('artifacts/mobile-results.json',JSON.stringify({passed:true,frame,checks:['public endpoint','fragment key removed','real desktop pixels','portrait viewport','hidden controls','touch mouse drag','pinch zoom','landscape fit','menu bounds','reconnect'],pageErrors:errors},null,2));
 console.log('Phone browser passed: real desktop, touch drag, pinch zoom, rotation, menu and reconnect.');
 } finally {await browser.close();}
})().catch(error=>{console.error(String(error.stack||error).replaceAll(redact,'[redacted]'));process.exit(1);});
