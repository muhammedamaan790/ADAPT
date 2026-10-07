export function money(value: number | null, compact = false): string {
  if (value === null || !Number.isFinite(value)) return '—';
  const sign = value < 0 ? '−' : '';
  const n = Math.abs(value);
  if (compact && n >= 10000000) return `${sign}₹${(n / 10000000).toFixed(2)} Cr`;
  if (compact && n >= 100000) return `${sign}₹${(n / 100000).toFixed(2)} L`;
  return `${sign}₹${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(n)}`;
}
export const signedMoney = (n: number) => `${n > 0 ? '+' : ''}${money(n)}`;
export const percent = (n: number) => `${(n * 100).toFixed(1)}%`;
export const humanStatus = (s: string) => s.toLowerCase().replaceAll('_', ' ');
export const dateTime = (s: string) =>
  new Intl.DateTimeFormat('en-IN', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(s));
