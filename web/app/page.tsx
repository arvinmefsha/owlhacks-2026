import Link from "next/link";

function Arrow() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M3 10h13m-5-5 5 5-5 5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

export default function Home() {
  return <div className="landing">
    <div className="landing-grid" aria-hidden="true" />
    <div className="landing-shell">
      <section className="landing-hero" aria-labelledby="hero-title">
        <div className="landing-copy">
          <div className="landing-kicker"><span /> INTELLIGENCE IN MOTION</div>
          <h1 id="hero-title">Make every dive <em>count.</em></h1>
          <p>See your form clearly. Understand what to improve. Step onto the board with more confidence every time.</p>
          <div className="landing-actions">
            <Link href="/record" className="landing-primary">Analyze a dive <Arrow /></Link>
            <Link href="/upload" className="landing-secondary">Upload a video <Arrow /></Link>
          </div>
          <div className="landing-note"><i /> Built for the moments between practice and progress</div>
        </div>
        <div className="landing-art" aria-label="Illustration of a dive analysis">
          <div className="landing-ring landing-ring-one" /><div className="landing-ring landing-ring-two" />
          <div className="landing-card">
            <div className="landing-card-head"><span className="landing-light" /> FORM ANALYSIS <span className="landing-live">● LIVE INSIGHT</span></div>
            <div className="landing-scene">
              <svg viewBox="0 0 390 310" className="landing-diver" fill="none" aria-hidden="true">
                <defs><linearGradient id="dive-line" x1="90" y1="20" x2="300" y2="280" gradientUnits="userSpaceOnUse"><stop stopColor="#E5FFFA"/><stop offset="1" stopColor="#69D0DE"/></linearGradient></defs>
                <path d="M106 71C167 10 273 30 316 98M75 127C96 217 196 274 302 257" stroke="#9DEBE4" strokeOpacity=".26" strokeDasharray="3 9"/>
                <path d="m143 57 49 49 40 16 32 52-47 24-54-25-33-45-29-32" fill="#5CD9D6" fillOpacity=".13" stroke="url(#dive-line)" strokeWidth="5" strokeLinejoin="round" strokeLinecap="round"/>
                <path d="m143 55-38-27m38 27-21-42m70 93 59-56m-19 72 73-24m-41 76 70 7m-117 17 22 73m-76-98-52 67m19-112-50 19" stroke="url(#dive-line)" strokeWidth="7" strokeLinecap="round"/>
                <circle cx="143" cy="55" r="19" fill="#C4FFF6" fillOpacity=".18" stroke="#D9FFFB" strokeWidth="5"/>
                <circle cx="305" cy="98" r="7" fill="#B9F8AD"/><circle cx="305" cy="98" r="15" stroke="#B9F8AD" strokeOpacity=".4"/>
                <circle cx="239" cy="271" r="7" fill="#B9F8AD"/><circle cx="239" cy="271" r="15" stroke="#B9F8AD" strokeOpacity=".4"/>
                {[[192,106],[232,122],[264,174],[217,198],[163,173],[130,128]].map(([cx,cy]) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="5" fill="#D8FFF9"/>)}
              </svg>
              <div className="landing-metric landing-metric-one">ENTRY ANGLE<strong>86°</strong></div>
              <div className="landing-metric landing-metric-two">BODY ALIGNMENT<strong>92%</strong></div>
            </div>
            <div className="landing-card-foot">Clarity in every frame <span>▂ ▄ ▆ █ ▆ ▄ ▂</span></div>
          </div>
          <div className="landing-chip landing-chip-one"><span>✦</span><div>Small adjustments.<br/><strong>Big breakthroughs.</strong></div></div>
          <div className="landing-chip landing-chip-two"><span>✓</span> Ready for your next dive</div>
        </div>
      </section>
      <section className="landing-process" aria-label="How it works">
        <div className="landing-process-title"><span>THE PROCESS</span><h2>From footage to forward motion.</h2></div>
        <div className="landing-steps">
          <div><b>01</b><section><h3>Capture the moment</h3><p>Record a dive live or bring a video you already have.</p></section></div>
          <div><b>02</b><section><h3>See what matters</h3><p>Turn movement into clear, focused feedback on your form.</p></section></div>
          <div><b>03</b><section><h3>Keep getting better</h3><p>Follow your progress and give every session a purpose.</p></section></div>
        </div>
      </section>
      <footer className="landing-footer"><span>DIVE FORM ANALYZER</span><Link href="/progress">Explore your progress <Arrow /></Link></footer>
    </div>
    <style>{`
      .landing{position:relative;isolation:isolate;overflow:hidden;width:100vw;left:50%;transform:translateX(-50%);margin:-1.5rem 0;min-height:calc(100vh - 55px);background:radial-gradient(ellipse 55% 65% at 76% 39%,#144251 0%,transparent 70%),linear-gradient(130deg,#071722,#0b2833 55%,#071722);color:#eaf8f5;font-family:var(--font-geist-sans),Arial,sans-serif}
      .landing:before{content:"";position:absolute;z-index:-1;inset:0;background:radial-gradient(circle at 75% 39%,#55bcb51c,transparent 35%),radial-gradient(circle at 0% 100%,#348a9b18,transparent 40%)}
      .landing-grid{position:absolute;z-index:-1;inset:0;background-image:linear-gradient(#b8f4ed0a 1px,transparent 1px),linear-gradient(90deg,#b8f4ed0a 1px,transparent 1px);background-size:70px 70px;mask-image:linear-gradient(transparent,black 12%,black 75%,transparent)}
      .landing-shell{max-width:1340px;margin:auto;padding:0 clamp(24px,5vw,76px)}
      .landing-hero{min-height:690px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.06fr);gap:3vw;align-items:center;padding:72px 0 62px}
      .landing-copy{position:relative;z-index:2;animation:landing-rise .85s both}.landing-kicker{display:flex;align-items:center;gap:12px;color:#a7e8dd;letter-spacing:.22em;font-size:11px;font-weight:700;margin-bottom:28px}.landing-kicker span{width:8px;height:8px;border-radius:50%;background:#b7ffe4;box-shadow:0 0 0 5px #b7ffe420,0 0 18px #b7ffe4;animation:landing-pulse 3s infinite}
      .landing h1{max-width:650px;margin:0;font-size:clamp(64px,6.5vw,102px);line-height:.99;letter-spacing:-.072em;font-weight:600}.landing h1 em{display:block;color:#a9e8d8;font:normal 400 1em Georgia,serif;letter-spacing:-.065em}.landing-copy>p{max-width:490px;margin:32px 0 0;color:#b2cbd0;font-size:clamp(17px,1.45vw,20px);line-height:1.7}
      .landing-actions{display:flex;flex-wrap:wrap;gap:13px;margin-top:36px}.landing-actions a{min-height:54px;display:inline-flex;align-items:center;justify-content:center;gap:23px;padding:0 22px;border-radius:7px;font-size:14px;font-weight:650;text-decoration:none;transition:transform .25s,background .25s,box-shadow .25s,border-color .25s}.landing-actions a:hover{transform:translateY(-3px)}.landing svg{flex:none}.landing-actions svg,.landing-footer svg{width:18px;height:18px;transition:transform .25s}.landing-actions a:hover svg,.landing-footer a:hover svg{transform:translateX(4px)}.landing a:focus-visible{outline:2px solid #c7ffed;outline-offset:4px}.landing-primary{background:#b8f1dd;color:#082832;box-shadow:0 12px 35px #90edce24}.landing-primary:hover{background:#d0ffed;box-shadow:0 17px 45px #90edce35}.landing-secondary{color:#e8f8f4;border:1px solid #90bdc356;background:#ffffff08}.landing-secondary:hover{background:#ffffff15;border-color:#c4eee4}
      .landing-note{display:flex;align-items:center;gap:14px;margin-top:55px;color:#7a9da2;font-size:11px}.landing-note i{width:31px;height:1px;background:#75adb0}
      .landing-art{position:relative;height:570px;min-width:0;display:flex;align-items:center;justify-content:center;animation:landing-rise 1.1s .1s both}.landing-art:before{content:"";position:absolute;width:560px;height:560px;border-radius:50%;background:radial-gradient(circle,#59c7bf22,transparent 68%);filter:blur(10px)}.landing-ring{position:absolute;border:1px solid #a7ebdd1c;border-radius:50%;width:520px;height:520px;transform:rotate(-24deg) scaleY(.78)}.landing-ring-two{width:620px;height:620px;border-color:#a7ebdd12}
      .landing-card{position:relative;z-index:1;width:min(100%,485px);border:1px solid #87d7d53d;border-radius:16px;overflow:hidden;background:linear-gradient(145deg,#153b46ed,#0b2532f2);box-shadow:0 34px 90px #010d18a8,inset 0 1px #ffffff18;animation:landing-float 7s ease-in-out infinite;backdrop-filter:blur(18px)}.landing-card-head{height:51px;display:flex;align-items:center;gap:9px;padding:0 21px;border-bottom:1px solid #a3e4df21;color:#aac8ca;font-size:9px;font-weight:700;letter-spacing:.17em}.landing-light{width:7px;height:7px;border-radius:50%;background:#a8eada;box-shadow:0 0 12px #8ee3d6}.landing-live{margin-left:auto;color:#a9ebc9;letter-spacing:.11em;font-size:9px}
      .landing-scene{position:relative;height:354px;overflow:hidden;background:radial-gradient(circle at 50% 40%,#2871804d,transparent 63%),linear-gradient(130deg,#0d2b38,#123f4b 55%,#0a2734)}.landing-scene:after{content:"";position:absolute;inset:0;background:linear-gradient(transparent 94%,#8fdad90e 95%),linear-gradient(90deg,transparent 94%,#8fdad90e 95%);background-size:32px 32px;mask-image:radial-gradient(circle,black,transparent 78%)}.landing-diver{position:absolute;z-index:1;left:50%;top:50%;width:88%;height:90%;transform:translate(-50%,-50%) rotate(3deg);filter:drop-shadow(0 0 13px #97f9ef56)}.landing-metric{position:absolute;z-index:2;border:1px solid #9de6de38;border-radius:5px;background:#0a2837db;color:#a9d3d2;padding:8px 10px;font-size:8px;letter-spacing:.12em;box-shadow:0 8px 22px #00141b55}.landing-metric strong{display:block;margin-top:3px;color:#d2fff2;font-size:17px;letter-spacing:0;font-weight:500}.landing-metric-one{top:66px;right:24px}.landing-metric-two{left:24px;bottom:24px}.landing-card-foot{height:51px;display:flex;align-items:center;justify-content:space-between;padding:0 21px;color:#87aaae;font-size:11px}.landing-card-foot span{color:#a4e7d3;letter-spacing:2px}
      .landing-chip{position:absolute;z-index:3;display:flex;align-items:center;gap:12px;border:1px solid #c0f4ec3e;background:#d7f7eeed;color:#17424b;box-shadow:0 16px 40px #00151d70;backdrop-filter:blur(14px);font-size:12px;line-height:1.4}.landing-chip-one{top:49px;left:-8px;padding:12px 18px;border-radius:9px;transform:rotate(4deg)}.landing-chip-one>span{font-size:22px;color:#3b9d9b}.landing-chip-two{right:-9px;bottom:59px;padding:11px 15px;border-radius:6px;transform:rotate(3deg);font-weight:600}.landing-chip-two span{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#3eaa94;color:white;font-size:13px}
      .landing-process{border-top:1px solid #a1d5d42b;padding:43px 0 60px;display:grid;grid-template-columns:1fr 2fr;gap:50px}.landing-process-title>span{color:#9bd9cf;font-size:10px;font-weight:700;letter-spacing:.23em}.landing-process-title h2{max-width:300px;margin:14px 0 0;font-size:28px;line-height:1.17;letter-spacing:-.045em;font-weight:500}.landing-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:27px}.landing-steps>div{display:flex;gap:13px}.landing-steps b{color:#88c9bf;font-size:11px;font-weight:500;padding-top:3px}.landing-steps h3{font-size:14px;font-weight:600;margin:0 0 7px}.landing-steps p{margin:0;color:#89a9ad;font-size:12px;line-height:1.6}
      .landing-footer{border-top:1px solid #a1d5d420;padding:22px 0 28px;display:flex;justify-content:space-between;align-items:center;color:#678b92;font-size:10px;letter-spacing:.17em}.landing-footer a{display:flex;align-items:center;gap:8px;color:#b7ded7;font-size:11px;letter-spacing:0;text-decoration:none}.landing-footer svg{width:16px;height:16px}
      @keyframes landing-rise{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:translateY(0)}}@keyframes landing-float{0%,100%{transform:translateY(0) rotate(-3deg)}50%{transform:translateY(-12px) rotate(-2deg)}}@keyframes landing-pulse{50%{box-shadow:0 0 0 8px #b7ffe408,0 0 25px #b7ffe4}}
      @media(max-width:1000px){.landing-hero{grid-template-columns:1fr;gap:10px;padding-top:80px}.landing-copy{text-align:center}.landing-kicker,.landing-actions,.landing-note{justify-content:center}.landing h1,.landing-copy>p{margin-left:auto;margin-right:auto}.landing-note{margin-top:35px}.landing-art{height:510px;max-width:600px;width:100%;margin:auto}.landing-process{grid-template-columns:1fr;gap:28px}.landing-process-title h2{max-width:none}}
      @media(max-width:640px){.landing-shell{padding:0 23px}.landing-hero{padding:66px 0 30px;min-height:0}.landing-kicker{font-size:9px;margin-bottom:24px}.landing h1{font-size:clamp(54px,13vw,76px)}.landing-copy>p{font-size:16px;margin-top:25px}.landing-actions{gap:10px}.landing-actions a{width:100%;min-height:52px}.landing-note{font-size:10px;line-height:1.4}.landing-art{height:400px}.landing-card{width:86%}.landing-scene{height:260px}.landing-ring{width:390px;height:390px}.landing-ring-two{width:460px;height:460px}.landing-chip-one{top:15px;left:0;padding:8px 10px;font-size:10px}.landing-chip-two{right:0;bottom:24px;font-size:10px;padding:8px 10px}.landing-process{padding:36px 0 42px}.landing-process-title h2{font-size:26px}.landing-steps{grid-template-columns:1fr;gap:24px}.landing-steps h3{font-size:15px}.landing-steps p{font-size:13px}.landing-footer{gap:20px}.landing-footer span{font-size:9px}}
      @media(prefers-reduced-motion:reduce){.landing-copy,.landing-art,.landing-card,.landing-kicker span{animation:none!important}.landing-actions a,.landing-actions svg,.landing-footer svg{transition:none!important}}
    `}</style>
  </div>;
}
