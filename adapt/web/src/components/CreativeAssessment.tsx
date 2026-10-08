import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { creativeFields, stage3, type CreativeField } from '../api/stage3';
import { Badge, ErrorState, InlineError, Loading, SectionTitle } from './ui';

const label = (field: CreativeField) => (field === 'cta' ? 'Call to action' : field);

export function CreativeAssessment() {
  const domains = useQuery({ queryKey: ['creative-attributes'], queryFn: stage3.attributes });
  const [attributes, setAttributes] = useState<Partial<Record<CreativeField, string>>>({});
  const score = useMutation({ mutationFn: stage3.scoreAttributes });
  const chosen = Object.fromEntries(Object.entries(attributes).filter(([, v]) => v));
  return (
    <section className="panel">
      <SectionTitle title="Creative assessment">
        <Badge>STRUCTURED PRIOR</Badge>
      </SectionTitle>
      <p className="section-description">
        Rank early click-through from structured creative attributes. Copy and images are not
        analysed; unset attributes take their most common trained value.
      </p>
      {domains.isPending ? (
        <Loading label="Loading trained attribute levels" />
      ) : domains.error ? (
        <ErrorState error={domains.error} retry={() => void domains.refetch()} />
      ) : domains.data.status !== 'AVAILABLE' ? (
        <p className="notice">{domains.data.note}</p>
      ) : (
        <>
          <fieldset disabled={score.isPending} className="creative-attributes">
            <legend className="sr-only">Structured creative attributes</legend>
            {creativeFields.map((field) => (
              <label className="field" key={field}>
                {label(field)}
                <select
                  value={attributes[field] ?? ''}
                  onChange={(e) => {
                    setAttributes((old) => ({ ...old, [field]: e.target.value }));
                    score.reset();
                  }}
                >
                  <option value="">Most common</option>
                  {(domains.data.domains[field] ?? []).map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
            ))}
          </fieldset>
          <button
            className="button secondary"
            disabled={score.isPending || !Object.keys(chosen).length}
            onClick={() => score.mutate(chosen)}
          >
            {score.isPending ? 'Assessing…' : 'Assess creative'}
          </button>
          <InlineError error={score.error} />
          {score.data && (
            <p className="notice" role="status">
              {score.data.score === null
                ? 'Not estimable'
                : `Higher than ${Math.round(score.data.score * 100)}% of existing creatives`}
              . {score.data.explanation}
            </p>
          )}
        </>
      )}
    </section>
  );
}
