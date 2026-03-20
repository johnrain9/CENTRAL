use crate::agentic_loop::state::AgenticLoopState;
use crate::agentic_loop::types::{LoopDependencies, LoopError, TurnOutcome};

pub async fn execute_turn(
    deps: &LoopDependencies,
    state: &mut AgenticLoopState,
) -> Result<TurnOutcome, LoopError> {
    let context = deps
        .context_manager
        .assemble(&state.session_id)
        .map_err(|err| LoopError::ContextAssembly(err.to_string()))?;
    let _ = context;
    Ok(TurnOutcome::Complete)
}
