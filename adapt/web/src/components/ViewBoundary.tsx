import { Component, type ReactNode } from 'react';
export class ViewBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed
      ? (this.props.fallback ?? (
          <section className="panel" role="alert">
            <h2>This view could not be displayed</h2>
            <p>
              Reload the workspace to retrieve the latest application and data. Any submitted action
              should be verified before retrying.
            </p>
            <button className="button secondary" onClick={() => window.location.reload()}>
              Reload workspace
            </button>
          </section>
        ))
      : this.props.children;
  }
}
