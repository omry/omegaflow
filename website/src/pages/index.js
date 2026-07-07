import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import VideoPlayer from '@site/src/components/VideoPlayer';

export default function Home() {
  return (
    <Layout
      title="OmegaFlow"
      description="Scripted terminal walkthroughs you can rebuild"
    >
      <main>
        <section className="homeHero">
          <div className="container homeHero__inner">
            <div className="homeHero__copy">
              <h1>Rebuildable terminal demos.</h1>
              <p className="homeHero__lede">
                Write the terminal flow once, then rebuild the video whenever the docs change.
              </p>
              <div className="homeHero__actions">
                <Link className="button button--primary button--lg" to="/tutorial/quickstart">
                  Start the tutorial
                </Link>
              </div>
            </div>
            <div className="homeHero__video" aria-label="Quick start video">
              <VideoPlayer
                title="Quickstart Demo"
                src="/omegaflow-videos/quickstart-demo/quickstart-demo.retimed.cast"
              />
            </div>
          </div>
        </section>

        <section className="homeBand">
          <div className="container homeGrid">
            <article>
              <h2>Script</h2>
              <p>Write the walkthrough as a versioned script.</p>
            </article>
            <article>
              <h2>Build</h2>
              <p>Record, retime, and package the terminal video.</p>
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
