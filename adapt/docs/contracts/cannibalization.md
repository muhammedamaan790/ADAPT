# Contract: cannibalization (Stage 2, spec §8.1 step 2)

Module: `backend/adapt/economics/cannibalization.py`; config `config/cannibalization.yaml` (`enabled: false` = the
Stage 1 switch, T = 0). Used by `portfolio_economics` and the optimizer's fast evaluator (both call `book()`).

## ALGORITHM
- C[i,j] for budget units sharing a category (product set): 0.30 same channel + stage, 0.15 otherwise; symmetric,
  zero diagonal. **T = αC with one global α = min(1, 0.5 / max row sum)** (row sums ≤ 0.5, exactly symmetric).
- Per joint draw (P = max(ΔR, 0), N = max(−ΔR, 0), R′ = post-change horizon revenue): raw steal S_raw[i←j] = T_ij P_i;
  scale_j = min(1, max(R′_j, 0) / Σ_i S_raw[i←j]); realized F = S_raw·scale_j; shortfall_i = Σ_j (S_raw − F)[i←j];
  recapture G[i←j] = T_ij N_j; **booked_i = ΔR_i − shortfall_i + Σ_j G[i←j] − Σ_k F[k←i]**. Vectorised with matrix
  products (O(n U²)); booked revenue lands in the receiving unit's SKU mix.
- Unit metadata: `UnitState.categories` (product sets) and `stage` (search / video / prospecting / retargeting from
  the campaign name), filled by `load_state`; snapshotted for replay. The generated T is an EXACT staleness input
  (`inputs_manifest()["cannibalization"]`).

## INVARIANTS (tests)
(a) Σ booked = Σ ΔR − Σ S_raw + Σ G whether or not a cap binds · (b) shortfall + Σ F = Σ S_raw, 0 ≤ F ≤ S_raw ·
(c) Σ_i F[i←j] ≤ max(R′_j, 0) · (d) net new demand ≥ 0 · (e) T as generated is symmetric, zero diagonal, row sums
≤ 0.5, = αC (T59). T54: A −10K / B +10K / R_A(s′) = 3K → booked −13K / +13K (total 0); the audit's −100/+100/10 case →
−110/+110; two thieves into caps 30 and 50 → realized 80, shortfall 20, total cap-independent. T49 three units into
one. Third-party overlap: net = (Σ_j T_Aj − Σ_j T_Bj)·x. Pure scale-up: more overlap never raises Δnet revenue.
Fast evaluation equals `portfolio_economics` with T ≠ 0. Hypothesis: 200 random cases of (a)–(e).
`backend/tests/test_cannibalization.py`.
