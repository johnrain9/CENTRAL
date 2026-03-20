use tool_executor::{ToolCallRequest, ToolContext, ToolExecutor, ToolOutcome};

pub async fn dispatch_tools(
    executor: &ToolExecutor,
    requests: Vec<ToolCallRequest>,
    ctx: &ToolContext,
) -> Vec<ToolOutcome> {
    let mut outcomes = Vec::new();
    for request in requests {
        let outcome = executor.execute(request, ctx).await;
        outcomes.push(outcome);
    }
    outcomes
}
