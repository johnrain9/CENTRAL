use super::types::TokenBudget;

pub fn allocate_summary_budget(budget: &mut TokenBudget, tokens: usize) -> bool {
    budget.try_allocate("summaries", tokens)
}

pub fn reserve_recent_turns(budget: &mut TokenBudget, tokens: usize) -> bool {
    budget.try_allocate("recent_turns", tokens)
}
