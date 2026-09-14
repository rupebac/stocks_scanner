const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const code = fs.readFileSync('dashboard/components/browser_ideas/storage.js','utf8');
function browser(storage = new Map(), blocked = false) {
  const listeners = {}, messages = [];
  const parent = {postMessage: m => messages.push(m)};
  let seq = 0;
  vm.runInNewContext(code, {parent, navigator:{}, crypto:{randomUUID:()=>String(++seq)},
    addEventListener:(type, fn)=>listeners[type]=fn,
    localStorage:{getItem:k=>storage.get(k)||null, setItem:(k,v)=>{if(blocked) throw Error('Quota exceeded'); storage.set(k,v)}}});
  return {storage,messages,async render(command) {
    listeners.message({source:parent,data:{type:'streamlit:render',args:{command}}});
    await new Promise(resolve=>setImmediate(resolve));
    return messages.filter(m=>m.type==='streamlit:setComponentValue').at(-1).value;
  }};
}
(async () => {
  const alice=browser(), bob=browser();
  assert.equal(Object.keys((await alice.render()).ideas).length,0);
  const idea={ticker:'ORCL',buy_price:100,thesis:'Private idea'};
  await alice.render({id:'save1',kind:'save',idea});
  assert.equal((await browser(alice.storage).render()).ideas.ORCL.thesis,'Private idea');
  assert.equal(Object.keys((await bob.render()).ideas).length,0);
  const tab2=browser(alice.storage);
  await tab2.render({id:'save2',kind:'save',idea:{ticker:'ACN',buy_price:150,thesis:'Second'}});
  await alice.render({id:'delete1',kind:'remove',ticker:'ORCL'});
  const result=await browser(alice.storage).render();
  assert.equal(result.ideas.ORCL,undefined);
  assert.equal(result.ideas.ACN.thesis,'Second');
  const denied=browser(new Map(),true);
  assert.equal((await denied.render({id:'x',kind:'save',idea})).ready,false);
  const corrupt=browser(new Map([['hunt.personal-ideas.v1','broken']]));
  assert.equal((await corrupt.render()).ready,false);
  assert.equal(corrupt.storage.get('hunt.personal-ideas.v1'),'broken');
  console.log('Browser storage: persistence, separate profiles, cross-tab merge, deletion, blocked storage and corrupt-data checks passed.');
})();
