import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';

export default function Home() {
  return (
    <Layout
      title="OmegaFlow Studio"
      description="Scripted terminal and video flows"
    >
      <main className="container margin-vert--xl">
        <h1>OmegaFlow Studio</h1>
        <p>
          Author reproducible terminal workflows and build them into
          website-ready OmegaFlow Videos.
        </p>
        <p>
          <Link className="button button--primary" to="/intro">
            Read the intro
          </Link>
        </p>
      </main>
    </Layout>
  );
}
