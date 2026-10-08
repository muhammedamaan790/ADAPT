import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { insights } from '../api/insights';
import { stage2 } from '../api/stage2';
import { importFields, parseCsv, validateImport, type Csv, type ImportType } from '../lib/csv';
import { InlineError, Modal } from './ui';

const typeLabels: Record<ImportType, string> = {
  ads: 'Ad spend (date, budget, platform, spend, impressions, clicks)',
  orders: 'Orders (date, order id, sku, quantity, net revenue)',
  margins: 'SKU margins (sku, price, unit cost)',
  inventory: 'Inventory (sku, on hand, reserved, safety stock)',
};

/** Upload one CSV into the active workspace: parse, map columns (backend suggestion), validate, import. */
export function UploadDialog({ close }: { close: () => void }) {
  const queryClient = useQueryClient();
  const [type, setType] = useState<ImportType>('ads');
  const [csv, setCsv] = useState<Csv | null>(null);
  const [fileName, setFileName] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [parseError, setParseError] = useState('');
  const [done, setDone] = useState('');

  const suggest = async (t: ImportType, parsed: Csv) => {
    const exact = Object.fromEntries(
      importFields[t].map((f) => [f, parsed.headers.find((h) => h.toLowerCase() === f) ?? '']),
    );
    try {
      const s = await stage2.suggestMapping(t, parsed.headers);
      if (s)
        for (const [f, v] of Object.entries(s.mapping))
          if (v.header && !exact[f]) exact[f] = v.header;
    } catch {
      // the exact-name mapping stands; the person can still map columns by hand
    }
    setMapping(exact);
  };
  const pick = async (file: File | undefined) => {
    setDone('');
    setParseError('');
    setCsv(null);
    if (!file) return;
    setFileName(file.name);
    try {
      const parsed = parseCsv(await file.text());
      setCsv(parsed);
      await suggest(type, parsed);
    } catch (e) {
      setParseError(e instanceof Error ? e.message : 'Could not read this CSV.');
    }
  };
  const checked = csv ? validateImport(csv, type, mapping) : null;
  const run = useMutation({
    mutationFn: () => insights.importData(type, checked!.records, mapping),
    onSuccess: (ack) => {
      setDone(ack.message);
      setCsv(null);
      setFileName('');
      void queryClient.invalidateQueries();
    },
  });

  return (
    <Modal title="Upload data" close={close}>
      <div className="upload-form">
        <label>
          What is in the file?
          <select
            value={type}
            onChange={(e) => {
              const t = e.target.value as ImportType;
              setType(t);
              setDone('');
              if (csv) void suggest(t, csv);
            }}
          >
            {(Object.keys(typeLabels) as ImportType[]).map((t) => (
              <option key={t} value={t}>
                {typeLabels[t]}
              </option>
            ))}
          </select>
        </label>
        <label>
          CSV file
          <input
            type="file"
            accept=".csv,text/csv"
            key={fileName || 'empty'}
            onChange={(e) => void pick(e.target.files?.[0])}
          />
        </label>
        <p className="muted upload-hint">
          Sample files: <code>adapt/demo_data/</code> (ads.csv, orders.csv, margins.csv,
          inventory.csv). Re-uploading a row with the same key replaces it.
        </p>
        {parseError && <p className="text-danger">{parseError}</p>}
        {csv && (
          <>
            <p className="muted">
              {fileName}: {csv.rows.length.toLocaleString('en-IN')} rows. Match each field to a
              column.
            </p>
            <div className="upload-mapping">
              {importFields[type].map((f) => (
                <label key={f}>
                  <span>{f}</span>
                  <select
                    value={mapping[f] ?? ''}
                    onChange={(e) => setMapping({ ...mapping, [f]: e.target.value })}
                  >
                    <option value="">Choose column…</option>
                    {csv.headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            {checked && checked.errors.length > 0 && (
              <ul className="upload-errors text-danger">
                {checked.errors.slice(0, 6).map((e) => (
                  <li key={e}>{e}</li>
                ))}
                {checked.errors.length > 6 && <li>…and {checked.errors.length - 6} more</li>}
              </ul>
            )}
            <button
              className="live-button upload-go"
              disabled={!checked || checked.errors.length > 0 || run.isPending}
              onClick={() => run.mutate()}
            >
              {run.isPending
                ? 'Importing…'
                : `Import ${csv.rows.length.toLocaleString('en-IN')} rows`}
            </button>
          </>
        )}
        {done && (
          <p className="upload-done" role="status">
            {done}
          </p>
        )}
        <InlineError error={run.error as Error | null} />
      </div>
    </Modal>
  );
}
