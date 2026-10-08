// One illustrative scenario drives every visual on the product page. Derived values are computed
// here, not typed in, so the charts stay arithmetically consistent with each other.

/** Meta Prospecting ROAS for the 14 days ending 7 Oct. The change point is index 8 (2 Oct). */
export const roasSeries = [
  4.08, 4.15, 4.1, 4.18, 4.12, 4.09, 4.14, 4.12, 3.86, 3.55, 3.31, 3.12, 2.98, 2.91,
];
export const roasDates = [
  'Sep 24',
  'Sep 25',
  'Sep 26',
  'Sep 27',
  'Sep 28',
  'Sep 29',
  'Sep 30',
  'Oct 1',
  'Oct 2',
  'Oct 3',
  'Oct 4',
  'Oct 5',
  'Oct 6',
  'Oct 7',
];
export const changePoint = 8;
export const roasBefore = roasSeries[changePoint - 1];
export const roasAfter = roasSeries[roasSeries.length - 1];
export const roasChange = roasAfter / roasBefore - 1; // −29.4%

/** Expected range from the eight days before the change point: mean ± 2σ. */
const baseline = roasSeries.slice(0, changePoint);
const mean = baseline.reduce((a, b) => a + b, 0) / baseline.length;
const sd = Math.sqrt(baseline.reduce((a, b) => a + (b - mean) ** 2, 0) / (baseline.length - 1));
export const expectedBand = [mean - 2 * sd, mean + 2 * sd] as const;

/**
 * ROAS = 1000 · CTR · CVR · AOV / CPM, so the ratio of new to old ROAS is the product of the
 * driver ratios (CPM inverted). Each driver's share of the change is its share of the log ratio.
 */
export const drivers = [
  {
    key: 'CPM',
    label: 'Cost per 1,000 impressions',
    before: 186.26,
    after: 192.22,
    unit: 'inr',
    harmWhen: 'up',
  },
  {
    key: 'CTR',
    label: 'Click-through rate',
    before: 0.0162,
    after: 0.01649,
    unit: 'pct',
    harmWhen: 'down',
  },
  {
    key: 'CVR',
    label: 'Conversion rate',
    before: 0.0258,
    after: 0.0186,
    unit: 'pct',
    harmWhen: 'down',
  },
  {
    key: 'AOV',
    label: 'Average order value',
    before: 1836,
    after: 1823.1,
    unit: 'inr',
    harmWhen: 'down',
  },
] as const;

const logRatio = (d: (typeof drivers)[number]) =>
  d.key === 'CPM' ? -Math.log(d.after / d.before) : Math.log(d.after / d.before);
const totalLog = drivers.reduce((sum, d) => sum + logRatio(d), 0);

export const bridge = drivers.map((d) => ({
  key: d.key,
  change: d.after / d.before - 1,
  /** ROAS points this driver moved, allocated by log share of the total change. */
  impact: ((roasAfter - roasBefore) * logRatio(d)) / totalLog,
  share: logRatio(d) / totalLog,
}));
export const primary = bridge.find((b) => b.key === 'CVR')!;

/** The SKU that Meta Prospecting traffic lands on. */
export const sku = {
  name: 'Classic Oversized Tee',
  code: 'TEE-OS-CLS',
  price: 1299,
  landingShare: 0.71,
  marginBefore: 0.36,
  marginAfter: 0.28,
  coverDays: 6.2,
  restockDays: 14,
  sizes: [
    { size: 'XS', inStock: true },
    { size: 'S', inStock: true },
    { size: 'M', inStock: false },
    { size: 'L', inStock: false },
    { size: 'XL', inStock: true },
  ],
};
export const marginPct = (m: number) => `${Math.round(m * 100)}%`;
/** Revenue Meta Prospecting would have made at its pre-change ROAS, on unchanged ₹80,000/day spend. */
export const prospectingDaily = 80000;
export const lostRevenue = roasSeries
  .slice(changePoint)
  .reduce((sum, r) => sum + (roasBefore - r) * prospectingDaily, 0);
export const cogsIncrease = sku.price * (sku.marginBefore - sku.marginAfter); // ₹104 per unit

/** The proposal. Total daily budget ₹2,25,000; an ₹18,000 move is 8 points of share. */
export const totalDaily = 225000;
export const shift = 18000;
export const proposal = {
  id: 'DC-0412',
  hash: '7f3a9e…c21b',
  from: { name: 'Meta Prospecting', daily: 80000 },
  to: { name: 'Google PMax', daily: 42000 },
  weeklyUplift: 41200,
  interval: [29800, 52600] as const,
  confidence: 0.91,
  checks: [
    { name: 'Budget ceiling & reserve', detail: 'Total unchanged at ₹2,25,000' },
    { name: 'Daily change limit', detail: '8% of budget, limit 10%' },
    { name: 'Inventory exposure', detail: 'PMax lands on 30+ day cover' },
    { name: 'Required data health', detail: 'All 5 sources reconciled' },
  ],
};

export const channels = [
  { key: 'meta', name: 'Meta', before: 0.48, after: 0.4 },
  { key: 'google', name: 'Google', before: 0.27, after: 0.35 },
  { key: 'tiktok', name: 'TikTok', before: 0.18, after: 0.18 },
  { key: 'other', name: 'Other', before: 0.07, after: 0.07 },
] as const;

/** Contribution per ₹100 of spend = ROAS · 100 · margin − 100. Break-even ROAS = 1 / margin. */
export const campaigns = [
  { key: 'A', name: 'Campaign A', product: 'Graphic Tee drop', roas: 4.6, margin: 0.11, cover: 4 },
  {
    key: 'B',
    name: 'Campaign B',
    product: 'Linen Shirt range',
    roas: 3.7,
    margin: 0.42,
    cover: 29,
  },
].map((c) => ({ ...c, breakEven: 1 / c.margin, per100: c.roas * 100 * c.margin - 100 }));

/** Seven days after approval: cumulative contribution change, forecast vs measured. */
export const outcomeActual = [4100, 9800, 15200, 20900, 26300, 31800, 37500];
export const outcomeForecast = proposal.weeklyUplift;
export const responseFactor = outcomeActual[6] / outcomeForecast; // 0.91

/** Six creatives over 21 days. Lane rules are shown on the page next to the board. */
export type Lane = 'Winner' | 'Stable' | 'Fatiguing' | 'Underperforming';
export const lanes: Lane[] = ['Winner', 'Stable', 'Fatiguing', 'Underperforming'];

type CreativeDef = {
  id: string;
  name: string;
  format: string;
  art: 'reel' | 'flatlay' | 'price' | 'quote' | 'unbox' | 'colours';
  breakEven: number;
  ctr: (d: number) => number;
  roas: (d: number) => number;
  freq: (d: number) => number;
  cvr: (d: number) => number;
  cac: (d: number) => number;
};
const ease = (d: number, from: number, to: number, start: number, end: number) => {
  const t = Math.min(1, Math.max(0, (d - start) / (end - start)));
  return from + (to - from) * (t * t * (3 - 2 * t));
};
export const creatives: CreativeDef[] = [
  {
    id: 'c1',
    name: 'Try-on reel',
    format: 'Oversized Tee · 15s video',
    art: 'reel',
    breakEven: 2.4,
    ctr: (d) => ease(d, 2.4, 1.55, 8, 18),
    roas: (d) => ease(d, 4.4, 2.9, 9, 19),
    freq: (d) => 1.2 + d * 0.14,
    cvr: (d) => ease(d, 2.9, 2.5, 9, 19),
    cac: (d) => ease(d, 520, 760, 9, 19),
  },
  {
    id: 'c2',
    name: 'Linen flat lay',
    format: 'Linen Shirt · static',
    art: 'flatlay',
    breakEven: 2.4,
    ctr: (d) => ease(d, 1.6, 1.8, 2, 12),
    roas: (d) => ease(d, 3.5, 4.3, 4, 11),
    freq: (d) => 1.1 + d * 0.05,
    cvr: (d) => ease(d, 2.4, 2.8, 4, 11),
    cac: (d) => ease(d, 640, 540, 4, 11),
  },
  {
    id: 'c3',
    name: 'Two-for price card',
    format: 'Tees · static',
    art: 'price',
    breakEven: 2.4,
    ctr: () => 1.9,
    roas: (d) => ease(d, 1.9, 2.05, 1, 21),
    freq: (d) => 1.4 + d * 0.06,
    cvr: () => 1.5,
    cac: (d) => ease(d, 980, 940, 1, 21),
  },
  {
    id: 'c4',
    name: 'Fit review quote',
    format: 'Cargo Pants · static',
    art: 'quote',
    breakEven: 2.4,
    ctr: () => 1.3,
    roas: (d) => ease(d, 3.2, 3.3, 1, 21),
    freq: (d) => 1.3 + d * 0.04,
    cvr: () => 2.6,
    cac: () => 610,
  },
  {
    id: 'c5',
    name: 'Hoodie unboxing',
    format: 'Hoodie · UGC video',
    art: 'unbox',
    breakEven: 2.4,
    ctr: (d) => ease(d, 2.1, 2.0, 1, 21),
    roas: (d) => ease(d, 4.6, 4.5, 1, 21),
    freq: (d) => 1.1 + d * 0.06,
    cvr: () => 3.1,
    cac: () => 480,
  },
  {
    id: 'c6',
    name: 'Six-colour drop',
    format: 'Tees · carousel',
    art: 'colours',
    breakEven: 2.4,
    ctr: (d) => ease(d, 1.5, 0.9, 1, 17),
    roas: (d) => ease(d, 3.0, 2.1, 6, 17),
    freq: (d) => 3.1 + d * 0.09,
    cvr: (d) => ease(d, 2.2, 1.7, 6, 17),
    cac: (d) => ease(d, 720, 1010, 6, 17),
  },
];

export const TARGET_ROAS = 3.4;
export function classify(c: CreativeDef, d: number): Lane {
  const peak = Math.max(...Array.from({ length: d }, (_, i) => c.ctr(i + 1)));
  const ctrDrop = 1 - c.ctr(d) / peak;
  if (c.roas(d) < c.breakEven) return 'Underperforming';
  if (ctrDrop >= 0.2 && c.freq(d) >= 3) return 'Fatiguing';
  if (c.roas(d) >= 1.25 * TARGET_ROAS) return 'Winner';
  return 'Stable';
}
/** Fatigue 0–1: CTR loss from peak, weighted by how far frequency is past 2.5. */
export function fatigue(c: CreativeDef, d: number) {
  const peak = Math.max(...Array.from({ length: d }, (_, i) => c.ctr(i + 1)));
  const drop = 1 - c.ctr(d) / peak;
  return Math.min(1, Math.max(0, drop * 2.2 + Math.max(0, c.freq(d) - 2.5) * 0.12));
}

export const inr = (n: number) =>
  `${n < 0 ? '−' : ''}₹${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Math.abs(n))}`;
export const signedInr = (n: number) => `${n > 0 ? '+' : n < 0 ? '' : ''}${inr(n)}`;
export const pct = (n: number, digits = 1) =>
  `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n * 100).toFixed(digits)}%`;
