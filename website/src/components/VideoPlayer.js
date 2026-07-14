import React, {useEffect} from 'react';
import useBaseUrl from '@docusaurus/useBaseUrl';

export default function VideoPlayer({
  manifest,
  title,
  intro,
  introSegment,
  introSeconds,
}) {
  const manifestSrc = useBaseUrl(manifest);
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
        manifest={manifestSrc}
        intro={intro || undefined}
        intro-segment={introSegment || undefined}
        intro-seconds={introSeconds != null ? String(introSeconds) : undefined}
        player={playerSrc}
      />
    </div>
  );
}
