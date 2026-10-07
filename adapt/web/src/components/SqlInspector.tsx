import { useState, useEffect, useRef } from 'react';
import { dataMode } from '../api/client';
import { completion } from '../api/completion';
import type { z } from 'zod';
import type { sqlResultSchema } from '../api/completion-contracts';
import { InlineError } from './ui';
import { dateTime } from '../lib/format';

export function SqlInspector() {
  const [query, setQuery] = useState('SELECT * FROM marts.recon_daily LIMIT 50');
  const [result, setResult] = useState<z.infer<typeof sqlResultSchema> | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (pending) return;
        setPending(true);
        setError(null);
        setResult(null);
        controller.current = new AbortController();
        try {
          setResult(await completion.sql(query, controller.current.signal));
        } catch (e) {
          setError(e instanceof Error ? e : new Error('Query service unavailable.'));
        } finally {
          setPending(false);
        }
      }}
    >
      <p className="workbench-copy">
        Inspect workspace reporting tables using a read-only query. Results are limited to 500 rows.
        The query service determines which tables and statements are permitted.
      </p>
      {dataMode === 'fixture' && (
        <p className="notice">No SQL engine is connected in fixture mode.</p>
      )}
      <label className="field">
        Read-only SQL
        <textarea
          rows={4}
          maxLength={4000}
          value={query}
          disabled={pending || dataMode === 'fixture'}
          onChange={(e) => {
            setQuery(e.target.value);
            setResult(null);
            setError(null);
          }}
          spellCheck={false}
        />
      </label>
      <InlineError error={error} />
      <div className="modal-actions">
        <button
          className="button primary"
          disabled={dataMode === 'fixture' || pending || query.trim().length < 3}
        >
          {pending ? 'Querying…' : 'Run read-only query'}
        </button>
      </div>
      {result && (
        <div aria-live="polite">
          <p className="caption">
            {result.rows.length} rows · {result.elapsed_ms} ms · {dateTime(result.as_of)}
            {result.truncated ? ' · Results truncated' : ''}
          </p>
          {result.rows.length === 0 ? (
            <p>No matching rows. Try a different filter.</p>
          ) : (
            <div
              className="table-scroll sql-results"
              tabIndex={0}
              role="region"
              aria-label="SQL results; scroll to inspect all rows and columns"
            >
              <table>
                <caption className="sr-only">SQL query results</caption>
                <thead>
                  <tr>
                    {result.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => (
                    <tr key={i}>
                      {r.map((c, j) => (
                        <td key={j}>{c === null ? 'NULL' : String(c)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </form>
  );
}
