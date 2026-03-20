use std::path::{Path, PathBuf};

#[derive(Debug, Clone)]
pub enum PathValidation {
    Allowed(PathBuf),
    OutsideRoot { resolved: PathBuf, roots: Vec<PathBuf> },
    Excluded { path: PathBuf, pattern: String },
}

#[derive(Debug, Default)]
pub struct PathValidator {
    roots: Vec<PathBuf>,
}

impl PathValidator {
    pub fn new(roots: Vec<PathBuf>) -> Self {
        Self { roots }
    }

    pub fn validate(&self, path: &Path) -> PathValidation {
        let resolved = path.to_path_buf();
        if self.roots.iter().any(|root| resolved.starts_with(root)) {
            PathValidation::Allowed(resolved)
        } else {
            PathValidation::OutsideRoot {
                resolved,
                roots: self.roots.clone(),
            }
        }
    }
}
