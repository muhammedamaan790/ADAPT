import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { dataMode } from '../api/client';
import { stage3 } from '../api/stage3';
import { Badge, Empty, ErrorState, Loading, SectionTitle } from './ui';

export function SavedImports() {
  const [selected, setSelected] = useState('');
  const list = useQuery({queryKey: ['saved-imports'], queryFn: stage3.imports, enabled: dataMode === 'api'});
  const detail = useQuery({queryKey: ['saved-import', selected], queryFn: () => stage3.importDetail(selected), enabled: !!selected});
  if (dataMode !== 'api') return null;
  return <section className="panel">
    <SectionTitle title="Saved upload workspaces"><Badge>ISOLATED FACTS</Badge></SectionTitle>
    <p className="section-description">Inspect persisted, typed records. These workspaces need additional sources before the engine can recommend or execute budgets.</p>
    {list.isPending ? <Loading label="Loading saved imports" /> : list.error ?
      <ErrorState error={list.error} retry={() => void list.refetch()} /> : !list.data?.length ?
      <Empty title="No saved imports">Confirm a valid CSV above to create a separate upload workspace.</Empty> : <>
        <label className="field compact-field">Upload workspace
          <select value={selected} onChange={e => setSelected(e.target.value)}>
            <option value="">Choose a confirmed import</option>
            {list.data.filter(row => row.status === 'IMPORTED').map(row =>
              <option value={row.import_id} key={row.import_id}>{row.type} · {row.row_count} rows · {row.import_id.slice(-8)}</option>)}
          </select>
        </label>
        {selected && (detail.isPending ? <Loading label="Reading uploaded records" /> : detail.error ?
          <ErrorState error={detail.error} retry={() => void detail.refetch()} /> : detail.data && <>
            <p className="notice">{detail.data.note}</p>
            <p className="caption">Missing decision dependencies: {detail.data.missing_sources.join(' · ')}.</p>
            <div className="table-scroll" tabIndex={0} role="region" aria-label="Saved normalized records">
              <table><caption>{detail.data.table} · first {detail.data.records.length} records{detail.data.truncated ? ' (preview truncated)' : ''}</caption>
                <thead><tr>{detail.data.columns.map(column => <th key={column}>{column.replaceAll('_', ' ')}</th>)}</tr></thead>
                <tbody>{detail.data.records.map((row, index) => <tr key={index}>{detail.data.columns.map(column =>
                  <td key={column}>{row[column] == null ? '—' : String(row[column])}</td>)}</tr>)}</tbody>
              </table>
            </div>
            {detail.data.cvr && <>
              <SectionTitle title="Conversion evidence" />
              <p className="section-description">{detail.data.cvr.note}</p>
              <div className="table-scroll" tabIndex={0} role="region" aria-label="SKU conversion posterior evidence">
                <table><caption>Observed clicks → purchases · 80% credible interval</caption>
                  <thead><tr><th>SKU / channel</th><th>Observed</th><th>Posterior CVR</th><th>P10–P90</th><th>Prior</th></tr></thead>
                  <tbody>{detail.data.cvr.rows.map(row => <tr key={`${row.sku}-${row.channel}`}>
                    <td>{row.sku} / {row.channel}</td><td>{row.purchases} / {row.clicks}</td>
                    <td>{(row.mean*100).toFixed(2)}%</td><td>{(row.p10*100).toFixed(2)}%–{(row.p90*100).toFixed(2)}%</td>
                    <td>{row.prior_source.replaceAll('_', ' ').toLowerCase()}</td>
                  </tr>)}</tbody>
                </table>
              </div>
            </>}
          </>)}
      </>}
  </section>;
}
