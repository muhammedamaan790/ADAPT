export type ImportType = 'ads' | 'orders' | 'inventory' | 'margins';
export const importFields: Record<ImportType, string[]> = {
  ads: ['date', 'budget_id', 'platform', 'spend', 'impressions', 'clicks'],
  orders: ['date', 'order_id', 'sku', 'quantity', 'net_revenue'],
  inventory: ['sku', 'on_hand', 'reserved', 'safety_stock'],
  margins: ['sku', 'price', 'unit_cost'],
};
export type Csv = { headers: string[]; rows: string[][] };
export function parseCsv(input: string): Csv {
  if (input.length > 2_000_000) throw new Error('CSV must be smaller than 2 MB.');
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  let closed = false;
  const text = input.replace(/^\uFEFF/, '');
  const push = () => {
    row.push(field);
    field = '';
    closed = false;
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
          closed = true;
        }
      } else field += c;
      continue;
    }
    if (c === '"') {
      if (field || closed) throw new Error('Unexpected quote in CSV field.');
      quoted = true;
    } else if (c === ',') {
      push();
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      push();
      if (row.some((v) => v.trim())) rows.push(row);
      row = [];
    } else {
      if (closed && !/\s/.test(c)) throw new Error('Unexpected text after quoted CSV field.');
      if (!closed) field += c;
    }
  }
  if (quoted) throw new Error('Unclosed quoted CSV field.');
  push();
  if (row.some((v) => v.trim())) rows.push(row);
  const headers = (rows.shift() || []).map((s) => s.trim());
  if (!headers.length || headers.some((s) => !s))
    throw new Error('CSV needs non-empty column headers.');
  if (new Set(headers).size !== headers.length)
    throw new Error('CSV contains duplicate column headers.');
  if (!rows.length) throw new Error('CSV needs at least one data row.');
  if (rows.length > 5000) throw new Error('This preview accepts up to 5,000 rows.');
  if (rows.some((r) => r.length !== headers.length))
    throw new Error('CSV rows must have the same number of columns as the header.');
  return { headers, rows };
}
export function validateImport(csv: Csv, type: ImportType, mapping: Record<string, string>) {
  const errors: string[] = [];
  const columns = importFields[type];
  if (columns.some((k) => !mapping[k] || !csv.headers.includes(mapping[k])))
    return { records: [], errors: ['Map every required field to a CSV column.'] };
  if (new Set(columns.map((k) => mapping[k])).size !== columns.length)
    return { records: [], errors: ['Each required field needs a distinct CSV column.'] };
  const seen = new Set<string>();
  const records = csv.rows.map((r, i) => {
    const record: Record<string, string | number> = {};
    columns.forEach((k) => {
      const raw = r[csv.headers.indexOf(mapping[k])].trim();
      if (['date', 'sku', 'budget_id', 'platform', 'order_id'].includes(k)) {
        record[k] = raw;
        if (!raw) errors.push(`Row ${i + 2}: ${k} is required.`);
      } else {
        const n = raw === '' ? NaN : Number(raw);
        record[k] = n;
        if (!Number.isFinite(n) || n < 0)
          errors.push(`Row ${i + 2}: ${k} must be a finite nonnegative number.`);
        if (
          ['impressions', 'clicks', 'on_hand', 'reserved', 'safety_stock', 'quantity'].includes(
            k,
          ) &&
          !Number.isSafeInteger(n)
        )
          errors.push(`Row ${i + 2}: ${k} must be a whole number.`);
      }
    });
    if (type === 'ads' || type === 'orders') {
      if (type === 'ads' && !['Meta', 'Google'].includes(String(record.platform)))
        errors.push(`Row ${i + 2}: platform must be Meta or Google.`);
      const date = String(record.date);
      if (
        !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
        !Number.isFinite(Date.parse(date)) ||
        new Date(date).toISOString().slice(0, 10) !== date
      )
        errors.push(`Row ${i + 2}: date must be a valid YYYY-MM-DD date.`);
      if (Number(record.clicks) > Number(record.impressions))
        errors.push(`Row ${i + 2}: clicks exceed impressions.`);
    }
    if (type === 'inventory' && Number(record.reserved) > Number(record.on_hand))
      errors.push(`Row ${i + 2}: reserved units exceed on-hand stock.`);
    const key =
      type === 'ads'
        ? `${record.date}|${record.platform}|${record.budget_id}`
        : type === 'orders'
          ? `${record.order_id}|${record.sku}`
          : String(record.sku);
    if (seen.has(key)) errors.push(`Row ${i + 2}: duplicate business key ${key}.`);
    seen.add(key);
    return record;
  });
  return { records, errors: [...new Set(errors)] };
}
export function downloadText(name: string, text: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
