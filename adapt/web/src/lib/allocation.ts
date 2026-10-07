import type { AllocationInput, OptimizerContext } from '../api/workbench-contracts';

// Browser input checks only. Backend alone validates model, inventory and execution policy.
export function allocationErrors(context: OptimizerContext, input: AllocationInput): string[] {
  const errors: string[] = [];
  if (
    input.decision_id !== context.decision_id ||
    input.decision_hash !== context.decision_hash ||
    input.policy_version !== context.policy_version
  )
    errors.push('Proposal or policy changed. Reload before evaluating.');
  if (!context.supported_objectives.includes(input.objective))
    errors.push('This objective is not supported by the current engine.');
  const seen = new Set<string>();
  for (const leg of input.legs) {
    const c = context.campaigns.find((c) => c.budget_id === leg.budget_id);
    if (!c || seen.has(leg.budget_id)) {
      errors.push('Each budget unit must occur exactly once.');
      continue;
    }
    seen.add(leg.budget_id);
    if (!Number.isSafeInteger(leg.after) || leg.after < 0)
      errors.push(`${c.entity}: enter a nonnegative whole-rupee budget.`);
    if (leg.after < c.min_budget || leg.after > c.max_budget)
      errors.push(`${c.entity}: outside the allowed budget range.`);
    if (Math.abs(leg.after - c.before) > c.before * context.max_daily_change + 1e-7)
      errors.push(`${c.entity}: exceeds the daily change limit.`);
    if (c.inventory_gate === 'BLOCK' && leg.after > c.before)
      errors.push(`${c.entity}: scaling is blocked by inventory.`);
  }
  if (seen.size !== context.campaigns.length)
    errors.push('Every portfolio budget unit must be included.');
  if (input.legs.reduce((n, l) => n + l.after, 0) > context.budget_ceiling - context.reserve_floor)
    errors.push('Allocated spend exceeds the ceiling after the reserve floor.');
  return [...new Set(errors)];
}
