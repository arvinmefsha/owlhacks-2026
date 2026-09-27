// Generate a temporary same-origin browser harness, then open /_sync-check.html.
// Remove the generated public file after checking the displayed results.
import { writeFileSync } from "node:fs";
const id = process.argv[2];
if (!/^[a-f0-9-]{36}$/.test(id ?? "")) throw new Error("Pass a saved dive UUID");
async function check(id) {
  const output = document.querySelector("pre");
  const report = text => { output.textContent += text + "\n"; };
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (predicate, label) => {
    for (let i=0; i<200; i++) { if (predicate()) return; await sleep(50); }
    throw new Error("Timeout: " + label);
  };
  try {
    const data = await (await fetch(`/api/dives/${id}`)).json();
    const iframe = document.querySelector("iframe");
    iframe.src = `/dives/${id}`;
    await until(() => iframe.contentDocument?.querySelector("video"), "review loaded");
    const win = iframe.contentWindow, doc = iframe.contentDocument, video = doc.querySelector("video");
    await until(() => video.readyState >= 2, "video decoded");
    const canvas = doc.querySelector('section[aria-label="Dive video review"] canvas');
    const ctx = canvas.getContext("2d");
    const records = []; let pts = null, record = null;
    const originalRequest = video.requestVideoFrameCallback.bind(video);
    video.requestVideoFrameCallback = cb => originalRequest((now, metadata) => {
      pts = metadata.mediaTime; cb(now, metadata); pts = null;
    });
    const originalDraw = ctx.drawImage.bind(ctx), originalArc = ctx.arc.bind(ctx);
    ctx.drawImage = (...args) => { record = {pts, time:video.currentTime, arcs:[]};records.push(record);originalDraw(...args); };
    ctx.arc = (...args) => { if(record) record.arcs.push(args.slice(0,2));originalArc(...args); };
    const frameAt = t => { let i=data.frames.t.length-1;while(i>=0 && data.frames.t[i]>t+.0001)i--;return i; };
    const seek = async t => {
      video.pause();video.currentTime=t;
      await until(() => !video.seeking && Math.abs(video.currentTime-t)<.01,"seek");
      await sleep(100);
    };
    const button = text => [...doc.querySelectorAll('button')].find(b=>b.textContent.trim()===text);
    await seek(5.8);
    button('Play').click();await until(()=>!video.paused,'play');await sleep(1200);
    button('Pause').click();await until(()=>video.paused && button('Play'),'pause');
    report('PASS normal playback / pause');
    const select=doc.querySelector('select');
    select.value='0.25';select.dispatchEvent(new win.Event('change',{bubbles:true}));
    await until(()=>video.playbackRate===.25,'slow speed');
    button('Play').click();await until(()=>!video.paused && button('Pause'),'slow play');await sleep(1200);button('Pause').click();await until(()=>video.paused && button('Play'),'slow pause');
    report('PASS quarter-speed playback');
    const slider=doc.querySelector('input[type=range]');
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype,'value').set.call(slider,'6.7');
    slider.dispatchEvent(new win.Event('input',{bubbles:true}));slider.dispatchEvent(new win.Event('change',{bubbles:true}));
    await until(()=>!video.seeking && Math.abs(video.currentTime-6.7)<.01,'scrub');
    await sleep(100); const before=frameAt(video.currentTime);
    doc.querySelector('button[aria-label="Next tracked frame"]').click();
    await until(()=>!video.seeking && frameAt(video.currentTime)===before+1,'next frame');
    doc.querySelector('button[aria-label="Previous tracked frame"]').click();
    await until(()=>!video.seeking && frameAt(video.currentTime)===before,'previous frame');
    report('PASS scrubbing and next/previous frame');
    const samples=records.filter(r=>r.pts!==null);
    assert(samples.length>10,'Insufficient decoded playback samples');
    for(const sample of samples) {
      const index=frameAt(sample.pts), flat=data.frames.lm[index];
      const expected=[];
      if(data.calibration.board_tip) expected.push([data.calibration.board_tip.x*canvas.width,data.calibration.board_tip.y*canvas.height]);
      if(flat) for(let j=0;j<17;j++) {
        const [x,y,,confidence]=flat.slice(j*4,j*4+4);
        if(confidence>=.15 && (data.calibration.water_y===null || y<data.calibration.water_y))expected.push([x*canvas.width,y*canvas.height]);
      }
      assert(expected.length===sample.arcs.length,'Joint count differs from decoded frame');
      expected.forEach((p,j)=>assert(Math.hypot(p[0]-sample.arcs[j][0],p[1]-sample.arcs[j][1])<.01,'Wrong frame skeleton'));
    }
    report(`PASS ${samples.length} decoded frames: video pixels and exact PTS-matched skeleton drawn together`);
    report('ALL BROWSER CHECKS PASSED');
  } catch(error) { report('FAIL '+error.message); }
}
writeFileSync(new URL('../public/_sync-check.html',import.meta.url),
  `<!doctype html><title>Playback verification</title><h1>Playback verification</h1><pre>Checking…\n</pre><iframe style="width:1100px;height:850px"></iframe><script>(${check.toString()})(${JSON.stringify(id)})</script>`);
