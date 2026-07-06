import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import OmegaFlowVideo from '@site/src/components/OmegaFlowVideo';

export default function Home() {
  return (
    <Layout
      title="OmegaFlow Studio"
      description="Scripted terminal walkthroughs you can rebuild"
    >
      <main>
        <section className="homeHero">
          <div className="container homeHero__inner">
            <div className="homeHero__copy">
              <p className="homeHero__eyebrow">OmegaFlow Studio</p>
              <h1>Rebuildable terminal demos.</h1>
              <p className="homeHero__lede">
                Script the workflow. Generate the video. Publish it with the docs.
              </p>
              <div className="homeHero__actions">
                <Link className="button button--primary button--lg" to="/intro">
                  Read the intro
                </Link>
              </div>
            </div>
            <div className="homeHero__video" aria-label="OmegaFlow Studio quick start video">
              <OmegaFlowVideo
                title="Getting Started With OmegaFlow Studio"
                src="/omegaflow-videos/getting-started/getting-started.retimed.cast"
              />
            </div>
          </div>
        </section>

        <section className="homeBand">
          <div className="container homeGrid">
            <article>
              <h2>Script</h2>
              <p>Write the walkthrough as a versioned Studio script.</p>
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
