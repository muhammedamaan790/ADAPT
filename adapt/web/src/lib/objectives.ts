import type { Objective } from '../api/contracts';
export const objectives: Record<Objective, { label: string; description: string }> = {
  PROFIT: {
    label: 'Profit',
    description: 'Maximize contribution after advertising, adjusted for downside risk.',
  },
  GROWTH: {
    label: 'Growth',
    description:
      'Maximize net revenue while retaining the backend contribution floor against the absolute current baseline.',
  },
  ACQUISITION: {
    label: 'Acquisition',
    description:
      'Maximize new customers subject to the backend contribution floor and attribution coverage.',
  },
  INVENTORY_CLEARANCE: {
    label: 'Inventory clearance',
    description:
      'Value contribution plus holding-cost savings on excess stock, within inventory guardrails.',
  },
  MARGIN_PROTECTION: {
    label: 'Margin protection',
    description: 'Apply stronger downside protection and a minimum blended contribution margin.',
  },
  BALANCED: {
    label: 'Balanced',
    description:
      'Balance contribution and downside risk while preserving net revenue against the current baseline.',
  },
};
