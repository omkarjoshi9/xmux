export interface WorkflowStep {
  name: string;
  depends_on?: string[];
  request: { schema_version: '1'; intent: string; objective: string; context_refs: string[] };
}
export type BoundedWorkflow = { steps: [WorkflowStep, WorkflowStep] | [WorkflowStep, WorkflowStep, WorkflowStep] };
