/* Plotly supplies the chart; pointer-capture overlays constrain each cutoff to its axis. */
(() => {
  const plot = document.getElementById('plot');
  const wrap = document.getElementById('wrap');
  const handles = {quality: document.getElementById('quality-line'), valuation: document.getElementById('valuation-line')};
  const send = (type, data) => parent.postMessage({isStreamlitMessage:true, type, ...data}, '*');
  let args, quality, valuation, drag, frame, ready = false, applying = false, queuedArgs;
  let renderedFigure, suppressClickUntil = 0;
  const uid = () => `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const clamp = (kind, value) => kind === 'quality'
    ? Math.min(100, Math.max(0, Math.round(value)))
    : Number(Math.min(2, Math.max(-2, Math.round(value / .05)*.05)).toFixed(2));
  const shapeIndex = name => plot.layout.shapes.findIndex(s => s.name === name);
  const setValue = data => send('streamlit:setComponentValue', {dataType:'json', value:{...data,id:uid()}});

  function positionHandles() {
    if (!ready || !plot._fullLayout) return;
    const xa = plot._fullLayout.xaxis, ya = plot._fullLayout.yaxis;
    const x = xa._offset + xa.l2p(valuation), y = ya._offset + ya.l2p(quality);
    const h = handles.quality, v = handles.valuation;
    Object.assign(h.style, {left:`${xa._offset}px`, top:`${y-9}px`, width:`${xa._length}px`, display:y>=ya._offset && y<=ya._offset+ya._length?'block':'none'});
    Object.assign(v.style, {left:`${x-9}px`, top:`${ya._offset}px`, height:`${ya._length}px`, display:x>=xa._offset && x<=xa._offset+xa._length?'block':'none'});
    h.querySelector('.handle').textContent = `↕ Quality ${quality}%`;
    v.querySelector('.handle').textContent = `↔ ${valuation.toFixed(2)}`;
    h.setAttribute('aria-valuenow', quality); v.setAttribute('aria-valuenow', valuation);
    h.setAttribute('aria-valuetext', `${quality} out of 100`);
  }

  function updateChart() {
    frame = null;
    if (!ready) return;
    const patch = {}, q = shapeIndex('quality-cut'), v = shapeIndex('valuation-cut'), z = shapeIndex('hunt-zone');
    if (q >= 0) {patch[`shapes[${q}].y0`] = quality; patch[`shapes[${q}].y1`] = quality;}
    if (v >= 0) {patch[`shapes[${v}].x0`] = valuation; patch[`shapes[${v}].x1`] = valuation;}
    if (z >= 0) {patch[`shapes[${z}].y0`] = quality; patch[`shapes[${z}].x0`] = valuation;}
    Plotly.relayout(plot, patch);
    positionHandles();
  }

  function commit(baseQ, baseV) {
    document.getElementById('status').textContent = `Quality ${quality} percent. Valuation advantage ${valuation.toFixed(2)}.`;
    if (quality !== baseQ || valuation !== baseV) {
      suppressClickUntil = Date.now()+500;
      setValue({kind:'zone', quality, valuation, base_quality:baseQ, base_valuation:baseV});
    }
  }

  for (const [kind, el] of Object.entries(handles)) {
    el.addEventListener('pointerdown', e => {
      if (!ready || e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      drag = {kind, id:e.pointerId, quality, valuation};
      el.classList.add('dragging'); el.setPointerCapture(e.pointerId); el.focus({preventScroll:true});
    });
    el.addEventListener('pointermove', e => {
      if (!drag || drag.id !== e.pointerId) return;
      const bounds = plot.getBoundingClientRect();
      const axis = plot._fullLayout[kind === 'quality' ? 'yaxis' : 'xaxis'];
      const pixel = (kind === 'quality' ? e.clientY-bounds.top : e.clientX-bounds.left)-axis._offset;
      const value = clamp(kind, axis.p2l(pixel));
      if (kind === 'quality') quality = value; else valuation = value;
      if (!frame) frame = requestAnimationFrame(updateChart);
    });
    const finish = (e, cancel=false) => {
      if (!drag || drag.id !== e.pointerId) return;
      const original = drag; drag = null;
      el.classList.remove('dragging');
      if (cancel) {quality = original.quality; valuation = original.valuation;}
      if (frame) cancelAnimationFrame(frame);
      updateChart();
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
      if (!cancel) commit(original.quality, original.valuation);
    };
    el.addEventListener('pointerup', e => finish(e));
    el.addEventListener('pointercancel', e => finish(e,true));
    el.addEventListener('lostpointercapture', e => finish(e,true));
    el.addEventListener('keydown', e => {
      if (e.key === 'Escape' && drag) {finish({pointerId:drag.id},true); return;}
      if (!ready || drag) return;
      const delta = {ArrowUp:1, ArrowRight:1, ArrowDown:-1, ArrowLeft:-1}[e.key];
      if (!delta && !['Home','End'].includes(e.key)) return;
      e.preventDefault();
      const bq = quality, bv = valuation;
      const value = e.key === 'Home' ? (kind === 'quality' ? 0 : -2) : e.key === 'End' ? (kind === 'quality' ? 100 : 2)
        : (kind === 'quality' ? quality : valuation)+delta*(kind === 'quality' ? 1 : .05);
      if (kind === 'quality') quality=clamp(kind,value); else valuation=clamp(kind,value);
      updateChart(); commit(bq,bv);
    });
  }

  async function render(next) {
    if (applying) {queuedArgs = next; return;}
    args = next;
    if (renderedFigure === args.figure) return;
    applying = true;
    try {
      if (!window.Plotly) throw Error('Plotly unavailable');
      const figure = JSON.parse(args.figure);
      quality = args.quality; valuation = args.valuation;
      drag = null;
      Object.values(handles).forEach(el => el.classList.remove('dragging'));
      wrap.style.height = `${args.height || 615}px`;
      // Cutoffs can be dragged without switching Plotly out of its usual zoom mode.
      await Plotly.react(plot, figure.data, figure.layout, {responsive:true, displaylogo:false, scrollZoom:false,
        modeBarButtonsToRemove:['select2d','lasso2d']});
      if (!ready) {
        plot.on('plotly_afterplot', positionHandles);
        plot.on('plotly_relayout', positionHandles);
        plot.on('plotly_click', e => {
          if (drag || Date.now()<suppressClickUntil) return;
          const ticker = e.points?.[0]?.customdata?.[0];
          if (typeof ticker === 'string') setValue({kind:'stock',ticker});
        });
      }
      ready = true; renderedFigure = args.figure;
      positionHandles();
      send('streamlit:setFrameHeight', {height:args.height || 615});
    } catch (err) {
      document.getElementById('error').style.display = 'block';
      Object.values(handles).forEach(el => el.style.display = 'none');
      console.error(err);
    } finally {
      applying = false;
      if (queuedArgs) {const next = queuedArgs; queuedArgs = null; render(next);}
    }
  }
  window.addEventListener('message', e => {
    if (e.source === parent && e.data?.type === 'streamlit:render') render(e.data.args);
  });
  new ResizeObserver(() => {if (ready) Plotly.Plots.resize(plot).then(positionHandles);}).observe(wrap);
  send('streamlit:componentReady', {apiVersion:1});
  send('streamlit:setFrameHeight', {height:615});
})();
