import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUp, Check, CircleAlert, RotateCcw, Square } from 'lucide-react';
import { askAdapt } from '../api/insights';
import type { CopilotReply, CopilotTurn } from '../api/insight-contracts';

type Message =
  | { id: string; role: 'user'; text: string }
  | { id: string; role: 'assistant'; reply: CopilotReply }
  | { id: string; role: 'error'; text: string; question: string };

const STORAGE_KEY = 'adapt.ask.thread';
const SUGGESTIONS = [
  'How did we do this week?',
  'What is waiting for my approval?',
  'Why is a campaign losing efficiency?',
  'Is any data source unhealthy?',
  'What has the system learned so far?',
];

function greeting(now = new Date()) {
  const h = now.getHours();
  return h < 5
    ? 'Good evening'
    : h < 12
      ? 'Good morning'
      : h < 17
        ? 'Good afternoon'
        : 'Good evening';
}

function load(): Message[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Message[]) : [];
  } catch {
    return [];
  }
}

function save(thread: Message[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(thread.slice(-40)));
  } catch {
    // The conversation still works for this view without storage.
  }
}

/** Bold (**x**) inside a line; everything else stays text, so model output can never inject markup. */
function inline(text: string): ReactNode[] {
  return text
    .split(/(\*\*[^*]+\*\*)/g)
    .map((part, i) =>
      part.startsWith('**') && part.endsWith('**') && part.length > 4 ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : (
        part
      ),
    );
}

/** Paragraphs and "- " / "1. " lists: the only structure the agent is asked to use. */
function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const items = list.items.map((item, i) => <li key={i}>{inline(item)}</li>);
    blocks.push(
      list.ordered ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>,
    );
    list = null;
  };
  for (const line of text.split('\n')) {
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      const ordered = !bullet;
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      }
      list.items.push((bullet || numbered)![1]);
    } else if (line.trim()) {
      flush();
      blocks.push(<p key={blocks.length}>{inline(line.trim())}</p>);
    } else flush();
  }
  flush();
  return <div className="ask-text">{blocks}</div>;
}

function Answer({ reply }: { reply: CopilotReply }) {
  const unverified = reply.unverified || [];
  return (
    <>
      <RichText text={reply.text} />
      <div className="ask-meta">
        {reply.evidence.map((e) => (
          <Link key={e.href} className="ask-source" to={e.href}>
            {e.label} <span aria-hidden="true">→</span>
          </Link>
        ))}
        {reply.mode === 'LLM' &&
          (unverified.length ? (
            <span className="badge badge-warning" title="These figures were not found in the data">
              Not found in data: {unverified.join(', ')}
            </span>
          ) : reply.verified ? (
            <span className="ask-checked">
              <Check size={13} aria-hidden="true" /> Figures checked against your data
            </span>
          ) : null)}
      </div>
      {reply.note && <p className="ask-note">{reply.note}</p>}
    </>
  );
}

export function AskAdapt() {
  const [thread, setThread] = useState<Message[]>(load);
  const [draft, setDraft] = useState('');
  const [steps, setSteps] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const inputId = useId();

  useEffect(() => save(thread), [thread]);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    const el = log.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread, steps]);

  const resize = () => {
    const el = input.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  };

  const ask = async (question: string) => {
    const text = question.trim();
    if (!text || pending) return;
    const history: CopilotTurn[] = thread.flatMap((m): CopilotTurn[] =>
      m.role === 'user'
        ? [{ role: 'user', content: m.text }]
        : m.role === 'assistant'
          ? [{ role: 'assistant', content: m.reply.text }]
          : [],
    );
    const id = `${Date.now()}`;
    setThread((t) => [...t, { id: `${id}-q`, role: 'user', text }]);
    setDraft('');
    requestAnimationFrame(resize);
    setSteps([]);
    setPending(true);
    const controller = new AbortController();
    abort.current = controller;
    try {
      const reply = await askAdapt(text, history, controller.signal, (s) =>
        setSteps((prev) => (prev[prev.length - 1] === s ? prev : [...prev, s])),
      );
      setThread((t) => [...t, { id: `${id}-a`, role: 'assistant', reply }]);
    } catch (error) {
      const cancelled = controller.signal.aborted;
      setThread((t) => [
        ...t,
        {
          id: `${id}-e`,
          role: 'error',
          question: text,
          text: cancelled
            ? 'Stopped.'
            : error instanceof Error
              ? error.message
              : 'The assistant could not answer.',
        },
      ]);
    } finally {
      setPending(false);
      setSteps([]);
      abort.current = null;
      input.current?.focus();
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void ask(draft);
  };

  const reset = () => {
    abort.current?.abort();
    setThread([]);
    input.current?.focus();
  };

  return (
    <section className="panel ask-panel" aria-labelledby={`${inputId}-title`}>
      <div className="ask-head">
        <p className="ask-eyebrow">
          <span className="ask-dot" aria-hidden="true" />
          Ask ADAPT
        </p>
        {thread.length > 0 && (
          <button className="button ghost" onClick={reset} disabled={pending && !abort.current}>
            <RotateCcw size={14} aria-hidden="true" /> New conversation
          </button>
        )}
      </div>
      <h2 id={`${inputId}-title`} className="ask-greeting">
        {greeting()}. What would you like to know?
      </h2>

      {thread.length === 0 ? (
        <div className="ask-suggestions" role="group" aria-label="Suggested questions">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="ask-chip" onClick={() => void ask(s)}>
              {s} <span aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      ) : (
        <div
          className="ask-thread"
          ref={log}
          role="log"
          aria-live="polite"
          aria-label="Conversation with ADAPT"
          tabIndex={0}
        >
          {thread.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="ask-msg ask-user">
                <p>{m.text}</p>
              </div>
            ) : m.role === 'assistant' ? (
              <div key={m.id} className="ask-msg ask-bot">
                <Answer reply={m.reply} />
              </div>
            ) : (
              <div key={m.id} className="ask-msg ask-bot ask-error" role="alert">
                <p>
                  <CircleAlert size={15} aria-hidden="true" /> {m.text}
                </p>
                <button
                  type="button"
                  className="button ghost"
                  disabled={pending}
                  onClick={() => void ask(m.question)}
                >
                  Ask again
                </button>
              </div>
            ),
          )}
          {pending && (
            <div className="ask-msg ask-bot ask-working" aria-label="ADAPT is working">
              {steps.length === 0 ? (
                <p className="ask-step ask-step-live">Thinking</p>
              ) : (
                steps.map((s, i) => (
                  <p
                    key={s}
                    className={`ask-step ${i === steps.length - 1 ? 'ask-step-live' : 'ask-step-done'}`}
                  >
                    {s}
                  </p>
                ))
              )}
            </div>
          )}
        </div>
      )}

      <form className="ask-form" onSubmit={submit}>
        <label className="sr-only" htmlFor={inputId}>
          Ask ADAPT
        </label>
        <textarea
          id={inputId}
          ref={input}
          rows={1}
          value={draft}
          maxLength={2000}
          placeholder="Ask ADAPT anything…"
          onChange={(e) => {
            setDraft(e.target.value);
            resize();
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void ask(draft);
            }
          }}
        />
        {pending ? (
          <button
            type="button"
            className="ask-send"
            aria-label="Stop answering"
            onClick={() => abort.current?.abort()}
          >
            <Square size={14} aria-hidden="true" />
          </button>
        ) : (
          <button
            type="submit"
            className="ask-send"
            aria-label="Send question"
            disabled={!draft.trim()}
          >
            <ArrowUp size={18} aria-hidden="true" />
          </button>
        )}
      </form>
      <p className="ask-foot">
        Answers come from this workspace, and figures are checked against it. ADAPT can explain
        anything here but never approves or changes budgets.
      </p>
    </section>
  );
}
