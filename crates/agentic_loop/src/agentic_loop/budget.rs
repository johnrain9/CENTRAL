#[derive(Debug, Default)]
pub struct BudgetTracker {
    chain_cost: f64,
}

impl BudgetTracker {
    pub fn new() -> Self {
        Self { chain_cost: 0.0 }
    }

    pub fn add_cost(&mut self, amount: f64) {
        self.chain_cost += amount;
    }

    pub fn chain_cost(&self) -> f64 {
        self.chain_cost
    }
}
