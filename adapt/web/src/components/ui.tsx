import { useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, Check, ChevronDown, Inbox, Info, X } from 'lucide-react';
import type { Metric } from '../api/contracts';
import { humanStatus, money, percent } from '../lib/format';

export function Badge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'accent';
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
export function Status({ value }: { value: string }) {
  const status = value.toUpperCase();
  const tone = [
    'EXECUTED',
    'SUCCEEDED',
    'VERIFIED',
    'SUCCESS',
    'GREEN',
    'APPROVED',
    'ONLINE',
    'READY',
    'HEALTHY',
    'AVAILABLE',
    'APPLIED',
    'FEASIBLE',
    'COMPLETE',
    'COMPLETED',
    'RESOLVED',
    'COMPENSATED',
    'RESOLVED_MANUALLY',
  ].includes(status)
    ? 'success'
    : [
          'BLOCKED',
          'FAILED',
          'RED',
          'UNKNOWN',
          'CONFLICT',
          'COMPENSATION_FAILED',
          'HUMAN_RESOLUTION_REQUIRED',
        ].includes(status)
      ? 'danger'
      : [
            'PENDING',
            'PENDING_APPROVAL',
            'PARTIAL',
            'YELLOW',
            'OPEN',
            'ACKNOWLEDGED',
            'EXECUTING',
            'COMPENSATING',
          ].includes(status)
        ? 'warning'
        : ['ACTIVE', 'INFO', 'INFORMATIONAL'].includes(status)
          ? 'accent'
          : 'neutral';
  return (
    <Badge tone={tone}>
      {status === 'PENDING_APPROVAL' ? 'Needs approval' : humanStatus(value)}
    </Badge>
  );
}
export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon" aria-hidden="true">
        <Inbox size={22} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading({ label = 'Loading workspace' }: { label?: string }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <div className="loading-skeleton" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
      <span>{label}</span>
    </div>
  );
}
export function ErrorState({ error, retry }: { error: Error; retry: () => void }) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={22} />
      <div>
        <h3>We couldn’t load this view</h3>
        <p>{error.message}</p>
        <button className="button secondary" onClick={retry}>
          Try again
        </button>
      </div>
    </div>
  );
}
export function InlineError({ error }: { error: Error | null }) {
  return error ? (
    <p className="inline-error" role="alert">
      <AlertCircle size={16} />
      {error.message}
    </p>
  ) : null;
}
export function MetricTile({ metric }: { metric: Metric }) {
  const [open, setOpen] = useState(false);
  const value =
    metric.value === null
      ? '—'
      : metric.format === 'money'
        ? money(metric.value, true)
        : metric.format === 'ratio'
          ? `${metric.value.toFixed(2)}×`
          : metric.value.toLocaleString('en-IN');
  return (
    <div className="metric-tile">
      <div className="metric-label">
        {metric.label}
        <button
          className="icon-button"
          aria-label={`Lineage for ${metric.label}`}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <Info size={14} />
        </button>
      </div>
      <strong
        title={metric.value === null ? metric.reason || 'ZERO_DENOMINATOR' : String(metric.value)}
      >
        {value}
      </strong>
      {metric.change !== null ? (
        <small className={metric.change < 0 ? 'text-danger' : 'text-success'}>
          {metric.change > 0 ? '+' : ''}
          {percent(metric.change)} <span className="muted">vs prior 7 days</span>
        </small>
      ) : (
        <small className="text-warning">Projected stock shortfall</small>
      )}
      {open && (
        <div className="lineage">
          <b>Derived metric</b>
          <p>{metric.formula}</p>
          <span>{metric.source}</span>
          <span>Available: {metric.available_at}</span>
          <div>
            {metric.provenance_inputs.map((p) => (
              <Badge key={p}>{p}</Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
export function SectionTitle({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="section-title">
      <h2>{title}</h2>
      {children}
    </div>
  );
}
export function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="disclosure">
      <summary>
        {title}
        <ChevronDown size={16} />
      </summary>
      <div>{children}</div>
    </details>
  );
}
export function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const d = ref.current!;
    d.showModal();
    return () => {
      d.close();
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="modal"
      aria-labelledby="modal-title"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <div className="section-title">
        <h2 id="modal-title">{title}</h2>
        <button className="icon-button" aria-label="Close dialog" onClick={close}>
          <X size={20} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function PolicyCheck({
  passed,
  label,
  detail,
  id,
}: {
  passed: boolean;
  label: string;
  detail: string;
  id: string;
}) {
  return (
    <div className={`policy-check ${passed ? '' : 'failed'}`}>
      {passed ? <Check size={16} /> : <X size={16} />}
      <div>
        <strong>{label}</strong>
        <p>{detail}</p>
        <code>{id}</code>
      </div>
    </div>
  );
}
