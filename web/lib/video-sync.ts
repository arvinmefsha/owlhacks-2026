/** One frame clock, shared by the video pixels and pose painted on the canvas. */
export function synchronizeVideo(
  video: HTMLVideoElement,
  render: (mediaTime: number) => void,
  updateTime: (mediaTime: number) => void,
) {
  let disposed = false;
  let frameCallback: number | null = null;
  let animation: number | null = null;
  let lastUiUpdate = -Infinity;
  const hasFrameCallback = typeof video.requestVideoFrameCallback === "function";
  const draw = (time: number, force = false) => {
    if (disposed || video.seeking || video.readyState < 2) return;
    render(time);
    const now = performance.now();
    if (force || video.paused || now - lastUiUpdate >= 100) {
      lastUiUpdate = now;
      updateTime(time);
    }
  };
  const frame: VideoFrameRequestCallback = (_now, metadata) => {
    frameCallback = null;
    if (disposed) return;
    draw(metadata.mediaTime);
    frameCallback = video.requestVideoFrameCallback(frame);
  };
  const fallback = () => {
    animation = null;
    if (disposed || video.paused || video.ended) return;
    draw(video.currentTime);
    animation = requestAnimationFrame(fallback);
  };
  const decoded = () => draw(video.currentTime, true);
  const play = () => {
    if (!hasFrameCallback && !video.paused && !video.ended && animation === null) animation = requestAnimationFrame(fallback);
  };
  const pause = () => {
    if (animation !== null) cancelAnimationFrame(animation);
    animation = null;
    // With RVFC, retain the last complete video/pose pair; a pause event can
    // precede the final presentation callback. Never overwrite it with wall time.
    if (!hasFrameCallback) decoded();
  };
  video.addEventListener("loadeddata", decoded);
  video.addEventListener("seeked", decoded);
  video.addEventListener("play", play);
  video.addEventListener("pause", pause);
  video.addEventListener("ended", pause);
  if (hasFrameCallback) frameCallback = video.requestVideoFrameCallback(frame);
  else play();
  decoded();
  return () => {
    disposed = true;
    if (frameCallback !== null) video.cancelVideoFrameCallback(frameCallback);
    if (animation !== null) cancelAnimationFrame(animation);
    video.removeEventListener("loadeddata", decoded);
    video.removeEventListener("seeked", decoded);
    video.removeEventListener("play", play);
    video.removeEventListener("pause", pause);
    video.removeEventListener("ended", pause);
  };
}
