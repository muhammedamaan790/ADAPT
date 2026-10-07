import { useEffect, useRef, useState, useId, type ReactNode } from 'react';
import { AlertCircle, Check, Inbox, ChevronDown, Info, Loader2, X } from 'lucide-react';
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
  const tone = [
    'EXECUTED',
    'SUCCEEDED',
    'VERIFIED',
    'SUCCESS',
    'GREEN',
    'RESOLVED',
    'COMPENSATED',
    'RESOLVED_MANUALLY',
  ].includes(value)
    ? 'success'
    : [
          'BLOCKED',
          'FAILED',
          'RED',
          'UNKNOWN',
          'CONFLICT',
          'COMPENSATION_FAILED',
          'HUMAN_RESOLUTION_REQUIRED',
        ].includes(value)
      ? 'danger'
      : ['PENDING_APPROVAL', 'APPROVED', 'PARTIAL', 'YELLOW', 'OPEN', 'ACKNOWLEDGED'].includes(
            value,
          )
        ? 'warning'
        : 'neutral';
  return (
    <Badge tone={tone}>
      {value === 'PENDING_APPROVAL' ? 'Needs approval' : humanStatus(value)}
    </Badge>
  );
}
export function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <Inbox size={28} aria-hidden="true" />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Loading({ label = 'Loading workspace' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <Loader2 className="spin" size={22} />
      {label}
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
  const lineageId = useId();
  const popover = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: 16, top: 100 });
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
          aria-controls={lineageId}
          onClick={(event) => {
            const box = event.currentTarget.getBoundingClientRect();
            setPosition({
              left: Math.max(16, Math.min(box.left, window.innerWidth - 304)),
              top: Math.min(box.bottom + 8, Math.max(16, window.innerHeight - 320)),
            });
            popover.current?.togglePopover();
          }}
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
        <small
          className={
            /spend/i.test(metric.label)
              ? 'muted'
              : metric.change < 0
                ? 'text-danger'
                : 'text-success'
          }
        >
          {metric.change > 0 ? '+' : ''}
          {percent(metric.change)} <span className="muted">vs prior 7 days</span>
        </small>
      ) : (
        <small className="muted">No comparison supplied</small>
      )}
      <div
        className="lineage"
        id={lineageId}
        ref={popover}
        popover="auto"
        style={position}
        onToggle={(event) => setOpen(event.currentTarget.matches(':popover-open'))}
      >
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
