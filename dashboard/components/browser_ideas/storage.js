(() => {
  const KEY = 'hunt.personal-ideas.v1';
  let ready = false;
  let pending = Promise.resolve();
  const enqueue = command => { pending = pending.then(() => navigator.locks ? navigator.locks.request(KEY, () => report(command)) : report(command)); };
  const seen = new Set();
  const send = payload => parent.postMessage({isStreamlitMessage:true,type:'streamlit:setComponentValue',value:{...payload,event_id:crypto.randomUUID()}}, '*');
  const read = () => {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {version:1,ideas:{}};
    const doc = JSON.parse(raw);
    if (doc.version !== 1 || !doc.ideas || typeof doc.ideas !== 'object' || Array.isArray(doc.ideas)) throw Error('Saved ideas could not be read. Browser data has not been overwritten.');
    return doc;
  };
  function report(command) {
    try {
      let doc = read();
      if (command && !seen.has(command.id) && !(doc.applied || []).includes(command.id)) {
        if (command.kind === 'save') doc.ideas[command.idea.ticker] = command.idea;
        else if (command.kind === 'remove') delete doc.ideas[command.ticker];
        else throw Error('Unknown storage action');
        // Merge each action with current storage so other tabs' unrelated ideas survive.
        doc.applied = [...(Array.isArray(doc.applied) ? doc.applied : []), command.id].slice(-100);
        localStorage.setItem(KEY, JSON.stringify(doc));
        seen.add(command.id);
      }
      if (command) seen.add(command.id);
      send({ideas:doc.ideas,ready:true,ack:command?.id || null});
    } catch (error) {
      send({ready:false,error:String(error.message || error),ack:command?.id || null});
    }
  }
  addEventListener('message', event => {
    if (event.source !== parent || event.data?.type !== 'streamlit:render') return;
    const command = event.data.args?.command;
    if (!ready || (command && !seen.has(command.id))) { ready=true; enqueue(command); }
    parent.postMessage({isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:0}, '*');
  });
  addEventListener('storage', event => { if (event.key === KEY || event.key === null) enqueue(null); });
  parent.postMessage({isStreamlitMessage:true,type:'streamlit:componentReady',apiVersion:1}, '*');
})();
