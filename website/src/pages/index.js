import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import VideoPlayer from '@site/src/components/VideoPlayer';

export default function Home() {
  return (
    <Layout
      title="OmegaFlow"
      description="Scripted software demos you can rebuild"
    >
      <main>
        <section className="homeHero">
          <div className="container homeHero__inner">
            <div className="homeHero__copy">
              <div className="homeHero__identity">
                <span>Scripted. Synchronized. Rebuildable.</span>
              </div>
              <div className="homeHero__headline">
                <h1>Rebuildable product demos.</h1>
                <img
                  className="homeHero__mascot"
                  src="/img/omegaflow-mascot-camera.svg"
                  alt="OmegaFlow mascot holding a video camera"
                />
              </div>
              <p className="homeHero__lede">
                Script terminal and browser workflows once, then rebuild synchronized videos
                whenever your product or documentation changes. To learn more, start the
                tutorial or read the docs.
              </p>
              <div className="homeHero__actions">
                <Link className="button button--primary button--lg" to="/tutorial/quickstart">
                  Start the tutorial
                </Link>
                <Link className="button button--lg homeHero__docsButton" to="/intro">
                  Read the docs
                </Link>
              </div>
            </div>
            <div className="homeHero__video" aria-label="Quick start video">
              <VideoPlayer
                title="OmegaFlow Overview"
                manifest="/omegaflow-videos/quickstart-demo/presentation/recording.presentation.json"
              />
            </div>
          </div>
        </section>

        <section className="homeBand">
          <div className="container homeGrid">
            <article>
              <h2>Script</h2>
              <p>Author terminal commands and browser interactions as a versioned workflow.</p>
            </article>
            <article>
              <h2>Build</h2>
              <p>Turn the workflow into a synchronized, ready-to-publish presentation.</p>
            </article>
            <article>
              <h2>Publish</h2>
              <p>Embed the generated asset in the docs.</p>
            </article>
          </div>
        </section>
      </main>
    </Layout>
  );
}
