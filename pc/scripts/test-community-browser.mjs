// Isolated UI contract test. Synthetic works never touch the public service.
import { chromium } from "playwright";
import assert from "node:assert/strict";
const base = "http://127.0.0.1:4177";
const browser = await chromium.launch({ headless:true, executablePath:process.env.PET_TEST_CHROMIUM || "/Applications/360Chrome.app/Contents/MacOS/360Chrome" });
try {
  const page=await browser.newPage({viewport:{width:1360,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/__community-test',route=>route.fulfill({contentType:'text/html',body:'<html lang="zh"><body style="margin:32px"><div id="root"></div></body></html>'}));
  await page.goto(base+'/__community-test');
  await page.evaluate(async()=>{
    const Refresh=(await import('/@react-refresh')).default;Refresh.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>t=>t;window.__vite_plugin_react_preamble_installed__=true;
    window.communityCalls=[];window.communityLiked=false;window.communityMine=[];window.communityComments=[];
    const work={id:'test-focus',name:'专注一刻',version:'1.1.0',author:'测试作者',summary:'给桌面一个安静的专注角落',category:'productivity',description:'短按 SW1 开始，退出遵循全局设置。',releaseNotes:'优化布局与计时。',license:'MIT',covers:[],requirements:{runtime:'p4-bounded-runtime-v4',pcRequired:false},updatedAt:1780000000,available:true,downloads:12,likes:4,commentCount:0,packageBytes:6000};
    window.__TAURI_INTERNALS__={invoke:async(command,args)=>{
      window.communityCalls.push([command,args]);if(command==='stock_watchlist_status')return {quotes:[]};
      if(command!=='community_request')throw Error('Unexpected command '+command);
      const input=args.input;await new Promise(r=>setTimeout(r,10));
      switch(input.action){
        case 'local':return {owned:{},downloaded:{}};
        case 'browse':return {items:input.query==='不存在'?[]:[work],nextOffset:null,total:1};
        case 'rankings':return {items:[{...work,rankValue:12}]};
        case 'detail':return {...work,liked:window.communityLiked};
        case 'comments':return {items:window.communityComments,nextOffset:null};
        case 'mine': {
          const items = window.communityMine.length ? window.communityMine :
            Array.from({length:31}, (_,i) => ({...work,id:'page-'+i,name:'作品 '+(i+1)+' · 测试',status:'approved'}));
          const offset = input.offset || 0;
          return {items:items.slice(offset,offset+30), nextOffset:offset+30 < items.length ? offset+30 : null};
        }
        case 'like':window.communityLiked=input.active;return {active:input.active,likes:5};
        case 'comment':window.communityComments.push({id:crypto.randomUUID(),name:input.name,text:input.text,at:1780000000});return {ok:true};
        case 'prepare':return {coverDataUrl:'data:image/png;base64,iVBORw0KGgo=',id:'test-creation',name:work.name,version:'1.1.0',description:work.summary,canPublish:true,hasLicense:true,contentHash:'b'.repeat(64),files:['component.json','LICENSE.txt'],packageBytes:6000,warning:'扫描不能替代人工检查。'};
        case 'publish':window.communityMine=[{...work,id:'test-creation',status:'pending',available:false}];return {...work,status:'pending'};
        case 'download':return {entry:{id:'test-focus',name:work.name,version:'1.1.0',isLocal:true,libraryPath:'/test/library'},work};
        default:throw Error('Unexpected action '+input.action);
      }
    }};
    const React=(await import('/node_modules/.vite/deps/react.js')).default;
    const dom=await import('/node_modules/.vite/deps/react-dom_client.js');
    const {default:Community}=await import('/src/component-center/CommunityCenter.jsx');
    const {BUILTIN_COMPONENT_CENTER}=await import('/src/fixtures.js');
    const css=document.createElement('link');css.rel='stylesheet';css.href='/src/styles.css';document.head.append(css);
    const items=BUILTIN_COMPONENT_CENTER.components.filter(x=>['stock-watchlist','wooden-fish'].includes(x.id));
    items.push({id:'test-creation',name:'我的创作',version:'1.0.0',isLocal:true,libraryPath:'/test/creation'});
    function Test(){const [local,setLocal]=React.useState(items);return React.createElement(Community,{localItems:local,inventory:{freshness:'live',items:[]},onOpenLocal:()=>{window.openedLocal=true;},onCreate:()=>{window.created=true;},onImported:entry=>setLocal(old=>[...old,entry])},React.createElement('div',null,'原有本地组件与按键配置保留'));}
    (dom.createRoot||dom.default.createRoot)(document.getElementById('root')).render(React.createElement(Test));
  });
  // Community browsing now lives in the embedded site, covered by test-community-web.mjs.
  await page.getByRole('tab', {name:'我的组件', exact:true}).waitFor();
  await page.getByText('原有本地组件与按键配置保留').waitFor();
  await page.getByRole('tab', {name:'我的创作', exact:true}).click();
  await page.getByRole('heading', {name:/作品 1 ·/}).first().waitFor();
  await page.getByRole('button', {name:'下一页'}).click();
  await page.getByRole('heading', {name:/作品 31 ·/}).waitFor();
  assert.equal(await page.getByRole('button', {name:'下一页'}).isDisabled(), true);
  await page.getByRole('button', {name:'上一页'}).click();
  await page.getByRole('heading', {name:/作品 1 ·/}).first().waitFor();
  await page.getByRole('button', {name:'发布作品'}).click();
  await page.getByLabel('选择组件').selectOption('/test/creation');
  await page.getByText('component.json', {exact:true}).waitFor({state:'attached'});
  await page.getByLabel('公开作者名', {exact:true}).fill('测试作者');
  await page.getByLabel(/本次更新说明/).fill('首次发布');
  await page.getByRole('button', {name:/提交审核/}).click();
  await page.getByRole('button', {name:'等待审核'}).waitFor();
  const calls = await page.evaluate(() => window.communityCalls.map(([,args]) => args.input?.action));
  assert.equal(calls.filter(a => a === 'publish').length, 1);
  assert.equal(calls.includes('download'), false);
  for (const width of [1440,1024,736,360]) {
    await page.setViewportSize({width,height:900});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow ${width}`);
  }
  console.log('PASS author pagination, local component selection and publication review');
} finally { await browser.close(); }
