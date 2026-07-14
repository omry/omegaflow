import React, {useEffect} from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';

export default function VideoPlayer({
  src,
  manifest,
  title,
  audio,
  audioMeta,
  intro,
  introSegment,
  introSeconds,
}) {
  const recordingSrc = useBaseUrl(manifest || src);
  const castSrc = manifest ? null : recordingSrc;
  const manifestSrc = manifest ? recordingSrc : null;
  const audioSrc = audio ? useBaseUrl(audio) : null;
  const audioMetaSrc = audioMeta ? useBaseUrl(audioMeta) : null;
  const playerSrc = useBaseUrl('/cast-player.html');
  const embedScriptSrc = useBaseUrl('/cast-player-embed.js');

  useEffect(() => {
    if (document.querySelector(`script[src="${embedScriptSrc}"]`)) {
      return;
    }
    const script = document.createElement('script');
    script.src = embedScriptSrc;
    script.async = true;
    document.head.appendChild(script);
  }, [embedScriptSrc]);

  return (
    <div className="video-player">
      <cast-player-embed
        title={title}
        src={castSrc || undefined}
        manifest={manifestSrc || undefined}
        audio={audioSrc || undefined}
        audio-meta={audioMetaSrc || undefined}
        intro={intro || undefined}
        intro-segment={introSegment || undefined}
        intro-seconds={introSeconds != null ? String(introSeconds) : undefined}
        player={playerSrc}
      />
    </div>
  );
}
