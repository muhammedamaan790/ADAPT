import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { api } from '../api/client';

export function useOverview() {
  return useQuery({ queryKey: ['overview'], queryFn: api.overview, refetchInterval: 5000 });
}
export function useDecisions() {
  return useQuery({ queryKey: ['decisions'], queryFn: api.decisions, refetchInterval: 1500 });
}
export function useExecutions() {
  return useQuery({ queryKey: ['executions'], queryFn: api.executions, refetchInterval: 1500 });
}
export function useOutcomes() {
  return useQuery({ queryKey: ['outcomes'], queryFn: api.outcomes, refetchInterval: 5000 });
}
export function useEvents() {
  return useQuery({ queryKey: ['events'], queryFn: api.events, refetchInterval: 3000 });
}
export function useAnomalies() {
  return useQuery({ queryKey: ['anomalies'], queryFn: api.anomalies, refetchInterval: 5000 });
}
export function useLedger() {
  return useQuery({ queryKey: ['ledger'], queryFn: api.ledger, refetchInterval: 1500 });
}
export function useAction<T, R>(fn: (value: T) => Promise<R>) {
  const cache = useQueryClient();
  return useMutation<R, Error, T>({
    mutationFn: fn,
    onSuccess: () => cache.invalidateQueries(),
    onError: () => cache.invalidateQueries(),
  });
}
