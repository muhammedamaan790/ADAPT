import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { askCopilot } from '../api/insights';
import type { CopilotReply } from '../api/insight-contracts';
import { dataMode } from '../api/client';
import { Badge, InlineError, Modal } from './ui';

export function Copilot({ close }: { close: () => void }) {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState<{ question: string; reply: CopilotReply }[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function send(value: string) {
    if (pending) return;
    setError(null);
    setPending(true);
    controller.current = new AbortController();
    try {
      const reply = await askCopilot(value, controller.current.signal);
      setMessages((m) => [...m.slice(-9), { question: value, reply }]);
      setQuestion('');
    } catch (e) {
      if (e instanceof Error && e.name === 'AbortError')
        setError(new Error('Request cancelled. No answer was accepted.'));
      else setError(e instanceof Error ? e : new Error('Copilot unavailable.'));
    } finally {
      setPending(false);
    }
  }
  return (
    <Modal title="Workspace Copilot" close={close}>
      <div className="badge-row">
        <Badge tone={dataMode === 'fixture' ? 'warning' : 'accent'}>
          {dataMode === 'fixture' ? 'GROUNDED TEMPLATES' : 'BACKEND COPILOT'}
        </Badge>
        <span className="caption">Read-only assistance</span>
      </div>
      <p className="workbench-copy">
        {dataMode === 'fixture'
          ? 'Deterministic answers reference visible fixture records. No LLM or SQL engine is connected.'
          : 'Answers arrive through the backend event stream with validated evidence links.'}{' '}
        Copilot cannot approve, execute or alter budgets.
      </p>
      <div className="copilot-prompts">
        {['Why this allocation?', 'What about inventory risk?', 'What feedback has matured?'].map(
          (q) => (
            <button
              key={q}
              className="button secondary"
              disabled={pending}
              onClick={() => void send(q)}
            >
              {q}
            </button>
          ),
        )}
      </div>
      <div
        className="copilot-transcript"
        role="log"
        aria-live="polite"
        aria-label="Copilot conversation"
      >
        {messages.length ? (
          messages.map((m, i) => (
            <article key={i}>
              <h3>{m.question}</h3>
              <p>{m.reply.text}</p>
              <div className="badge-row">
                <Badge>{m.reply.mode === 'TEMPLATE' ? 'Fixture template' : 'LLM answer'}</Badge>
                {m.reply.evidence.map((e) => (
                  <Link key={e.href} className="text-link" to={e.href} onClick={close}>
                    {e.label}
                  </Link>
                ))}
              </div>
            </article>
          ))
        ) : (
          <p className="caption">
            Ask about the current proposal, inventory gates or outcome feedback.
          </p>
        )}
        {pending && <p role="status">Waiting for a complete, validated answer…</p>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(question);
        }}
      >
        <label className="field">
          Ask Copilot
          <textarea
            rows={2}
            maxLength={2000}
            value={question}
            disabled={pending}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask about your workspace evidence"
          />
        </label>
        <InlineError error={error} />
        <div className="modal-actions">
          <button
            className="button secondary"
            type="button"
            disabled={pending}
            onClick={() => setMessages([])}
          >
            Clear conversation
          </button>
          {pending ? (
            <button
              className="button secondary"
              type="button"
              onClick={() => controller.current?.abort()}
            >
              Cancel request
            </button>
          ) : (
            <button className="button primary" type="submit" disabled={question.trim().length < 3}>
              Ask question
            </button>
          )}
        </div>
      </form>
    </Modal>
  );
}
