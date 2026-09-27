let queue: Promise<void> = Promise.resolve();
let generation = 0;
let request: AbortController | null = null;
let interrupt: (() => void) | null = null;

/** Speak a coaching tip after any tip already playing. Uses the coach voice when the server has one, else the browser voice. */
export function speakTip(text: string): Promise<void> {
  const ticket = generation;
  queue = queue.then(() => (ticket === generation ? say(text, ticket) : undefined)).catch(() => undefined);
  return queue;
}

/** Silence the current tip and drop any that are waiting. */
export function stopSpeech() {
  generation += 1;
  request?.abort();
  interrupt?.();
  if (typeof window !== "undefined") window.speechSynthesis?.cancel();
}

async function say(text: string, ticket: number) {
  const audio = await fetchVoice(text);
  if (ticket !== generation) return;
  if (audio && (await playAudio(audio))) return;
  if (ticket !== generation) return;
  await speakWithBrowser(text);
}

async function fetchVoice(text: string): Promise<Blob | null> {
  const controller = new AbortController();
  request = controller;
  try {
    const response = await fetch("/api/live/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal: controller.signal,
    });
    if (response.status !== 200) return null;
    const blob = await response.blob();
    return blob.size ? blob : null;
  } catch {
    return null;
  } finally {
    if (request === controller) request = null;
  }
}

function playAudio(blob: Blob): Promise<boolean> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    let settled = false;
    const finish = (played: boolean) => {
      if (settled) return;
      settled = true;
      interrupt = null;
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      URL.revokeObjectURL(url);
      resolve(played);
    };
    interrupt = () => finish(true);
    audio.onended = () => finish(true);
    audio.onerror = () => finish(false);
    audio.play().catch(() => finish(false));
  });
}

function speakWithBrowser(text: string): Promise<void> {
  const synth = typeof window === "undefined" ? undefined : window.speechSynthesis;
  if (!synth || typeof SpeechSynthesisUtterance === "undefined") return Promise.resolve();
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      interrupt = null;
      clearTimeout(watchdog);
      resolve();
    };
    // Some browsers never fire `end` for an utterance; one lost event must not stall every later tip.
    const watchdog = setTimeout(finish, 5000 + text.length * 100);
    interrupt = () => {
      synth.cancel();
      finish();
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    synth.speak(utterance);
  });
}
